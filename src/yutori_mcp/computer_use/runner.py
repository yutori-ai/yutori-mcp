"""The computer-use runner child process.

Spawned by the supervisor as ``python -m yutori_mcp.computer_use.runner``. It
reads one JSONL run request from stdin, drives the pinned ``cua-agent``
yutori n2 loop against the local desktop, and emits protocol-v1 events
(``ready``, ``action``..., then exactly one ``result`` or ``error``) on stdout.

It runs out of process on purpose: the agent loop's dependencies print to
stdout (litellm announces every transient-error retry), and the parent is an
MCP stdio server whose stdout is the protocol channel. The first thing this
module does is claim the real stdout for the event stream and point fd 1 at
stderr, so no library print can corrupt either process's framing.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from importlib import metadata
from typing import Any, TextIO

from .constants import DRIVER_VERSION, PROTOCOL_VERSION, TOOL_SET
from .driver import CuaDriverDesktop, DriverCLI, DriverError, prepare_app

# Standing instructions for the run. Carried over verbatim from the previous
# runner's macOS system turn so model behavior does not shift with the harness
# swap; the trained prompt already covers the normalized-coordinate rule, so
# this deliberately does not restate it.
SYSTEM_CONTEXT = (
    "You control the entire macOS screen. Use macOS conventions: cmd, not ctrl, for "
    "standard shortcuts. Prefer reliable keyboard shortcuts such as cmd+w to close the "
    "current window, cmd+q to quit, and cmd+shift+s for Save As. If one visual attempt "
    "to click a title-bar or menu close control misses, do not repeat the same "
    "coordinates; use the keyboard shortcut. If a desktop widget opens accidentally, "
    "press cmd+w once and continue. The coordinate origin is the top-left of the "
    "capture. Take screenshots after visual changes. If a click produces no visible "
    "change, the target may be busy rather than mis-aimed: retry the same click up to "
    "three times, checking a fresh screenshot between attempts, and if it still does "
    "not respond, reach the same result another way — a menu item, a keyboard "
    "shortcut, or a different control — instead of clicking it again. Once you have "
    "confirmed a step worked, leave that result in view: bring the file, window, or "
    "view it changed to the front, reopening or refreshing it if the change was made "
    "somewhere the screen does not show, so progress stays visible while the task is "
    "still running. When you finish, open the final artifact (the document, file, "
    "page, or app view holding the result) so it is visible on screen, then take a "
    "screenshot to confirm it before you summarize.\n\n"
    "Do not open or change System Settings, application settings, accounts, "
    "permissions, or defaults unless the user explicitly asked for that settings "
    "change."
)

STOP_SUMMARY_PROMPT = (
    "Stop here. Briefly summarize what you accomplished and what you found."
)

# Terminal-text markers the model was trained to prefix a final answer with.
FINAL_TEXT_MARKERS = ("[DONE]", "[INFEASIBLE]")

MAX_APP_RECOVERY_ATTEMPTS = 2


class RequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_string(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value:
        raise RequestError("INVALID_REQUEST", f"{field} must be a non-empty string.")
    return value


def parse_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestError("INVALID_REQUEST", "Request must be a JSON object.")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise RequestError(
            "UNSUPPORTED_PROTOCOL_VERSION",
            f"Expected protocol_version {PROTOCOL_VERSION}.",
        )
    if payload.get("type") != "run":
        raise RequestError("INVALID_REQUEST", "type must be 'run'.")
    task = _require_string(payload, "task")
    app = payload.get("app")
    if app is not None and (not isinstance(app, str) or not app):
        raise RequestError("INVALID_REQUEST", "app must be a non-empty string or null.")
    start_url = payload.get("start_url")
    if start_url is not None and (not isinstance(start_url, str) or not start_url):
        raise RequestError(
            "INVALID_REQUEST", "start_url must be a non-empty string or null."
        )
    if start_url is not None and app is None:
        raise RequestError("INVALID_REQUEST", "start_url requires app.")
    deadline_ms = payload.get("deadline_ms")
    if not isinstance(deadline_ms, int) or isinstance(deadline_ms, bool) or deadline_ms <= 0:
        raise RequestError("INVALID_REQUEST", "deadline_ms must be a positive integer.")
    max_steps = payload.get("max_steps")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
        raise RequestError("INVALID_REQUEST", "max_steps must be a positive integer.")
    return {
        "task": task,
        "app": app,
        "start_url": start_url,
        "deadline_ms": deadline_ms,
        "max_steps": max_steps,
        "model": _require_string(payload, "model"),
        "api_base_url": _require_string(payload, "api_base_url"),
        "driver_path": _require_string(payload, "driver_path"),
    }


class Emitter:
    def __init__(self, stream: TextIO):
        self._stream = stream

    def emit(self, event: dict[str, Any]) -> None:
        self._stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        self._stream.flush()


def _package_version() -> str:
    try:
        return metadata.version("yutori-mcp")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def classify_result(outputs: list[dict[str, Any]] | None) -> str:
    """Map one tool call's output frames onto the action event's raw status.

    Mirrors the previous runner's classification so hosts reading the event
    stream see the same vocabulary: no ``[ERROR]`` anywhere means the driver
    confirmed the dispatch; "refused" means a policy refusal; a timeout may
    have dispatched before dying; anything else is unverifiable.
    """
    for frame in outputs or []:
        output = frame.get("output")
        text: str | None = None
        if isinstance(output, str):
            text = output
        elif isinstance(output, dict):
            result = output.get("result")
            if isinstance(result, str):
                text = result
            elif isinstance(result, dict) and result.get("status") == "stopped":
                return "unverifiable"
        if text is not None and text.startswith("[ERROR]"):
            lowered = text.lower()
            if "refused" in lowered or "not confirmed" in lowered:
                return "refused"
            if "timed out" in lowered or "timeout" in lowered:
                return "timeout_after_possible_dispatch"
            return "unverifiable"
    return "confirmed"


def _status_for(raw_status: str) -> str:
    if raw_status == "confirmed":
        return "executed"
    if raw_status == "refused":
        return "refused"
    return "uncertain"


class ActionReporter:
    """Emits one protocol action event per attempted tool call."""

    def __init__(self, emitter: Emitter, run_start: float):
        self._emitter = emitter
        self._run_start = run_start
        self._index = 0

    async def on_computer_call_end(
        self, item: dict[str, Any], result: list[dict[str, Any]]
    ) -> None:
        raw_status = classify_result(result)
        self._emitter.emit(
            {
                "type": "action",
                "index": self._index,
                "tool": str(item.get("name") or "unknown").lower(),
                "status": _status_for(raw_status),
                "raw_status": raw_status,
                "delivery_mode": "foreground",
                "route": "pixel",
                "refusal_code": "driver_refused" if raw_status == "refused" else None,
                "elapsed_ms": max(0, int((time.monotonic() - self._run_start) * 1000)),
            }
        )
        self._index += 1


class RunGuard:
    """Bounds the run: model-step cap, wall-clock deadline, app liveness.

    Deliberately never stops the very first iteration — the loop's
    ``on_run_end`` references a variable bound inside its body, so breaking
    before step one raises inside the library. The supervisor's process-group
    deadline still bounds the pathological case.
    """

    def __init__(
        self,
        max_steps: int,
        deadline: float,
        *,
        cli: DriverCLI | None = None,
        app: str | None = None,
        start_url: str | None = None,
        target: dict[str, Any] | None = None,
    ):
        self.max_steps = max_steps
        self.deadline = deadline
        self.steps = 0
        self.limit_reached = False
        self.deadline_reached = False
        self.target_crashed = False
        self.recovery_attempts = 0
        self._cli = cli
        self._app = app
        self._start_url = start_url
        self._target = target

    async def on_run_continue(self, _kwargs: dict, _old_items: list, _new_items: list) -> bool:
        if self.steps > 0:
            if self.steps >= self.max_steps:
                self.limit_reached = True
                return False
            if time.monotonic() >= self.deadline:
                self.deadline_reached = True
                return False
            if not await self._target_alive():
                return False
        self.steps += 1
        return True

    async def _target_alive(self) -> bool:
        if self._target is None:
            return True
        try:
            os.kill(self._target["pid"], 0)
            return True
        except ProcessLookupError:
            pass
        except OSError:
            return True
        # The launched app died mid-run. Relaunch it so the next observation
        # shows a fresh instance; give up after two recoveries so a
        # crash-looping app cannot consume the whole budget.
        while self.recovery_attempts < MAX_APP_RECOVERY_ATTEMPTS:
            self.recovery_attempts += 1
            try:
                assert self._cli is not None and self._app is not None
                self._target = await prepare_app(self._cli, self._app, self._start_url)
                return True
            except DriverError:
                continue
        self.target_crashed = True
        return False


def _texts_from_items(items: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for item in items:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


def _strip_final_markers(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    for marker in FINAL_TEXT_MARKERS:
        if stripped.startswith(marker):
            stripped = stripped[len(marker) :].strip()
    return stripped or None


async def _collect_run(agent: Any, messages: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    async for response in agent.run(messages, stream=False):
        items.extend(response.get("output") or [])
    return items


async def _summarize_limit_run(
    request: dict[str, Any],
    api_key: str,
    desktop: CuaDriverDesktop,
    items: list[dict[str, Any]],
    deadline: float,
) -> str | None:
    """Best-effort closing summary once the step cap is hit.

    The previous runner made one extra, uncounted model call so a limited run
    still reports what it accomplished. A fresh agent replays the collected
    trajectory plus the stop instruction; a denying confirmation callback
    keeps any further tool call from touching the desktop.
    """
    from cua_agent import ComputerAgent

    async def deny(_request: dict[str, Any]) -> bool:
        return False

    agent = ComputerAgent(
        model=f"yutori/{request['model']}",
        tools=[desktop],
        api_key=api_key,
        api_base=request["api_base_url"],
        callbacks=[RunGuard(1, deadline)],
        telemetry_enabled=False,
        action_confirmation_callback=deny,
        tool_set=TOOL_SET,
    )
    messages = (
        [{"role": "user", "content": request["task"]}]
        + items
        + [{"role": "user", "content": STOP_SUMMARY_PROMPT}]
    )
    summary_items = await _collect_run(agent, messages)
    texts = _texts_from_items(summary_items)
    return texts[-1] if texts else None


async def run_request(request: dict[str, Any], emitter: Emitter, api_key: str) -> str:
    """Execute one run request and emit its result. Returns the outcome."""
    from cua_agent import ComputerAgent

    run_start = time.monotonic()
    remaining_seconds = request["deadline_ms"] / 1000 - time.time()
    if remaining_seconds <= 0:
        emitter.emit(
            {
                "type": "result",
                "outcome": "limit",
                "delivery_mode": "foreground",
                "final_text": "The deadline expired before the run started.",
                "elapsed_ms": 0,
                "steps": 0,
            }
        )
        return "limit"
    deadline = run_start + remaining_seconds

    cli = DriverCLI(request["driver_path"])
    target: dict[str, Any] | None = None
    if request["app"]:
        target = await prepare_app(cli, request["app"], request["start_url"])

    guard = RunGuard(
        request["max_steps"],
        deadline,
        cli=cli,
        app=request["app"],
        start_url=request["start_url"],
        target=target,
    )
    reporter = ActionReporter(emitter, run_start)
    async with CuaDriverDesktop(cli) as desktop:
        agent = ComputerAgent(
            model=f"yutori/{request['model']}",
            tools=[desktop],
            api_key=api_key,
            api_base=request["api_base_url"],
            callbacks=[guard, reporter],
            instructions=SYSTEM_CONTEXT,
            telemetry_enabled=False,
            tool_set=TOOL_SET,
            # Truncates in-flight batch execution at the wall clock; the
            # step-level deadline lives in the guard.
            execution_deadline=deadline,
        )
        items = await _collect_run(agent, request["task"])
        texts = _texts_from_items(items)
        final_text = _strip_final_markers(texts[-1] if texts else None)

        if guard.target_crashed:
            outcome = "target_crashed"
            final_text = "The target app exited and could not be recovered."
        elif guard.limit_reached or guard.deadline_reached:
            outcome = "limit"
            if guard.limit_reached:
                try:
                    final_text = (
                        await _summarize_limit_run(
                            request, api_key, desktop, items, deadline
                        )
                        or final_text
                    )
                except Exception as error:  # noqa: BLE001 - the summary is a nicety
                    print(f"limit summary failed: {error}", file=sys.stderr)
        else:
            outcome = "completed"

    emitter.emit(
        {
            "type": "result",
            "outcome": outcome,
            "delivery_mode": "foreground",
            "final_text": final_text,
            "elapsed_ms": max(0, int((time.monotonic() - run_start) * 1000)),
            "steps": guard.steps,
            "target_recovery_attempts": guard.recovery_attempts,
        }
    )
    return outcome


def _claim_protocol_stream() -> TextIO:
    """Own the real stdout for events; route everything else to stderr.

    Duplicated at the fd level so that library prints, C extensions, and
    inherited descriptors in grandchildren all land on stderr — the parent
    treats stdout as JSONL and stderr as diagnostics.
    """
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", buffering=1)


def _read_request_line() -> str:
    data = sys.stdin.read()
    lines = [line for line in data.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RequestError(
            "INVALID_REQUEST", "Expected exactly one JSONL request line."
        )
    return lines[0]


def main() -> int:
    emitter = Emitter(_claim_protocol_stream())
    emitter.emit(
        {
            "type": "ready",
            "protocol_version": PROTOCOL_VERSION,
            "package_version": _package_version(),
            "driver_version_pinned": DRIVER_VERSION,
            "observation_format": "jpeg",
            "observation_format_fallback": False,
            "reasoning_overlay_requested": False,
        }
    )
    try:
        line = _read_request_line()
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            raise RequestError("INVALID_JSON", "Request was not valid JSON.") from None
        request = parse_request(payload)
    except RequestError as error:
        emitter.emit({"type": "error", "code": error.code, "message": str(error)})
        return 1
    api_key = os.environ.get("YUTORI_API_KEY")
    if not api_key:
        emitter.emit(
            {
                "type": "error",
                "code": "MISSING_API_KEY",
                "message": "YUTORI_API_KEY is not set in the runner environment.",
            }
        )
        return 1
    try:
        outcome = asyncio.run(run_request(request, emitter, api_key))
    except Exception as error:  # noqa: BLE001 - the wire carries the failure
        message = str(error).replace(api_key, "[REDACTED]") or type(error).__name__
        emitter.emit(
            {
                "type": "result",
                "outcome": "failed",
                "delivery_mode": "foreground",
                "final_text": message,
                "steps": 0,
            }
        )
        return 1
    return 0 if outcome in {"completed", "limit"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
