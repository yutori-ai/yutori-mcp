from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO
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
    HOST_ONLY_EVENT_TYPES,
    MAX_CREDENTIAL_CHARACTERS,
)
from .lock import ComputerUseBusyError, DesktopLock
from .preflight import (
    blocker_message,
    check_driver_binary,
    check_runtime,
    child_search_path,
    embedded_driver_host,
    find_cua_driver,
    first_blocker,
    run_checks,
)
from .result import (
    Terminal,
    compact_json_line,
    describe_delivery_surface,
    elapsed_ms_since,
    format_duration,
    format_startup_line,
    format_runtime_version,
    format_terminal_action,
    format_terminal_result,
    is_clean_credential_line,
    read_bounded_line,
    structured_content,
)
from .supervisor import (
    PrewarmedRunner,
    discard_prewarmed_runner,
    prewarm_runner,
    run_task_with_resolved_credentials,
    stop_active_run,
)

VM_RUN_TOKEN_PREFIX = "yvm_"
# One `standby` request line: a JSON array of `run` arguments, task text included.
MAX_STANDBY_REQUEST_BYTES = 1024 * 1024


def _json_line(payload: dict[str, Any]) -> None:
    """One machine-readable stdout line; the `--json` surface a host application consumes."""
    print(compact_json_line(payload), flush=True)


def _doctor(*, json_output: bool = False) -> int:
    results = run_checks()
    ok = all(result.ok or not result.blocking for result in results)
    if json_output:
        _json_line({"type": "doctor", "ok": ok, "checks": [asdict(result) for result in results]})
        return 0 if ok else 1
    for result in results:
        label = "PASS"
        if not result.ok:
            label = "BLOCKED" if result.blocking else "WARNING"
        print(f"{label} {result.name}: {result.detail}")
        if result.remediation:
            print(f"  Fix: {result.remediation}")
    return 0 if ok else 1


def _download_installer(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def _setup() -> int:
    runtime = check_runtime()
    if not runtime.ok:
        print(runtime.remediation)
        return 1
    try:
        embedded = embedded_driver_host()
    except ValueError as error:
        print(error)
        return 1
    if embedded is not None:
        # The host application ships the driver and owns permissions; installing the standalone
        # CuaDriver.app here would create the second permission identity embedding exists to avoid.
        print(f"Embedded cua-driver host configured ({embedded.binary}); nothing to install.")
        return _doctor()
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


def _blocked(*, json_output: bool = False, api_key_provided: bool = False) -> bool:
    """Print and return True if a blocking preflight check fails; False if ready to run."""
    blocker = first_blocker(api_key_provided=True) if api_key_provided else first_blocker()
    if blocker is None:
        return False
    if json_output:
        _json_line({"type": "blocked", **asdict(blocker)})
    else:
        print(blocker_message(blocker))
    return True


def _read_vm_run_token(stream: TextIO | None = None) -> str:
    source = stream or sys.stdin
    credential_frame = read_bounded_line(source, MAX_CREDENTIAL_CHARACTERS)
    if credential_frame is None:
        raise ValueError("Expected one newline-terminated VM run token on stdin.")
    token = credential_frame[:-1]
    if not is_clean_credential_line(token) or not token.startswith(VM_RUN_TOKEN_PREFIX):
        raise ValueError("Expected a VM run token on stdin.")
    return token


def _credential_input_error(message: str, *, json_output: bool) -> int:
    if json_output:
        _json_line({"type": "error", "code": "INVALID_CREDENTIAL_INPUT", "message": message})
        return 1
    raise ValueError(message)


def _exit_code(result: dict[str, Any]) -> int:
    """The process exit code a run result maps to: 0 only for a completed outcome."""
    return 0 if result.get("outcome") == "completed" else 1


def _report(result: dict[str, Any], *, include_actions: bool = True) -> int:
    """Print the formatted run result and derive the process exit code from its outcome.

    ``include_actions`` stays on for commands that streamed nothing while the run was
    in progress; `run` turns it off, having already printed each action as it landed.
    """
    print(format_terminal_result(result, Terminal.detect(), include_actions=include_actions))
    return _exit_code(result)


async def _mechanical_calculator_check() -> str:
    from yutori.navigator.macos.transport import CuaDriverTransport

    from .app import prepare_app
    from .targeting import TargetGuardedMacOSComputer

    driver = find_cua_driver()
    if driver is None:
        raise RuntimeError(check_driver_binary().remediation)
    transport = CuaDriverTransport(binary=driver)
    sentinel = f"yutori-smoke-{uuid.uuid4().hex[:12]}"
    async with TargetGuardedMacOSComputer(
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
        return "The model chooses app windows in the background; keep working, but leave the window being driven alone."
    return "The model takes over this Mac's desktop now; do not touch it during the run."


def _print_milestone(
    paint: Terminal, label: str, start: float, *, clock: Callable[[], float] = time.monotonic
) -> None:
    """One green "<label>  <duration since start>" line, this file's style for a reached milestone."""
    duration = paint(format_duration(elapsed_ms_since(start, clock=clock)), "dim")
    print(f"{paint(paint.glyph('bullet'), 'green')} {label}  {duration}", flush=True)


def _event_printer(
    mode: str,
    app: str | None,
    paint: Terminal | None = None,
    *,
    started_at: float | None = None,
    clock: Callable[[], float] = time.monotonic,
):
    surface = describe_delivery_surface(mode, app)
    paint = Terminal.detect() if paint is None else paint
    started_at = clock() if started_at is None else started_at

    async def print_event(event: dict) -> None:
        if event.get("type") in HOST_ONLY_EVENT_TYPES:
            return
        if event.get("type") == "ready":
            _print_milestone(paint, "runner process ready", started_at, clock=clock)
            return
        if event.get("type") == "startup":
            observed = {**event, "elapsed_ms": elapsed_ms_since(started_at, clock=clock)}
            line = format_startup_line(observed, app=app)
            if event.get("phase") == "computer" and app is None:
                line = line.replace("computer session ready", f"ready to drive {surface}", 1)
            print(f"{paint(paint.glyph('bullet'), 'green')} {line}", flush=True)
            return
        print("\n".join(format_terminal_action(event, paint)), flush=True)

    return print_event


def _json_event_printer():
    """Relay every runner event verbatim as one JSON line, for a host that renders progress itself."""

    async def print_event(event: dict) -> None:
        _json_line(event)

    return print_event


def format_run_header(params: ComputerUseTaskInput, paint: Terminal) -> str:
    """The block a `run` opens with: what was asked, where it lands, and the limits."""
    target = params.app or (
        "automatic app selection" if params.mode == DELIVERY_MODE_BACKGROUND else "the visible desktop"
    )
    if params.start_url:
        target += f"  {params.start_url}"
    limits = f"{params.mode}  {paint.glyph('separator')}  {params.minutes:g} min  "
    limits += f"{paint.glyph('separator')}  {params.max_steps} model turns"
    return "\n".join(
        [
            paint.rule("YUTORI COMPUTER USE"),
            paint.row("task", params.task),
            paint.row("target", target),
            paint.row("version", format_runtime_version(paint)),
            paint.row("limits", limits),
            "",
            paint(f"{paint.glyph('warn')} {hands_off_notice(params.mode)}", "yellow", "bold"),
            "",
        ]
    )


async def _run_custom(args: argparse.Namespace, *, prewarmed: PrewarmedRunner | None = None) -> int:
    # Reuses the MCP tool's input schema so the CLI enforces the same bounds
    # (minutes 1-60, positive steps, start_url requires app) with the same
    # messages; the resulting ValidationError is a ValueError, so dispatch's
    # handler prints it as a message rather than a traceback.
    background_focus_overlay = bool(getattr(args, "background_focus_overlay", False))
    if background_focus_overlay and args.mode != DELIVERY_MODE_BACKGROUND:
        raise ValueError("--background-focus-overlay requires --mode background")
    if background_focus_overlay and getattr(args, "no_presentation", False):
        raise ValueError("--background-focus-overlay cannot be combined with --no-presentation")
    params = ComputerUseTaskInput(
        task=args.task,
        app=args.app,
        start_url=args.start_url,
        minutes=args.minutes,
        max_steps=args.max_steps,
        mode=args.mode,
        allow_foreground_fallback=args.allow_foreground_fallback,
        allow_local_shell=args.allow_local_shell,
    )
    json_output = bool(getattr(args, "json", False))
    api_key_stdin = bool(getattr(args, "api_key_stdin", False))
    vm_run_id = getattr(args, "vm_run_id", None)
    if api_key_stdin != (vm_run_id is not None):
        return _credential_input_error(
            "--api-key-stdin and --vm-run-id must be provided together.",
            json_output=json_output,
        )
    try:
        api_key_override = _read_vm_run_token() if api_key_stdin else None
    except ValueError as error:
        return _credential_input_error(str(error), json_output=json_output)
    paint = Terminal.detect()
    preflight_started = time.monotonic()
    if _blocked(json_output=json_output, api_key_provided=api_key_override is not None):
        return 1
    if json_output:
        # Lets a host split its launch-to-ready wait into this process's own start and the gate.
        _json_line({"type": "preflight", "duration_ms": elapsed_ms_since(preflight_started)})
    # Both branches below run the identical request through the supervisor, differing only
    # in which `on_event` callback renders progress; bound here once as the single source of
    # truth for the request's fixed display flags.
    run_task = functools.partial(
        run_task_with_resolved_credentials,
        **params.model_dump(),
        show_stop_button=not getattr(args, "hide_stop_item", False),
        presentation=not getattr(args, "no_presentation", False),
        background_focus_overlay=background_focus_overlay,
        exclude_capture_window_ids=tuple(getattr(args, "exclude_capture_windows", None) or ()),
        api_key_override=api_key_override,
        vm_run_id=str(vm_run_id) if vm_run_id is not None else None,
        prewarmed=prewarmed,
    )
    if json_output:
        result = await run_task(on_event=_json_event_printer())
        _json_line({"type": "result", **result})
        return _exit_code(result)
    _print_milestone(paint, "preflight ready", preflight_started)
    print(format_run_header(params, paint))
    runner_started = time.monotonic()
    result = await run_task(on_event=_event_printer(params.mode, params.app, paint, started_at=runner_started))
    return _report(result, include_actions=False)


def _standby_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="computer-use run", add_help=False, exit_on_error=False)
    _add_run_arguments(parser)
    return parser


def parse_standby_request(line: str) -> argparse.Namespace:
    """Parse the `run` arguments a host sends a standby process: one JSON array of argv strings.

    The same parser as `computer-use run`, so a host builds one argument list for both paths
    and every bound is enforced identically.
    """
    try:
        argv = json.loads(line)
    except json.JSONDecodeError:
        raise ValueError("Standby request was not valid JSON.") from None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ValueError("Standby request must be a JSON array of `run` arguments.")
    try:
        args = _standby_run_parser().parse_args(argv)
    # Before Python 3.13 some parse errors still exit even with exit_on_error=False.
    except (argparse.ArgumentError, SystemExit) as error:
        raise ValueError(f"Invalid standby run arguments: {error}") from None
    if args.api_key_stdin or args.vm_run_id is not None:
        raise ValueError("Standby runs read their request from stdin; VM run credentials are not supported.")
    args.json = True
    return args


async def _read_standby_request(runner: PrewarmedRunner) -> str | None:
    """The host's request line, or None once stdin closes without one (the host let it go).

    Raises RuntimeError if the prewarmed runner dies first, so the host can start a new standby
    instead of submitting to one that can only fail.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_STANDBY_REQUEST_BYTES)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    read = asyncio.ensure_future(reader.readline())
    lost = asyncio.ensure_future(runner.process.wait())
    try:
        done, _ = await asyncio.wait({read, lost}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (read, lost):
            if not task.done():
                task.cancel()
    if read not in done:
        raise RuntimeError("The prewarmed computer-use runner exited while on standby.")
    line = read.result().decode()
    return line if line.strip() else None


async def _standby() -> int:
    """Boot a run before its task exists, then run the one request the host sends on stdin.

    For host applications: the interpreter start, imports, and the runner process's own start
    (about two seconds together) happen while the operator is still typing. Emits `standby`
    once warm; after the request line it behaves exactly like `run --json`, with the preflight
    gate evaluated at submission so the Mac's state is checked when the run actually starts.
    """
    try:
        runner = await prewarm_runner()
    except (RuntimeError, OSError) as error:
        _json_line({"type": "error", "code": "STANDBY_FAILED", "message": str(error)})
        return 1
    try:
        _json_line({"type": "standby"})
        try:
            line = await _read_standby_request(runner)
        except RuntimeError as error:
            _json_line({"type": "error", "code": "STANDBY_LOST", "message": str(error)})
            return 1
        if line is None:
            return 0
        try:
            return await _run_custom(parse_standby_request(line), prewarmed=runner)
        except ValueError as error:
            _json_line({"type": "error", "code": "INVALID_REQUEST", "message": str(error)})
            return 1
    finally:
        await discard_prewarmed_runner(runner)


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


def _dispatch_setup(_args: argparse.Namespace | None) -> int:
    return _setup()


def _dispatch_doctor(args: argparse.Namespace | None) -> int:
    return _doctor(json_output=bool(args is not None and getattr(args, "json", False)))


def _dispatch_smoke(_args: argparse.Namespace | None) -> int:
    return asyncio.run(_smoke_live())


def _dispatch_stop(_args: argparse.Namespace | None) -> int:
    print(stop_active_run())
    return 0


def _dispatch_standby(_args: argparse.Namespace | None) -> int:
    return asyncio.run(_standby())


def _dispatch_run(args: argparse.Namespace | None) -> int:
    if args is None:
        raise ValueError("computer-use run needs its parsed arguments")
    return asyncio.run(_run_custom(args))


# Pairing each subcommand's help text with its handler keeps register_parser's
# advertised commands and dispatch's implemented commands from drifting apart,
# mirroring server.py's _AUTH_SUBCOMMANDS table.
_COMPUTER_USE_SUBCOMMANDS: dict[str, tuple[str, Callable[[argparse.Namespace | None], int]]] = {
    "setup": ("Install and configure the pinned CuaDriver", _dispatch_setup),
    "doctor": ("Run all computer-use readiness checks", _dispatch_doctor),
    "smoke": ("Run Calculator mechanical and live checks", _dispatch_smoke),
    "stop": ("Stop the active computer-use run (the local stop for background runs)", _dispatch_stop),
    "run": ("Run one custom task on the visible desktop or in app windows", _dispatch_run),
    "standby": (
        "For host applications: boot a run, then read one JSON array of `run` arguments from stdin",
        _dispatch_standby,
    ),
}


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("computer-use", help="Set up, diagnose, and run macOS computer use")
    commands = parser.add_subparsers(dest="computer_use_command", required=True)
    json_help = "Emit JSON lines instead of terminal text, for a host application that renders progress itself"
    for name, (help_text, _) in _COMPUTER_USE_SUBCOMMANDS.items():
        if name == "run":
            continue
        command = commands.add_parser(name, help=help_text)
        if name == "doctor":
            command.add_argument("--json", action="store_true", help=json_help)
    run_parser = commands.add_parser("run", help=_COMPUTER_USE_SUBCOMMANDS["run"][0])
    run_parser.add_argument("--json", action="store_true", help=json_help)
    _add_run_arguments(run_parser)


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Every `run` option except --json; shared with the standby request parser."""
    parser.add_argument(
        "--hide-stop-item",
        dest="hide_stop_item",
        action="store_true",
        help=(
            "Do not show the SDK's menu bar Stop item; "
            "the host application provides its own (the hotkey stays active)"
        ),
    )
    parser.add_argument(
        "--exclude-capture-window",
        dest="exclude_capture_windows",
        action="append",
        type=int,
        metavar="WINDOW_ID",
        help=(
            "CGWindowID of a host application window to keep out of the model's desktop frames "
            "(foreground runs); it stays on screen and in recordings. Repeatable."
        ),
    )
    parser.add_argument(
        "--no-presentation",
        dest="no_presentation",
        action="store_true",
        help=(
            "Show none of the SDK's surfaces (overlay, menu bar item, activity window, hotkey); "
            "the host application renders the run from the --json frame and activity events"
        ),
    )
    parser.add_argument(
        "--background-focus-overlay",
        action="store_true",
        help=(
            "Background only: show the Navigator pointer/reasoning/action overlay when the target app is frontmost; "
            "the embedding host owns status, activity, Stop, and hotkey surfaces"
        ),
    )
    parser.add_argument("task", help="Task for the model to perform")
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read one VM run token from stdin (requires --vm-run-id)",
    )
    parser.add_argument(
        "--vm-run-id",
        type=uuid.UUID,
        default=None,
        help="Run ID bound to the stdin VM token (requires --api-key-stdin)",
    )
    parser.add_argument("--app", default=None, help="Application to target")
    parser.add_argument("--start-url", dest="start_url", default=None, help="URL to open in the app")
    parser.add_argument(
        "--minutes",
        type=float,
        default=COMPUTER_USE_DEFAULT_MINUTES,
        help=f"Absolute deadline in minutes (1-{COMPUTER_USE_MAX_MINUTES})",
    )
    parser.add_argument(
        "--max-steps",
        dest="max_steps",
        type=int,
        default=COMPUTER_USE_DEFAULT_MAX_STEPS,
        help="Maximum model turns (one turn may contain multiple actions)",
    )
    parser.add_argument(
        "--mode",
        choices=DELIVERY_MODES,
        default=COMPUTER_USE_DEFAULT_MODE,
        help=("foreground drives the visible desktop; background (default) chooses and switches app windows "
              "without taking focus; --app is optional"),
    )
    parser.add_argument(
        "--allow-foreground-fallback",
        dest="allow_foreground_fallback",
        action="store_true",
        help="Background only: retry an action that did not land with the window fronted briefly",
    )
    parser.add_argument(
        "--no-local-shell",
        dest="allow_local_shell",
        action="store_false",
        help="Disable local shell and filesystem tools; drive only the visible desktop or target app window",
    )


def dispatch(command: str, args: argparse.Namespace | None = None) -> int:
    if command not in _COMPUTER_USE_SUBCOMMANDS:
        raise ValueError(f"Unknown computer-use command: {command}")
    _, handler = _COMPUTER_USE_SUBCOMMANDS[command]
    try:
        return handler(args)
    except ValueError as error:
        # Out-of-bounds run arguments should read as a clear message,
        # not a traceback. Pydantic's ValidationError is a ValueError too.
        print(error)
        return 1
