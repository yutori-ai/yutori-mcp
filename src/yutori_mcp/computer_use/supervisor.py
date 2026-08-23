from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
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


async def _notify(
    on_event: EventCallback | None, event: dict[str, Any]
) -> EventCallback | None:
    """Invoke the callback and return it, or None once it must stay disabled."""
    if on_event is None:
        return None
    try:
        await asyncio.wait_for(on_event(event), EVENT_CALLBACK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "Computer-use event callback timed out after %.0fs; "
            "disabling notifications for the rest of the run",
            EVENT_CALLBACK_TIMEOUT_SECONDS,
        )
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Computer-use event callback failed; continuing the run")
    return on_event


def _child_environment(api_key: str) -> dict[str, str]:
    # PATH is not optional here even though the env is otherwise built from scratch: shell
    # commands the model runs resolve their tools from it, so an env without PATH makes every
    # shell_command fail with ENOENT. CUA_TELEMETRY_ENABLED must be off in the environment,
    # not just the agent constructor, because the harness fires an import-time telemetry
    # event before any constructor argument is seen.
    env = {
        "YUTORI_API_KEY": api_key,
        "PATH": child_search_path(),
        "CUA_TELEMETRY_ENABLED": "false",
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
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def _drain_stderr(stream: asyncio.StreamReader, secret: str) -> list[str]:
    diagnostics: list[str] = []
    while line := await stream.readline():
        diagnostics.append(
            line.decode(errors="replace").replace(secret, "[REDACTED]").rstrip()
        )
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
    )
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
                event = json.loads(
                    line.decode(errors="replace").replace(api_key, "[REDACTED]")
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                return failure(
                    "Computer-use runner emitted invalid JSON.", actions=actions
                )
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
            return failure(
                f"Computer-use runner exited without a result.{suffix}", actions=actions
            )
        if terminal["type"] == "error":
            return failure(
                f"{terminal.get('code', 'RUNNER_ERROR')}: {terminal.get('message', 'Runner error')}",
                actions=actions,
            )
        terminal.setdefault("actions", actions)
        return terminal
    except TimeoutError:
        await _stop_process_group(process)
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
        if process.returncode is None:
            await _stop_process_group(process)
        if not stderr_task.done():
            stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)


def runner_command() -> list[str]:
    """The runner child's argv: this interpreter, running the runner module.

    The runner lives in this package and imports the pinned `cua-agent`
    harness from the same environment, so the interpreter serving the MCP
    process is exactly the one that can run it.
    """
    return [sys.executable, "-m", "yutori_mcp.computer_use.runner"]


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
            driver = find_cua_driver()
            if driver is None:  # Kept defensive because preflight already checked it.
                return failure(
                    "cua-driver not found. Run: yutori-mcp computer-use setup"
                )
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
                "driver_path": str(driver),
            }
            return await _supervise(
                command=runner_command(),
                request=request,
                api_key=api_key,
                deadline=deadline,
                on_event=on_event,
            )
    except (ComputerUseBusyError, RuntimeError, OSError) as error:
        return failure(str(error))
