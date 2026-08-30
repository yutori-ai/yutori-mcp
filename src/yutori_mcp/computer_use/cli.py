from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.request import urlopen

from ..schemas import (
    COMPUTER_USE_DEFAULT_MAX_STEPS,
    COMPUTER_USE_DEFAULT_MINUTES,
    COMPUTER_USE_MAX_MINUTES,
    ComputerUseTaskInput,
)
from .constants import DRIVER_INSTALLER_SHA256, DRIVER_VERSION
from .lock import ComputerUseBusyError, DesktopLock
from .preflight import (
    blocker_message,
    check_driver_binary,
    check_runtime,
    child_search_path,
    find_cua_driver,
    first_blocker,
    run_checks,
)
from .result import format_action_line, format_result
from .supervisor import run_task


def _doctor() -> int:
    results = run_checks()
    for result in results:
        label = "PASS"
        if not result.ok:
            label = "BLOCKED" if result.blocking else "WARNING"
        print(f"{label} {result.name}: {result.detail}")
        if result.remediation:
            print(f"  Fix: {result.remediation}")
    return 0 if all(result.ok or not result.blocking for result in results) else 1


def _download_installer(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def _setup() -> int:
    runtime = check_runtime()
    if not runtime.ok:
        print(runtime.remediation)
        return 1
    version = DRIVER_VERSION
    installer = _download_installer(
        f"https://github.com/trycua/cua/releases/download/cua-driver-rs-v{version}/install.sh"
    )
    if hashlib.sha256(installer).hexdigest() != DRIVER_INSTALLER_SHA256:
        print("Driver installer checksum mismatch; nothing was executed.")
        return 1
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "install.sh"
        path.write_bytes(installer)
        path.chmod(0o700)
        env = {
            "PATH": child_search_path(),
            "HOME": str(Path.home()),
            "CUA_DRIVER_RS_VERSION": version,
        }
        subprocess.run([str(path)], env=env, check=True)
    subprocess.run(["open", "-n", "-g", "-a", "CuaDriver", "--args", "serve"], check=True)
    # Resolved after the installer runs, and by absolute path: a Dock-launched MCP client's PATH
    # does not include Homebrew, so the bare name would not be found here.
    driver = find_cua_driver()
    if driver is None:
        print(check_driver_binary().remediation)
        return 1
    subprocess.run([str(driver), "permissions", "grant"], check=True)
    from yutori.navigator.macos import (
        MacOSOverlayPreparationError,
        prepare_macos_overlay,
    )

    try:
        prepared = prepare_macos_overlay()
        print(f"Prepared reasoning overlay: {prepared.binary}")
    except (MacOSOverlayPreparationError, OSError) as error:
        print(f"WARNING reasoning overlay unavailable: {error}")
    return _doctor()


def _blocked() -> bool:
    """Print and return True if a blocking preflight check fails; False if ready to run."""
    blocker = first_blocker()
    if blocker is None:
        return False
    print(blocker_message(blocker))
    return True


async def _mechanical_calculator_check() -> str:
    from yutori.navigator.macos import MacOSComputer
    from yutori.navigator.macos.transport import CuaDriverTransport

    from .app import prepare_app, structured_content

    driver = find_cua_driver()
    if driver is None:
        raise RuntimeError(check_driver_binary().remediation)
    transport = CuaDriverTransport(binary=driver)
    sentinel = f"yutori-smoke-{uuid.uuid4().hex[:12]}"
    async with MacOSComputer(
        transport=transport,
        owns_transport=True,
        presentation=False,
        show_stop_button=False,
    ) as computer:
        await prepare_app(computer, "Calculator", None)
        await computer._call_tool(
            "clipboard_write",
            {"session": computer.session, "text": sentinel},
        )
        await computer.keypress("ESC")
        await computer.wait(300)
        await computer.type("6*7=")
        await computer.wait(500)
        # Exact clipboard equality rejects a stale result; retries avoid racing Calculator's display update.
        copied = ""
        for _attempt in range(3):
            await computer.keypress(["CMD", "C"])
            await computer.wait(700)
            result = await computer._call_tool(
                "clipboard_read",
                {"session": computer.session, "include_text": True},
                read_only=True,
            )
            copied = str(structured_content(result).get("text") or "").strip()
            if copied == "42":
                break
            await computer.wait(500)
    return copied


async def _smoke_live() -> int:
    from ..adapter import resolve_run_credentials

    try:
        with DesktopLock() as lock:
            if _blocked():
                return 1

            try:
                copied = await _mechanical_calculator_check()
            # Every driver and computer failure lands here: CuaDriverError and
            # MacOSComputerError, and so every subclass prepare_app and the transport raise,
            # derive from RuntimeError. What is left outside this tuple is a bug in this file,
            # which should surface as a traceback rather than a setup-blocker message.
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                print(f"Mechanical Calculator check failed through CuaDriver. Detail: {error}")
                return 1
            if copied != "42":
                print("Mechanical Calculator check failed through CuaDriver: clipboard result did not match '42'.")
                return 1
            api_key, api_base_url = resolve_run_credentials()
            result = await run_task(
                task="In Calculator, clear the display, compute 9 * 9, and report the result.",
                app="Calculator",
                start_url=None,
                minutes=2,
                max_steps=10,
                api_key=api_key,
                api_base_url=api_base_url,
                lock=lock,
            )
    except ComputerUseBusyError as error:
        print(error)
        return 1
    print(format_result(result))
    return 0 if result.get("outcome") == "completed" else 1


async def _print_event(event: dict) -> None:
    if event.get("type") == "ready":
        print("runner ready; driving the desktop")
        return
    line = format_action_line(event)
    if event.get("elapsed_ms") is not None:
        line += f" [at {event['elapsed_ms']} ms]"
    if event.get("command"):
        line += f"\n  $ {event['command']}"
    print(line, flush=True)


async def _run_custom(args: argparse.Namespace) -> int:
    from ..adapter import resolve_run_credentials

    # Reuses the MCP tool's input schema so the CLI enforces the same bounds
    # (minutes 1-60, positive steps, start_url requires app) with the same
    # messages; the resulting ValidationError is a ValueError, so dispatch's
    # handler prints it as a message rather than a traceback.
    params = ComputerUseTaskInput(
        task=args.task,
        app=args.app,
        start_url=args.start_url,
        minutes=args.minutes,
        max_steps=args.max_steps,
    )
    if _blocked():
        return 1
    print("The model takes over this Mac's desktop now; do not touch it during the run.")
    api_key, api_base_url = resolve_run_credentials()
    result = await run_task(
        **params.model_dump(),
        api_key=api_key,
        api_base_url=api_base_url,
        on_event=_print_event,
    )
    print(format_result(result))
    return 0 if result.get("outcome") == "completed" else 1


def apply_computer_use_environment(env: str | None) -> None:
    """Set or clear YUTORI_ENV so resolve_base_url() sees --env exactly as passed.

    Public computer-use commands default to production even if a shell has stale
    YUTORI_ENV state, so the absence of an explicit --env clears any ambient value
    rather than leaving it in place.
    """
    from ..adapter import ENV_VAR_ENVIRONMENT

    if env:
        os.environ[ENV_VAR_ENVIRONMENT] = env
    else:
        os.environ.pop(ENV_VAR_ENVIRONMENT, None)


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("computer-use", help="Set up, diagnose, and run macOS computer use")
    commands = parser.add_subparsers(dest="computer_use_command", required=True)
    commands.add_parser("setup", help="Install and configure the pinned CuaDriver")
    commands.add_parser("doctor", help="Run all computer-use readiness checks")
    commands.add_parser("smoke", help="Run Calculator mechanical and live checks")
    run_parser = commands.add_parser("run", help="Run one custom task on the visible desktop")
    run_parser.add_argument("task", help="Task for the model to perform")
    run_parser.add_argument("--app", default=None, help="Application to target")
    run_parser.add_argument("--start-url", dest="start_url", default=None, help="URL to open in the app")
    run_parser.add_argument(
        "--minutes",
        type=float,
        default=COMPUTER_USE_DEFAULT_MINUTES,
        help=f"Absolute deadline in minutes (1-{COMPUTER_USE_MAX_MINUTES})",
    )
    run_parser.add_argument(
        "--max-steps",
        dest="max_steps",
        type=int,
        default=COMPUTER_USE_DEFAULT_MAX_STEPS,
        help="Maximum actions",
    )


def dispatch(command: str, args: argparse.Namespace | None = None) -> int:
    if command not in {"setup", "doctor", "smoke", "run"}:
        raise ValueError(f"Unknown computer-use command: {command}")
    try:
        if command == "setup":
            return _setup()
        if command == "doctor":
            return _doctor()
        if command == "run":
            if args is None:
                raise ValueError("computer-use run needs its parsed arguments")
            return asyncio.run(_run_custom(args))
        return asyncio.run(_smoke_live())
    except ValueError as error:
        # Out-of-bounds run arguments should read as a clear message,
        # not a traceback. Pydantic's ValidationError is a ValueError too.
        print(error)
        return 1
