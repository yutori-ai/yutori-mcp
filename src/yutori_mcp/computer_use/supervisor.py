from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .constants import (
    DELIVERY_MODE_FOREGROUND,
    DELIVERY_MODES,
    DRIVER_VERSION,
    MCP_VERSION,
    MODEL,
    PROTOCOL_VERSION,
    SDK_ARTIFACT_SHA256,
    SDK_PROVENANCE_SHA256,
    SDK_VERSION,
)
from .lock import ComputerUseBusyError, DesktopLock
from .preflight import child_search_path, find_cua_driver
from .result import failure, redact, terminal_result

logger = logging.getLogger(__name__)

# Receives each non-terminal runner event (`ready`, `action`) as it streams in, so a host
# can surface live progress. Presentation only, like the reasoning overlay: a callback
# that raises must never cost the run, so failures are logged and swallowed, and a callback
# that blocks (a wedged notification transport) is cancelled after a bounded wait and
# disabled for the rest of the run so it cannot stall past the run's deadline.
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

EVENT_CALLBACK_TIMEOUT_SECONDS = 5.0
EVENT_CALLBACK_FLUSH_SECONDS = 0.25
EVENT_QUEUE_LIMIT = 32
RUNNER_SHUTDOWN_GRACE_SECONDS = 5.0
RUNNER_FRAME_LIMIT_BYTES = 8 * 1024 * 1024
RUNNER_MODULE = "yutori_mcp.computer_use.runner"


def runner_pid_path() -> Path:
    """Where the supervisor advertises the live runner's pid for `computer-use stop`.

    Next to the desktop lock. Advisory only: the lock stays the concurrency gate, and the
    file names a process group that `stop` verifies is really a runner before signalling.
    """
    return Path.home() / ".yutori" / "computer-use.pid"


def _record_runner_pid(pid: int) -> None:
    with suppress(OSError):
        path = runner_pid_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid}\n")


def _clear_runner_pid(pid: int) -> None:
    with suppress(OSError, ValueError):
        path = runner_pid_path()
        if int(path.read_text().strip()) == pid:
            path.unlink()


def _discard_stale_pid_file(path: Path) -> str:
    """Remove a pid file that no longer names a live runner, and report that fact."""
    with suppress(OSError):
        path.unlink()
    return "No computer-use run is active (removed a stale pid file)."


def _process_command(pid: int) -> str | None:
    try:
        listing = subprocess.run(
            ["/bin/ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return listing or None


def stop_active_run() -> str:
    """Ask the live runner to stop; its SIGTERM handler ends the run with outcome `aborted`.

    A background run has no on-screen Stop button, so this is the operator's local stop.
    The runner is its own process group leader (`start_new_session=True`), so signalling
    the group also reaches any model-owned shells.
    """
    path = runner_pid_path()
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return "No computer-use run is active."
    command = _process_command(pid)
    if command is None or RUNNER_MODULE not in command:
        return _discard_stale_pid_file(path)
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return _discard_stale_pid_file(path)
    return f"Asked the computer-use runner (pid {pid}) to stop; the run ends with outcome 'aborted'."


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


class _EventNotifier:
    """Deliver presentation events without blocking the runner protocol reader.

    The queue is deliberately bounded. If a host cannot keep up, stale progress
    is discarded in favor of the latest state while stdout continues draining.
    """

    def __init__(self, callback: EventCallback) -> None:
        self._callback: EventCallback | None = callback
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=EVENT_QUEUE_LIMIT)
        self._task = asyncio.create_task(self._run())
        self._dropped = 0

    def submit(self, event: dict[str, Any]) -> None:
        if self._callback is None or self._task.done():
            return
        if self._queue.full():
            with suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._queue.task_done()
                self._dropped += 1
        self._queue.put_nowait(event)

    async def _run(self) -> None:
        while self._callback is not None:
            event = await self._queue.get()
            try:
                self._callback = await _notify(self._callback, event)
            finally:
                self._queue.task_done()
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    async def close(self) -> None:
        if not self._task.done():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._queue.join(), EVENT_CALLBACK_FLUSH_SECONDS)
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        if self._dropped:
            logger.debug("Dropped %d stale computer-use progress event(s)", self._dropped)


def _child_environment(api_key: str) -> dict[str, str]:
    # PATH is required because the SDK resolves cua-driver by name and model-run
    # shell commands must retain the host's standard utilities.
    env = {
        "YUTORI_API_KEY": api_key,
        "PATH": child_search_path(),
    }
    for name in ("HOME", "TMPDIR", "LANG", "LC_ALL"):
        if value := os.environ.get(name):
            env[name] = value
    return env


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        # The runner handles SIGTERM by cancelling the SDK session, emitting its
        # terminal event, and reaping detached shell groups before it exits.
        await asyncio.wait_for(process.wait(), RUNNER_SHUTDOWN_GRACE_SECONDS)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


def _remaining_seconds(deadline: float) -> float:
    """Seconds left before ``deadline``, or raise ``asyncio.TimeoutError`` if none remain.

    ``_supervise`` checks this before both of its waits on the child process -- the
    per-line read loop and the final ``process.wait()`` after EOF -- so an expired
    absolute deadline is caught the same way in both places instead of one of them
    risking a zero/negative timeout reaching ``asyncio.wait_for``.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise asyncio.TimeoutError
    return remaining


async def _drain_stderr(stream: asyncio.StreamReader, secret: str) -> list[str]:
    diagnostics: deque[str] = deque(maxlen=20)
    while line := await stream.readline():
        diagnostics.append(redact(line.decode(errors="replace"), secret).rstrip())
    return list(diagnostics)


def _ready_error(event: dict[str, Any]) -> str | None:
    expected = {
        "protocol_version": PROTOCOL_VERSION,
        "package_version": MCP_VERSION,
        "sdk_version": SDK_VERSION,
        "sdk_artifact_sha256": SDK_ARTIFACT_SHA256,
        "sdk_provenance_sha256": SDK_PROVENANCE_SHA256,
        "driver_version_pinned": DRIVER_VERSION,
    }
    mismatches = [
        f"{name}={event.get(name)!r} (expected {value!r})"
        for name, value in expected.items()
        if event.get(name) != value
    ]
    return "; ".join(mismatches) if mismatches else None


def _event_shape_error(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    if event_type == "action":
        required: dict[str, type | tuple[type, ...]] = {
            "index": int,
            "tool": str,
            "status": str,
            "raw_status": str,
            "delivery_mode": str,
            "route": str,
            "refusal_code": (str, type(None)),
        }
    elif event_type == "result":
        required = {
            "outcome": str,
            "delivery_mode": str,
            "final_text": (str, type(None)),
        }
    elif event_type == "error":
        required = {"code": str, "message": str}
    else:
        return None
    invalid = [
        name for name, expected in required.items() if name not in event or not isinstance(event[name], expected)
    ]
    if "delivery_mode" in required and "delivery_mode" not in invalid and event["delivery_mode"] not in DELIVERY_MODES:
        invalid.append("delivery_mode")
    return f"invalid or missing fields: {', '.join(invalid)}" if invalid else None


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
        limit=RUNNER_FRAME_LIMIT_BYTES,
        # Never the caller's directory: `python -m` puts the child's cwd on
        # sys.path, and a run launched from inside the Yutori monorepo had its
        # `yutori/` source tree shadow the installed yutori SDK, crashing the
        # runner on import.
        cwd=os.path.expanduser("~"),
    )
    assert process.stdin and process.stdout and process.stderr
    stderr_task = asyncio.create_task(_drain_stderr(process.stderr, api_key))
    notifier = _EventNotifier(on_event) if on_event is not None else None
    actions: list[dict[str, Any]] = []
    terminal: dict[str, Any] | None = None
    ready = False
    mode = str(request.get("mode") or DELIVERY_MODE_FOREGROUND)

    def protocol_failure(detail: str) -> dict[str, Any]:
        """A runner protocol violation, reported with the actions observed so far.

        Every violation below returned the same two things by hand -- the
        "Computer-use runner" subject the host reads the message by, and the running
        ``actions`` list -- across eleven early-return branches. Binding both here
        means a new branch cannot word the subject differently or, worse, omit
        ``actions`` and silently drop the action history from the result.
        """
        return failure(f"Computer-use runner {detail}", actions=actions, delivery_mode=mode)

    try:
        process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()
        while True:
            remaining = _remaining_seconds(deadline)
            try:
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
            except ValueError:
                return protocol_failure(f"event exceeded the {RUNNER_FRAME_LIMIT_BYTES}-byte limit.")
            if not line:
                break
            try:
                event = json.loads(redact(line.decode(), api_key))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return protocol_failure("emitted invalid JSON.")
            if not isinstance(event, dict):
                return protocol_failure("emitted a non-object event.")
            if terminal is not None:
                return protocol_failure("emitted data after its terminal event.")
            event_type = event.get("type")
            if shape_error := _event_shape_error(event):
                return protocol_failure(f"emitted malformed {event_type!r} event: {shape_error}.")
            if event_type == "action":
                if not ready:
                    return protocol_failure("emitted an action before ready.")
                actions.append(event)
                if notifier is not None:
                    notifier.submit(event)
            elif event_type == "ready":
                if ready:
                    return protocol_failure("emitted multiple ready events.")
                if mismatch := _ready_error(event):
                    return protocol_failure(f"provenance mismatch: {mismatch}")
                ready = True
                # Only advertise a process that has emitted ready after installing
                # its synchronous SIGTERM latch, so `stop` cannot hit the old
                # startup window where default signal handling killed it abruptly.
                _record_runner_pid(process.pid)
                if notifier is not None:
                    notifier.submit(event)
            elif event_type in {"result", "error"}:
                if not ready:
                    return protocol_failure("terminated before ready.")
                terminal = event
                # Any further stdout is a protocol violation, so consume to EOF.
            else:
                return protocol_failure(f"emitted unknown event type: {event_type!r}.")
        remaining = _remaining_seconds(deadline)
        await asyncio.wait_for(process.wait(), remaining)
        if terminal is None:
            diagnostics = await stderr_task
            suffix = f" Diagnostics: {'; '.join(diagnostics)}" if diagnostics else ""
            return protocol_failure(f"exited without a result.{suffix}")
        if terminal["type"] == "error":
            return failure(
                f"{terminal.get('code', 'RUNNER_ERROR')}: {terminal.get('message', 'Runner error')}",
                actions=actions,
                delivery_mode=mode,
            )
        terminal["actions"] = actions
        return terminal
    except asyncio.TimeoutError:
        await _stop_process_group(process)
        return terminal_result("limit", "The absolute deadline expired.", actions=actions, delivery_mode=mode)
    except asyncio.CancelledError:
        await _stop_process_group(process)
        return terminal_result(
            "aborted",
            "Computer-use task was cancelled; the runner process group was terminated.",
            actions=actions,
            delivery_mode=mode,
        )
    finally:
        if process.returncode is None:
            await _stop_process_group(process)
        if notifier is not None:
            await notifier.close()
        _clear_runner_pid(process.pid)
        if not stderr_task.done():
            stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)


def run_chat_id(result: dict[str, Any]) -> str | None:
    """The platform chat id for a run: the result's own, else the latest one an action carried."""
    chat_id = result.get("chat_id")
    if isinstance(chat_id, str) and chat_id:
        return chat_id
    for action in reversed(result.get("actions") or []):
        candidate = action.get("chat_id")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def attach_run_link(result: dict[str, Any], platform_url: str | None) -> dict[str, Any]:
    """Add ``chat_id`` and, when the platform is known, the ``run_url`` of this run's page."""
    chat_id = run_chat_id(result)
    if chat_id is None:
        return result
    result["chat_id"] = chat_id
    if platform_url:
        result["run_url"] = f"{platform_url.rstrip('/')}/navigator/chats/{chat_id}"
    return result


def python_runner_command() -> list[str]:
    """The Python runner child's argv: this interpreter, running the runner module.

    The runner lives in this package and imports the pinned Python SDK
    runtime from the same environment, so the interpreter serving the MCP
    process is exactly the one that can run it. `-I` (isolated mode) keeps the
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
    platform_url: str | None = None,
    mode: str = DELIVERY_MODE_FOREGROUND,
    allow_foreground_fallback: bool = False,
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
                "mode": mode,
                "allow_foreground_fallback": allow_foreground_fallback,
                "model": MODEL,
                "api_base_url": api_base_url,
            }
            if find_cua_driver() is None:  # Kept defensive; preflight already checked it.
                return failure("cua-driver not found. Run: yutori-mcp computer-use setup", delivery_mode=mode)
            result = await _supervise(
                command=python_runner_command(),
                request=request,
                api_key=api_key,
                deadline=deadline,
                on_event=on_event,
            )
            return attach_run_link(result, platform_url)
    except (ComputerUseBusyError, RuntimeError, OSError, ValueError) as error:
        return failure(str(error), delivery_mode=mode)
