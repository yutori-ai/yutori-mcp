"""Isolated protocol-v1 child for the SDK-owned macOS computer-use runtime."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from importlib import metadata
from typing import Any, TextIO

from yutori.navigator import N2ComputerAgent
from yutori.navigator.macos import (
    MacOSComputer,
    MacOSPresentationStatus,
    MacOSTargetCrashedError,
    CancellationLatch,
    ShellPresentationEvent,
    sanitize_command_preview,
)

from .app import prepare_app
from .constants import (
    DELIVERY_MODE_FOREGROUND,
    DRIVER_VERSION,
    PROTOCOL_VERSION,
    SDK_ARTIFACT_SHA256,
    SDK_PROVENANCE_SHA256,
    SDK_VERSION,
    TOOL_SET,
)
from .result import redact

SYSTEM_CONTEXT = (
    "You control the entire macOS screen. This is macOS, not Linux: do not use "
    "Ubuntu or Linux UI conventions. Use macOS conventions: cmd, not ctrl, for "
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
    "screenshot to confirm it before you summarize. Shell commands run headlessly "
    "in bash as the logged-in user; do not use sudo. Do not use osascript or shell "
    "commands to inspect or control GUI applications because macOS Automation consent "
    "can block them indefinitely. Use screenshots and computer actions for GUI work "
    "and visual verification. Never inspect a GUI application's databases, containers, "
    "caches, or private data stores through the shell. Unless the user explicitly "
    "requested exhaustive research, use at most three shell calls for research, combine "
    "related lookups, and then begin the GUI work without further shell research. "
    "Determine sign-in state only from visible UI; never inspect browser profile databases, "
    "cookies, login data, history, Keychain, or other credential stores. When a visible "
    "sign-in or reauthentication challenge blocks the task, stop immediately instead of "
    "trying alternate URLs, accounts, or sign-in methods, and ask the user to sign in "
    "themselves. Never ask them to give you a password, passkey, or verification code. "
    "Do not install software or packages unless the user explicitly requested installation.\n\n"
    "Do not open or change System Settings, application settings, accounts, "
    "permissions, or defaults unless the user explicitly asked for that settings change."
)
STOP_SUMMARY_PROMPT = "Stop here. Briefly summarize what you accomplished and what you found."
FINAL_TEXT_MARKERS = ("[DONE]", "[INFEASIBLE]")
_SHELL_TOOL_NAMES = frozenset({"bash", "shell_command", "run_command"})
_BACKGROUND_TASK_PATTERN = re.compile(r"\bStarted background task ([A-Za-z0-9-]+)\b")


class RequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_string(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value:
        raise RequestError("INVALID_REQUEST", f"{field} must be a non-empty string.")
    return value


def _require_optional_string(request: dict[str, Any], field: str) -> str | None:
    value = request.get(field)
    if value is not None and (not isinstance(value, str) or not value):
        raise RequestError("INVALID_REQUEST", f"{field} must be a non-empty string or null.")
    return value


def _require_positive_int(request: dict[str, Any], field: str) -> int:
    value = request.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RequestError("INVALID_REQUEST", f"{field} must be a positive integer.")
    return value


def parse_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestError("INVALID_REQUEST", "Request must be a JSON object.")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise RequestError("UNSUPPORTED_PROTOCOL_VERSION", f"Expected protocol_version {PROTOCOL_VERSION}.")
    if payload.get("type") != "run":
        raise RequestError("INVALID_REQUEST", "type must be 'run'.")
    task = _require_string(payload, "task")
    app = _require_optional_string(payload, "app")
    start_url = _require_optional_string(payload, "start_url")
    if start_url is not None and app is None:
        raise RequestError("INVALID_REQUEST", "start_url requires app.")
    deadline_ms = _require_positive_int(payload, "deadline_ms")
    max_steps = _require_positive_int(payload, "max_steps")
    return {
        "task": task,
        "app": app,
        "start_url": start_url,
        "deadline_ms": deadline_ms,
        "max_steps": max_steps,
        "model": _require_string(payload, "model"),
        "api_base_url": _require_string(payload, "api_base_url"),
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


def _arguments(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("arguments")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def shell_command_preview(item: dict[str, Any]) -> str | None:
    if str(item.get("name") or "").lower() not in _SHELL_TOOL_NAMES:
        return None
    command = _arguments(item).get("command")
    return sanitize_command_preview(command) if isinstance(command, str) and command.strip() else None


def _background_task_id(outputs: list[dict[str, Any]] | None) -> str | None:
    for frame in outputs or []:
        output = frame.get("output")
        result = output.get("result") if isinstance(output, dict) else output
        if isinstance(result, str) and (match := _BACKGROUND_TASK_PATTERN.search(result)):
            return match.group(1)
    return None


class ActionReporter:
    """Emit one privacy-safe action event for each attempted top-level tool call."""

    def __init__(
        self,
        emitter: Emitter,
        run_start: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        chat: ChatTracker | None = None,
    ) -> None:
        self._emitter = emitter
        self._run_start = run_start
        self._clock = clock
        self._chat = chat
        self._index = 0
        self._call_start: float | None = None
        self._pending_item: dict[str, Any] | None = None
        self.tool_calls = 0

    async def on_computer_call_start(self, item: dict[str, Any]) -> None:
        self._call_start = self._clock()
        self._pending_item = item

    async def on_computer_call_end(self, item: dict[str, Any], result: list[dict[str, Any]]) -> None:
        self._emit(item, classify_result(result), result)

    def flush_interrupted(self) -> None:
        if self._pending_item is not None:
            self._emit(self._pending_item, "interrupted", [])

    def _emit(self, item: dict[str, Any], raw_status: str, result: list[dict[str, Any]]) -> None:
        duration_ms = None
        if self._call_start is not None:
            duration_ms = max(0, round((self._clock() - self._call_start) * 1000))
        self._call_start = None
        self._pending_item = None
        arguments = _arguments(item)
        run_in_background = bool(
            str(item.get("name") or "").lower() == "bash" and arguments.get("run_in_background") is True
        )
        self._emitter.emit(
            {
                "type": "action",
                "index": self._index,
                "tool": str(item.get("name") or "unknown").lower(),
                "status": _status_for(raw_status),
                "raw_status": raw_status,
                "delivery_mode": DELIVERY_MODE_FOREGROUND,
                "route": "pixel",
                "refusal_code": "driver_refused" if raw_status == "refused" else None,
                "elapsed_ms": max(0, round((self._clock() - self._run_start) * 1000)),
                "duration_ms": duration_ms,
                "command": shell_command_preview(item),
                "run_in_background": run_in_background,
                "background_task_id": _background_task_id(result) if run_in_background else None,
                # Carried on every action so the supervisor can still link the run when it
                # has to conclude the run itself and never sees the runner's result event.
                "chat_id": self._chat.chat_id if self._chat is not None else None,
            }
        )
        self._index += 1
        self.tool_calls += 1


class ApiCounter:
    def __init__(self) -> None:
        self.calls = 0

    async def on_api_start(self, _kwargs: dict[str, Any]) -> None:
        self.calls += 1


class ChatTracker:
    """Remember the platform's identity for this run: the first model call's request_id.

    The SDK echoes each response's ``request_id`` back as ``prev_request_id``, and the
    platform files every call in that chain under the first one, so the first response
    names the chat page the run appears on.
    """

    def __init__(self) -> None:
        self.chat_id: str | None = None

    async def on_api_end(self, _kwargs: dict[str, Any], response: Any) -> None:
        if self.chat_id is not None:
            return
        if hasattr(response, "model_dump"):
            response = response.model_dump()
        request_id = response.get("request_id") if isinstance(response, dict) else None
        if isinstance(request_id, str) and request_id:
            self.chat_id = request_id


class RunGuard:
    """Bound model turns independently of in-flight SDK cancellation."""

    def __init__(self, max_steps: int, deadline: float) -> None:
        self.max_steps = max_steps
        self.deadline = deadline
        self.steps = 0
        self.limit_reached = False
        self.deadline_reached = False

    async def on_run_continue(self, _kwargs: dict, _old_items: list, _new_items: list) -> bool:
        if time.monotonic() >= self.deadline:
            self.deadline_reached = True
            return False
        if self.steps >= self.max_steps:
            self.limit_reached = True
            return False
        self.steps += 1
        return True


def _texts_from_items(items: list[dict[str, Any]]) -> list[str]:
    return [
        part["text"]
        for item in items
        if item.get("type") == "message"
        for part in item.get("content") or []
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]


def _strip_final_markers(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    for marker in FINAL_TEXT_MARKERS:
        if stripped.startswith(marker):
            stripped = stripped[len(marker) :].strip()
        if stripped.endswith(marker):
            stripped = stripped[: -len(marker)].strip()
    return stripped or None


def _redacted_error_text(error: BaseException, secret: str) -> str:
    """Render an exception as protocol-safe text with the API key scrubbed out.

    Falls back to the exception's type name when str(error) is empty (e.g. a bare
    `RuntimeError()`), so the caller always reports something readable.
    """
    return redact(str(error), secret) or type(error).__name__


async def _collect_run(agent: Any, messages: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    async for response in agent.run(messages):
        items.extend(response.get("output") or [])
    return items


async def _summarize_limit_run(
    request: dict[str, Any],
    api_key: str,
    computer: MacOSComputer,
    items: list[dict[str, Any]],
    deadline: float,
) -> str | None:
    async def deny(_request: dict[str, Any]) -> bool:
        return False

    messages = [
        {"role": "user", "content": request["task"]},
        *items,
        {"role": "user", "content": STOP_SUMMARY_PROMPT},
    ]
    async with N2ComputerAgent(
        computer=computer,
        tool_set=TOOL_SET,
        api_key=api_key,
        base_url=request["api_base_url"],
        model=request["model"],
        callbacks=[RunGuard(1, deadline)],
        action_confirmation_callback=deny,
        presentation=computer.presentation,
        supports_click_modifiers=True,
        screenshot_delay=0,
        execution_deadline=deadline,
    ) as agent:
        summary_items = await _collect_run(agent, messages)
    texts = _texts_from_items(summary_items)
    return _strip_final_markers(texts[-1] if texts else None)


def _background_counts(events: tuple[ShellPresentationEvent, ...]) -> dict[str, int]:
    counts = {state: 0 for state in ("started", "completed", "failed", "cancelled")}
    for event in events:
        if not event.run_in_background:
            continue
        if event.state == "running":
            counts["started"] += 1
        elif event.state == "timed_out":
            counts["failed"] += 1
        elif event.state in counts:
            counts[event.state] += 1
    return counts


def _timings_payload(
    total_ms: int,
    agent: Any,
    api_counter: ApiCounter,
    reporter: ActionReporter,
    computer: MacOSComputer,
) -> dict[str, int]:
    sdk = computer.timings
    model_ms = round(getattr(agent, "timings", {}).get("model_ms", 0)) if agent is not None else 0
    capture_ms = sdk.get("capture_ms", 0)
    encode_ms = sdk.get("encode_ms", 0)
    polling_ms = sdk.get("polling_ms", 0)
    shell_ms = sdk.get("shell_ms", 0)
    action_ms = sdk.get("action_ms", 0)
    accounted = model_ms + action_ms + capture_ms + encode_ms + polling_ms + shell_ms
    return {
        "total_ms": total_ms,
        "model_ms": model_ms,
        "model_calls": api_counter.calls,
        "action_ms": action_ms,
        "tool_calls": reporter.tool_calls,
        "screenshot_ms": capture_ms + encode_ms,
        "screenshots": sdk.get("screenshots", 0),
        "capture_ms": capture_ms,
        "encode_ms": encode_ms,
        "settle_ms": 0,
        "polling_ms": polling_ms,
        "shell_ms": shell_ms,
        "other_ms": max(0, total_ms - accounted),
    }


def _presentation_payload(computer: MacOSComputer, status: MacOSPresentationStatus) -> dict[str, Any]:
    codec = status.codec
    if codec is None and computer.current_observation is not None:
        codec = computer.current_observation.media_type.rsplit("/", 1)[-1]
    telemetry = list(computer.presentation.telemetry) if computer.presentation is not None else []
    return {
        "reasoning_overlay_requested": True,
        "reasoning_overlay_effective": status.available,
        "presentation": asdict(status),
        "presentation_telemetry": telemetry,
        "codec": codec,
        "observation_format": codec,
        "observation_format_fallback": codec == "jpeg",
        "target_recovery_attempts": computer.target_recovery_attempts,
        "no_progress_triggers": computer.no_progress_triggers,
        "shell_events": [asdict(event) for event in computer.shell_events],
        "background_command_counts": _background_counts(computer.shell_events),
    }


def _cancelled_outcome(cause: str | None) -> tuple[str, str]:
    if cause == "target_crash":
        return "target_crashed", "The target app exited and could not be recovered."
    if cause == "deadline":
        return "limit", "The absolute deadline expired."
    if cause == "transport_failure":
        return "failed", "The CuaDriver transport failed."
    return "aborted", "The computer-use run was stopped."


def _error_event(code: str, message: str) -> dict[str, Any]:
    """The two keys every runner-to-supervisor "error" event shares.

    `main()` reports a malformed request and a missing API key at two different points before
    the run ever starts, but both must still agree on this shape -- the same one
    `supervisor._event_shape_error()` validates on the other side of the pipe.
    """
    return {"type": "error", "code": code, "message": message}


def _result_event(outcome: str, final_text: str | None) -> dict[str, Any]:
    """The four keys every runner-to-supervisor "result" event shares.

    A deadline that expires before the run starts, a normal run outcome, and an unexpected
    top-level exception in ``main()`` each terminate at a different point with different
    additional data available (elapsed_ms/steps, timings, presentation state), but all three
    must still agree on this core shape -- the same one `supervisor._event_shape_error()`
    validates on the other side of the pipe.
    """
    return {
        "type": "result",
        "outcome": outcome,
        "delivery_mode": DELIVERY_MODE_FOREGROUND,
        "final_text": final_text,
    }


async def run_request(
    request: dict[str, Any],
    emitter: Emitter,
    api_key: str,
    cancellation: CancellationLatch | None = None,
) -> str:
    run_start = time.monotonic()
    remaining_seconds = request["deadline_ms"] / 1000 - time.time()
    if remaining_seconds <= 0:
        emitter.emit(
            {
                **_result_event("limit", "The deadline expired before the run started."),
                "elapsed_ms": 0,
                "steps": 0,
            }
        )
        return "limit"

    deadline = run_start + remaining_seconds
    guard = RunGuard(request["max_steps"], deadline)
    chat = ChatTracker()
    reporter = ActionReporter(emitter, run_start, chat=chat)
    api_counter = ApiCounter()
    computer = MacOSComputer(
        presentation=True,
        allow_local_shell=True,
        execution_deadline=deadline,
        cancellation=cancellation or CancellationLatch(),
        known_secrets=(api_key,),
    )
    agent: Any = None
    outcome = "failed"
    final_text: str | None = None
    try:
        await computer.__aenter__()
        if request["app"]:
            # Consume the pre-launch frame captured during session startup so
            # the first model observation is guaranteed to show the target.
            await computer.screenshot()
            target = await prepare_app(computer, request["app"], request["start_url"])
            computer.target_pid = target["pid"]

            async def recover_target() -> int | None:
                recovered = await prepare_app(computer, request["app"], request["start_url"])
                return recovered["pid"]

            computer.recover_target = recover_target

        async with N2ComputerAgent(
            computer=computer,
            tool_set=TOOL_SET,
            api_key=api_key,
            base_url=request["api_base_url"],
            model=request["model"],
            callbacks=[guard, reporter, api_counter, chat],
            instructions=SYSTEM_CONTEXT,
            presentation=computer.presentation,
            screenshot_delay=0,
            execution_deadline=deadline,
            supports_click_modifiers=True,
        ) as agent:
            items = await _collect_run(agent, request["task"])
        texts = _texts_from_items(items)
        final_text = _strip_final_markers(texts[-1] if texts else None)
        if guard.limit_reached or guard.deadline_reached:
            outcome = "limit"
            if guard.limit_reached:
                try:
                    final_text = await _summarize_limit_run(request, api_key, computer, items, deadline) or final_text
                except Exception as error:  # noqa: BLE001 - final summary is best effort
                    print(f"limit summary failed: {error}", file=sys.stderr)
        else:
            outcome = "completed"
    except asyncio.CancelledError as error:
        cause = computer.cancellation.cause or (str(error) if str(error) else None)
        outcome, final_text = _cancelled_outcome(cause)
    except MacOSTargetCrashedError:
        outcome, final_text = _cancelled_outcome("target_crash")
    except Exception as error:  # noqa: BLE001 - the protocol carries a redacted failure
        outcome = "failed"
        final_text = _redacted_error_text(error, api_key)
    finally:
        status = computer.presentation_status
        try:
            await computer.aclose()
        except Exception as error:  # noqa: BLE001 - preserve the primary outcome
            if outcome == "completed":
                outcome = "failed"
                final_text = _redacted_error_text(error, api_key)

    reporter.flush_interrupted()
    elapsed_ms = max(0, round((time.monotonic() - run_start) * 1000))
    emitter.emit(
        {
            **_result_event(outcome, final_text),
            "elapsed_ms": elapsed_ms,
            "steps": guard.steps,
            "chat_id": chat.chat_id,
            "timings": _timings_payload(elapsed_ms, agent, api_counter, reporter, computer),
            **_presentation_payload(computer, status),
        }
    )
    return outcome


def _claim_protocol_stream() -> TextIO:
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", buffering=1)


def _read_request_line() -> str:
    lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
    if len(lines) != 1:
        raise RequestError("INVALID_REQUEST", "Expected exactly one JSONL request line.")
    return lines[0]


def _take_api_key() -> str | None:
    """Move the API key out of the environment before model-owned shells exist."""
    return os.environ.pop("YUTORI_API_KEY", None)


async def _run_until_terminated(request: dict[str, Any], emitter: Emitter, api_key: str) -> str:
    """Turn supervisor SIGTERM into SDK cancellation so teardown can reap shells."""
    cancellation = CancellationLatch()
    task = asyncio.create_task(run_request(request, emitter, api_key, cancellation))
    loop = asyncio.get_running_loop()
    terminating = False

    def terminate() -> None:
        nonlocal terminating
        if terminating:
            return
        terminating = True
        cancellation.request("supervisor")
        task.cancel("supervisor")

    loop.add_signal_handler(signal.SIGTERM, terminate)
    try:
        return await task
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main() -> int:
    api_key = _take_api_key()
    emitter = Emitter(_claim_protocol_stream())
    emitter.emit(
        {
            "type": "ready",
            "protocol_version": PROTOCOL_VERSION,
            "package_version": _package_version(),
            "sdk_version": SDK_VERSION,
            "sdk_artifact_sha256": SDK_ARTIFACT_SHA256,
            "sdk_provenance_sha256": SDK_PROVENANCE_SHA256,
            "driver_version_pinned": DRIVER_VERSION,
            "observation_format": "webp",
            "observation_format_fallback": True,
            "observation_fallback_format": "jpeg",
            "reasoning_overlay_requested": True,
        }
    )
    try:
        try:
            payload = json.loads(_read_request_line())
        except json.JSONDecodeError:
            raise RequestError("INVALID_JSON", "Request was not valid JSON.") from None
        request = parse_request(payload)
    except RequestError as error:
        emitter.emit(_error_event(error.code, str(error)))
        return 1
    if not api_key:
        emitter.emit(_error_event("MISSING_API_KEY", "YUTORI_API_KEY is not set in the runner environment."))
        return 1
    try:
        outcome = asyncio.run(_run_until_terminated(request, emitter, api_key))
    except Exception as error:  # noqa: BLE001 - last-resort protocol boundary
        message = _redacted_error_text(error, api_key)
        emitter.emit(
            {
                **_result_event("failed", message),
                "steps": 0,
            }
        )
        return 1
    return 0 if outcome in {"completed", "limit", "aborted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
