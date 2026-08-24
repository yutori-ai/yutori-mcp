from __future__ import annotations

import argparse
import asyncio
import hashlib
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.request import urlopen

from ..credentials import resolve_api_key_for_environment

from .constants import (
    DRIVER_INSTALLER_SHA256,
    DRIVER_VERSION,
    ENV_VAR_HARNESS,
    HARNESSES,
    resolve_harness,
)
from ..schemas import ComputerUseTaskInput

from .preflight import (
    check_driver_binary,
    child_search_path,
    find_cua_driver,
    first_blocker,
    harness_blocker,
    run_checks,
)
from .result import format_result
from .supervisor import run_task


def _doctor() -> int:
    harness = resolve_harness()
    print(
        f"Harness: {harness} (switch with {ENV_VAR_HARNESS} or the tool's harness parameter)"
    )
    results = run_checks(harness)
    for result in results:
        print(f"{'PASS' if result.ok else 'BLOCKED'} {result.name}: {result.detail}")
        if result.remediation:
            print(f"  Fix: {result.remediation}")
    for other in HARNESSES:
        if other == harness:
            continue
        blocker = harness_blocker(other)
        status = "available" if blocker is None else f"unavailable ({blocker.detail})"
        print(f"INFO harness '{other}': {status}")
    return 0 if all(result.ok for result in results) else 1


def _download_installer(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def _setup() -> int:
    blocker = harness_blocker()
    if blocker is not None:
        print(blocker.remediation)
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
    subprocess.run(
        ["open", "-n", "-g", "-a", "CuaDriver", "--args", "serve"], check=True
    )
    # Resolved after the installer runs, and by absolute path: a Dock-launched MCP client's PATH
    # does not include Homebrew, so the bare name would not be found here.
    driver = find_cua_driver()
    if driver is None:
        print(check_driver_binary().remediation)
        return 1
    subprocess.run([str(driver), "permissions", "grant"], check=True)
    return _doctor()


class _ScriptResult:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def _osascript(*lines: str) -> _ScriptResult:
    argv: list[str] = ["osascript"]
    for line in lines:
        argv.extend(["-e", line])
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return _ScriptResult(
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def _smoke_live() -> int:
    blocker = harness_blocker()
    if blocker is not None:
        print(blocker.remediation)
        return 1
    # The result is read back through Calculator's own copy-result (cmd+c) and
    # the clipboard, not the accessibility tree: macOS 26 rewrote Calculator and
    # "static text 1 of window 1" no longer exists there, so the AX read failed
    # on a machine whose Accessibility grant was fine. Keystrokes still need the
    # *terminal's* Accessibility permission, which is what this check verifies.
    #
    # Two measured failure modes shape this sequence. The clipboard is seeded
    # with a unique sentinel and the read must equal exactly "42", because a
    # previous smoke leaves "42" behind — without the seed, a cmd+c that
    # silently did nothing still passed. And the copy is retried as its own
    # polled step, because cmd+c can race the "=" keypress and copy the prior
    # display value (state restoration reopens Calculator mid-calculation).
    # Escape first clears that restored state.
    sentinel = f"yutori-smoke-{uuid.uuid4().hex[:12]}"
    setup = await _osascript(
        f'set the clipboard to "{sentinel}"',
        'tell application "Calculator" to activate',
        "delay 2",
        'tell application "System Events" to key code 53',
        "delay 0.3",
        'tell application "System Events" to keystroke "6*7="',
        "delay 1",
    )
    copied = ""
    if setup.returncode == 0:
        for _attempt in range(3):
            copy = await _osascript(
                'tell application "System Events" to keystroke "c" using command down',
                "delay 0.7",
                "the clipboard",
            )
            copied = copy.stdout.strip() if copy.returncode == 0 else ""
            if copied == "42":
                break
            await asyncio.sleep(0.5)
    if copied != "42":
        detail = setup.stderr.strip() or f"clipboard read {copied!r}"
        print(
            "Mechanical Calculator check failed; verify the terminal's Accessibility "
            "permission (System Settings > Privacy & Security > Accessibility)."
            + (f" Detail: {detail}" if detail else "")
        )
        return 1
    result = await run_task(
        task="In Calculator, clear the display, compute 9 * 9, and report the result.",
        app="Calculator",
        start_url=None,
        minutes=1,
        max_steps=10,
        api_key=resolve_api_key_for_environment("dev"),
        api_base_url="https://api.dev.yutori.com/v1",
    )
    print(format_result(result))
    return 0 if result.get("outcome") == "completed" else 1


async def _print_event(event: dict) -> None:
    if event.get("type") == "ready":
        print("runner ready; driving the desktop")
        return
    line = f"action #{event.get('index')}: {event.get('tool')} -> {event.get('status')}"
    if event.get("refusal_code"):
        line += f" ({event['refusal_code']})"
    if event.get("duration_ms") is not None:
        line += f" took {event['duration_ms']} ms"
    if event.get("elapsed_ms") is not None:
        line += f" [at {event['elapsed_ms']} ms]"
    print(line, flush=True)


async def _run_custom(args: argparse.Namespace) -> int:
    # Reuses the MCP tool's input schema so the CLI enforces the same bounds
    # (minutes 1-15, steps 1-100, start_url requires app) with the same
    # messages; the resulting ValidationError is a ValueError, so dispatch's
    # handler prints it as a message rather than a traceback.
    params = ComputerUseTaskInput(
        task=args.task,
        app=args.app,
        start_url=args.start_url,
        minutes=args.minutes,
        max_steps=args.max_steps,
        harness=args.harness,
    )
    blocker = first_blocker(params.harness)
    if blocker is not None:
        print(f"{blocker.detail} Fix: {blocker.remediation}")
        return 1
    print(
        "The model takes over this Mac's desktop now; do not touch it during the run."
    )
    result = await run_task(
        **params.model_dump(),
        api_key=resolve_api_key_for_environment("dev"),
        api_base_url="https://api.dev.yutori.com/v1",
        on_event=_print_event,
    )
    print(format_result(result))
    return 0 if result.get("outcome") == "completed" else 1


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "computer-use", help="Set up and diagnose the macOS computer-use preview"
    )
    commands = parser.add_subparsers(dest="computer_use_command", required=True)
    commands.add_parser("setup", help="Install and configure the pinned CuaDriver")
    commands.add_parser("doctor", help="Run all computer-use readiness checks")
    commands.add_parser("smoke", help="Run Calculator mechanical and live checks")
    run_parser = commands.add_parser(
        "run", help="Run one custom task on the visible desktop (dev only)"
    )
    run_parser.add_argument("task", help="Task for the model to perform")
    run_parser.add_argument(
        "--harness",
        choices=list(HARNESSES),
        default=None,
        help="Runner implementation (default: YUTORI_COMPUTER_USE_HARNESS or node)",
    )
    run_parser.add_argument("--app", default=None, help="Application to target")
    run_parser.add_argument(
        "--start-url", dest="start_url", default=None, help="URL to open in the app"
    )
    run_parser.add_argument(
        "--minutes", type=float, default=3, help="Absolute deadline in minutes (1-15)"
    )
    run_parser.add_argument(
        "--max-steps", dest="max_steps", type=int, default=60, help="Maximum actions (1-100)"
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
        # An invalid YUTORI_COMPUTER_USE_HARNESS value or out-of-bounds run
        # argument should read as the same clear message run_task reports,
        # not a traceback. Pydantic's ValidationError is a ValueError too.
        print(error)
        return 1
