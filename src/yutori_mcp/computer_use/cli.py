from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from ..schemas import (
    COMPUTER_USE_DEFAULT_MAX_STEPS,
    COMPUTER_USE_DEFAULT_MINUTES,
    COMPUTER_USE_DEFAULT_MODE,
    COMPUTER_USE_MAX_MINUTES,
    ComputerUseTaskInput,
)
from .constants import (
    DELIVERY_MODE_BACKGROUND,
    DELIVERY_MODES,
    DRIVER_INSTALLER_SHA256,
    DRIVER_VERSION,
)
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
from .result import Terminal, describe_delivery_surface, format_terminal_action, format_terminal_result
from .supervisor import run_task_with_resolved_credentials, stop_active_run


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


def _report(result: dict[str, Any], *, include_actions: bool = True) -> int:
    """Print the formatted run result and derive the process exit code from its outcome.

    ``include_actions`` stays on for commands that streamed nothing while the run was
    in progress; `run` turns it off, having already printed each action as it landed.
    """
    print(format_terminal_result(result, Terminal.detect(), include_actions=include_actions))
    return 0 if result.get("outcome") == "completed" else 1


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
            result = await run_task_with_resolved_credentials(
                task="In Calculator, clear the display, compute 9 * 9, and report the result.",
                app="Calculator",
                start_url=None,
                minutes=2,
                max_steps=10,
                lock=lock,
            )
    except ComputerUseBusyError as error:
        print(error)
        return 1
    return _report(result)


def hands_off_notice(mode: str) -> str:
    """What the operator must (not) do with the Mac while a run of ``mode`` is in progress."""
    if mode == DELIVERY_MODE_BACKGROUND:
        return "The model drives only the target app's window in the background; keep working, but leave that window alone."
    return "The model takes over this Mac's desktop now; do not touch it during the run."


def _event_printer(mode: str, app: str | None, paint: Terminal | None = None):
    surface = describe_delivery_surface(mode, app)
    paint = Terminal.detect() if paint is None else paint

    async def print_event(event: dict) -> None:
        if event.get("type") == "ready":
            print(f"{paint(paint.glyph('bullet'), 'green')} runner ready, driving {surface}\n", flush=True)
            return
        print("\n".join(format_terminal_action(event, paint)), flush=True)

    return print_event


def format_run_header(params: ComputerUseTaskInput, paint: Terminal) -> str:
    """The block a `run` opens with: what was asked, where it lands, and the limits."""
    target = params.app or "the visible desktop"
    if params.start_url:
        target += f"  {params.start_url}"
    limits = f"{params.mode}  {paint.glyph('separator')}  {params.minutes:g} min  "
    limits += f"{paint.glyph('separator')}  {params.max_steps} model turns"
    return "\n".join(
        [
            paint.rule("YUTORI COMPUTER USE"),
            paint.row("task", params.task),
            paint.row("target", target),
            paint.row("limits", limits),
            "",
            paint(f"{paint.glyph('warn')} {hands_off_notice(params.mode)}", "yellow", "bold"),
            "",
        ]
    )


async def _run_custom(args: argparse.Namespace) -> int:
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
        mode=args.mode,
        allow_foreground_fallback=args.allow_foreground_fallback,
    )
    if _blocked():
        return 1
    paint = Terminal.detect()
    print(format_run_header(params, paint))
    result = await run_task_with_resolved_credentials(
        **params.model_dump(),
        on_event=_event_printer(params.mode, params.app, paint),
    )
    return _report(result, include_actions=False)


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
    commands.add_parser("stop", help="Stop the active computer-use run (the local stop for background runs)")
    run_parser = commands.add_parser("run", help="Run one custom task on the visible desktop or in one app window")
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
        help="Maximum model turns (one turn may contain multiple actions)",
    )
    run_parser.add_argument(
        "--mode",
        choices=DELIVERY_MODES,
        default=COMPUTER_USE_DEFAULT_MODE,
        help="foreground drives the visible desktop; background drives only --app's window without taking focus",
    )
    run_parser.add_argument(
        "--allow-foreground-fallback",
        dest="allow_foreground_fallback",
        action="store_true",
        help="Background only: retry an action that did not land with the window fronted briefly",
    )


def dispatch(command: str, args: argparse.Namespace | None = None) -> int:
    if command not in {"setup", "doctor", "smoke", "run", "stop"}:
        raise ValueError(f"Unknown computer-use command: {command}")
    try:
        if command == "setup":
            return _setup()
        if command == "doctor":
            return _doctor()
        if command == "stop":
            print(stop_active_run())
            return 0
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
