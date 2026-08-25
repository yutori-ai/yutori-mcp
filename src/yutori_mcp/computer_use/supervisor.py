from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .constants import MODEL, PROTOCOL_VERSION
from .lock import ComputerUseBusyError, DesktopLock
from .preflight import child_search_path, find_cua_driver
from .result import failure

logger = logging.getLogger(__name__)

# Receives each non-terminal runner event (`ready`, `action`) as it streams in, so a host
# can surface live progress. Presentation only, like the reasoning overlay: a callback
# that raises must never cost the run, so failures are logged and swallowed, and a callback
# that blocks (a wedged notification transport) is cancelled after a bounded wait and
# disabled for the rest of the run so it cannot stall past the run's deadline.
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

EVENT_CALLBACK_TIMEOUT_SECONDS = 5.0
EMPTY_SESSION_CONFIRMATION_SECONDS = 0.1
ProcessIdentity = int | tuple[int, int]


class _MacBSDInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("command", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("tty_device", ctypes.c_uint32),
        ("tty_pgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


async def _notify(on_event: EventCallback | None, event: dict[str, Any]) -> EventCallback | None:
    """Invoke the callback and return it, or None once it must stay disabled."""
    if on_event is None:
        return None
    try:
        await asyncio.wait_for(on_event(event), EVENT_CALLBACK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "Computer-use event callback timed out after %.0fs; disabling notifications for the rest of the run",
            EVENT_CALLBACK_TIMEOUT_SECONDS,
        )
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Computer-use event callback failed; continuing the run")
    return on_event


def _child_environment(api_key: str) -> dict[str, str]:
    # PATH is not optional even though the env is otherwise built from scratch:
    # shell commands the model runs resolve their tools from it.
    env = {
        "YUTORI_API_KEY": api_key,
        "PATH": child_search_path(),
    }
    for name in ("HOME", "TMPDIR", "LANG", "LC_ALL"):
        if value := os.environ.get(name):
            env[name] = value
    return env


def _process_identity(pid: int) -> ProcessIdentity | None:
    if sys.platform == "darwin":
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        info = _MacBSDInfo()
        size = libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size != ctypes.sizeof(info) or info.uid != os.getuid():
            return None
        return info.start_seconds, info.start_microseconds
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        _prefix, separator, fields = stat.partition(") ")
        if not separator:
            return None
        return int(fields.split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _runner_session_pids(session_id: int) -> list[int] | None:
    try:
        output = subprocess.run(
            ["/bin/ps", "-axo", "pid=,sess="],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pids: list[int] = []
    for line in output.splitlines():
        try:
            pid, process_session = (int(part) for part in line.split())
        except (TypeError, ValueError):
            continue
        if process_session == session_id:
            pids.append(pid)
    return pids


def _kernel_runner_session_pids(session_id: int) -> list[int] | None:
    if sys.platform == "darwin":
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        capacity = libproc.proc_listallpids(None, 0)
        if capacity <= 0:
            return None
        buffer = (ctypes.c_int * capacity)()
        count = libproc.proc_listallpids(buffer, ctypes.sizeof(buffer))
        if count <= 0:
            return None
        candidates = [pid for pid in buffer[:count] if pid > 0]
    else:
        try:
            candidates = [int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit()]
        except OSError:
            return None
    members: list[int] = []
    for pid in candidates:
        try:
            if os.getsid(pid) == session_id:
                members.append(pid)
        except (ProcessLookupError, PermissionError):
            continue
    return members


def _owned_runner_session_pids(
    session_id: int, session_identity: ProcessIdentity | None = None
) -> list[int] | None:
    leader_identity = _process_identity(session_id)
    if session_identity is not None and leader_identity is not None and leader_identity != session_identity:
        return []
    if sys.platform == "darwin":
        pids = _kernel_runner_session_pids(session_id)
        if pids is None:
            pids = _runner_session_pids(session_id)
    else:
        pids = _runner_session_pids(session_id)
        if pids is None:
            pids = _kernel_runner_session_pids(session_id)
    leader_identity = _process_identity(session_id)
    if session_identity is not None and leader_identity is not None and leader_identity != session_identity:
        return []
    return pids


def _signal_runner_session(
    session_id: int,
    sent_signal: signal.Signals,
    session_identity: ProcessIdentity | None = None,
) -> bool:
    """Signal every process still contained in the runner's private session."""
    pids = _owned_runner_session_pids(session_id, session_identity)
    if pids is None:
        return False
    leader_identity = _process_identity(session_id)
    if session_identity is not None and leader_identity is not None and leader_identity != session_identity:
        return True
    for pid in pids:
        try:
            if os.getsid(pid) != session_id:
                continue
        except ProcessLookupError:
            continue
        try:
            os.kill(pid, sent_signal)
        except (ProcessLookupError, PermissionError):
            pass
    return True


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate the runner session, escalating even after its leader exits."""
    session_identity = getattr(process, "_yutori_session_identity", None)
    if session_identity is None:
        session_identity = _process_identity(process.pid)
    enumerated = _signal_runner_session(process.pid, signal.SIGTERM, session_identity)
    if not enumerated and process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    if await _wait_for_empty_session(process.pid, session_identity, timeout=2):
        if process.returncode is None:
            await process.wait()
        return
    enumerated = _signal_runner_session(process.pid, signal.SIGKILL, session_identity)
    if not enumerated and process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    if process.returncode is None:
        await process.wait()
    if not await _wait_for_empty_session(process.pid, session_identity, timeout=2, kill=True):
        raise RuntimeError("Could not verify that the process session stopped.")


async def _wait_for_empty_session(
    session_id: int,
    session_identity: ProcessIdentity | None,
    *,
    timeout: float,
    kill: bool = False,
) -> bool:
    deadline = time.monotonic() + timeout
    empty_since: float | None = None
    while time.monotonic() < deadline:
        members = _owned_runner_session_pids(session_id, session_identity)
        if members == []:
            empty_since = empty_since or time.monotonic()
            if time.monotonic() - empty_since >= EMPTY_SESSION_CONFIRMATION_SECONDS:
                return True
        else:
            empty_since = None
        if kill and members:
            _signal_runner_session(session_id, signal.SIGKILL, session_identity)
        await asyncio.sleep(0.02)
    return False


async def _drain_stderr(stream: asyncio.StreamReader, secret: str) -> list[str]:
    diagnostics: list[str] = []
    while line := await stream.readline():
        diagnostics.append(line.decode(errors="replace").replace(secret, "[REDACTED]").rstrip())
    return diagnostics[-20:]


async def _supervise(
    *,
    command: list[str],
    request: dict[str, Any],
    api_key: str,
    deadline: float,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_environment(api_key),
        start_new_session=True,
        # Never the caller's directory: `python -m` puts the child's cwd on
        # sys.path, and a run launched from inside the Yutori monorepo had its
        # `yutori/` source tree shadow the installed yutori SDK, crashing the
        # runner on import.
        cwd=os.path.expanduser("~"),
    )
    session_identity = getattr(process, "_yutori_session_identity", None) or _process_identity(process.pid)
    if session_identity is None:
        with suppress(ProcessLookupError):
            process.terminate()
        await process.wait()
        return failure("Could not establish the computer-use runner process identity.")
    setattr(process, "_yutori_session_identity", session_identity)
    assert process.stdin and process.stdout and process.stderr
    stderr_task = asyncio.create_task(_drain_stderr(process.stderr, api_key))
    actions: list[dict[str, Any]] = []
    terminal: dict[str, Any] | None = None
    try:
        process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            line = await asyncio.wait_for(process.stdout.readline(), remaining)
            if not line:
                break
            try:
                event = json.loads(line.decode(errors="replace").replace(api_key, "[REDACTED]"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return failure("Computer-use runner emitted invalid JSON.", actions=actions)
            event_type = event.get("type")
            if event_type == "action":
                actions.append(event)
                on_event = await _notify(on_event, event)
            elif event_type == "ready":
                on_event = await _notify(on_event, event)
            elif event_type in {"result", "error"}:
                if terminal is not None:
                    return failure(
                        "Computer-use runner emitted multiple terminal events.",
                        actions=actions,
                    )
                terminal = event
                # Any further stdout is a protocol violation, so consume to EOF.
        await process.wait()
        if terminal is None:
            diagnostics = await stderr_task
            suffix = f" Diagnostics: {'; '.join(diagnostics)}" if diagnostics else ""
            return failure(f"Computer-use runner exited without a result.{suffix}", actions=actions)
        if terminal["type"] == "error":
            return failure(
                f"{terminal.get('code', 'RUNNER_ERROR')}: {terminal.get('message', 'Runner error')}",
                actions=actions,
            )
        terminal.setdefault("actions", actions)
        return terminal
    except (TimeoutError, asyncio.TimeoutError):
        timeout_observed_at = time.monotonic()
        await _stop_process_group(process)
        # Python 3.10's wait_for can translate cancellation of its inner read
        # into TimeoutError. Capture time before cleanup for that version;
        # newer Tasks also expose the cancellation count directly.
        current_task = asyncio.current_task()
        cancelling = getattr(current_task, "cancelling", None)
        if timeout_observed_at < deadline or (cancelling is not None and cancelling()):
            return failure(
                "Computer-use task was cancelled; the runner process group was terminated.",
                actions=actions,
            )
        return {
            "outcome": "limit",
            "delivery_mode": "foreground",
            "final_text": "The absolute deadline expired.",
            "actions": actions,
        }
    except asyncio.CancelledError:
        await _stop_process_group(process)
        return failure(
            "Computer-use task was cancelled; the runner process group was terminated.",
            actions=actions,
        )
    finally:
        await _stop_process_group(process)
        if not stderr_task.done():
            stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)


def python_runner_command() -> list[str]:
    """The Python runner child's argv: this interpreter, running the runner module.

    The runner lives in this package and imports the pinned SDK loop from the
    same environment, so the interpreter serving the MCP process is exactly
    the one that can run it. `-I` (isolated mode) keeps the
    child's sys.path free of the working directory, PYTHONPATH, and user
    site-packages, so no ambient directory can shadow the installed packages;
    the venv's own site-packages still resolve.
    """
    return [sys.executable, "-I", "-m", "yutori_mcp.computer_use.runner"]


async def run_task(
    *,
    task: str,
    app: str | None,
    start_url: str | None,
    minutes: float,
    max_steps: int,
    api_key: str,
    api_base_url: str,
    lock: DesktopLock | None = None,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + minutes * 60
    deadline_ms = int((time.time() + minutes * 60) * 1000)
    try:
        with lock or DesktopLock():
            request = {
                "protocol_version": PROTOCOL_VERSION,
                "type": "run",
                "task": task,
                "app": app,
                "start_url": start_url,
                "deadline_ms": deadline_ms,
                "max_steps": max_steps,
                "model": MODEL,
                "api_base_url": api_base_url,
            }
            driver = find_cua_driver()
            if driver is None:  # Kept defensive; preflight already checked it.
                return failure("cua-driver not found. Run: yutori-mcp computer-use setup")
            request["driver_path"] = str(driver)
            return await _supervise(
                command=python_runner_command(),
                request=request,
                api_key=api_key,
                deadline=deadline,
                on_event=on_event,
            )
    except (ComputerUseBusyError, RuntimeError, OSError, ValueError) as error:
        return failure(str(error))
