"""The n2 computer-use agent loop against a local macOS app.

Port of the monorepo's playground-core computer-use loop: the observation is a
window screenshot, actions execute through cua-driver, and the model is
Yutori's n2 computer-use preview served over the OpenAI-compatible
``/v1/chat/completions``. The screenshot the model reacts to is delivered as
the tool_result of each action (the standard computer-use pattern).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .cua import CuaCliError, daemon_running, launch_app, pick_best_window
from .driver import CuaComputerUseDriver

DEFAULT_API_BASE = "https://api.dev.yutori.com/v1"
API_BASE_ENV = "YUTORI_N2_API_BASE"
MODEL = "n2-preview"
TOOL_SET = "computer_use_tools-20260708"
TEMPERATURE = 0
MAX_STEPS = 60
REQUEST_TIMEOUT_SECONDS = 180
# The n2 route rejects request bodies over 8 MiB; leave headroom for the
# non-message fields when pruning screenshot history.
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
REQUEST_BODY_HEADROOM_BYTES = 64 * 1024

_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


class ComputerUseError(RuntimeError):
    """The computer-use task could not run (environment or API failure)."""


@dataclass
class ComputerUseResult:
    outcome: str  # completed | limit
    final_text: str
    app_name: str
    pid: int
    steps: list[str] = field(default_factory=list)


def _task_preamble(task: str, app_name: str) -> str:
    now = datetime.now(timezone.utc)
    return (
        f"{task}\n\n"
        f"Current date (UTC): {now.date().isoformat()}\n"
        f"Current time (UTC): {now.strftime('%H:%M:%S')}\n\n"
        "Guidance:\n"
        f'- You control the "{app_name}" application window on a macOS desktop. '
        "A screenshot of the window is attached; act on what you see, giving "
        "click/scroll targets as [x, y] in a normalized 0-1000 space (origin top-left).\n"
        "- Use macOS conventions: cmd instead of ctrl for shortcuts (cmd+c, cmd+v, cmd+a, ...).\n"
        "- Take a screenshot action whenever you need a fresh view after the screen changes."
    )


def _image_part(data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}}


def prune_screenshots(messages: list[dict], max_bytes: int) -> int:
    """Drop the oldest image parts until the serialized payload fits.

    The model only needs the current frame (the server trims to the most
    recent image before inference); earlier frames ride along for the server's
    replay log, so they're the first to go. The newest image is always kept.
    Mutates ``messages``; returns how many images were dropped.
    """

    def size() -> int:
        return len(json.dumps(messages).encode())

    if size() <= max_bytes:
        return 0
    image_holders = [
        message["content"]
        for message in messages
        if isinstance(message.get("content"), list)
        and any(part.get("type") == "image_url" for part in message["content"])
    ]
    dropped = 0
    for content in image_holders[:-1]:
        if size() <= max_bytes:
            break
        for i, part in enumerate(content):
            if part.get("type") == "image_url":
                content[i] = {"type": "text", "text": "[Earlier screenshot omitted.]"}
                dropped += 1
                break
    return dropped


def _call_n2(
    client: httpx.Client,
    api_base: str,
    api_key: str,
    messages: list[dict],
    prev_request_id: str | None,
) -> dict:
    payload: dict = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        "tool_set": TOOL_SET,
        "parallel_tool_calls": False,
    }
    if prev_request_id:
        payload["prev_request_id"] = prev_request_id
    body = json.dumps(payload).encode()
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise ComputerUseError(
            f"request exceeds the {MAX_REQUEST_BODY_BYTES}-byte n2 payload limit"
        )
    response = client.post(
        f"{api_base}/chat/completions",
        content=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise ComputerUseError(
            f"n2 API error {response.status_code}: {response.text[:400]}"
        )
    data = response.json()
    # Billing/admission rejections arrive as HTTP 200 with an OpenAI-style
    # error body, so a status check alone doesn't catch them.
    if isinstance(data.get("error"), dict):
        message = data["error"].get("message") or json.dumps(data["error"])[:300]
        raise ComputerUseError(f"n2 API error: {message}")
    return data


def run_computer_use_task(
    task: str,
    app: str,
    api_key: str,
    minutes: float = 3,
    start_url: str | None = None,
    keep_ctrl: bool = False,
    api_base: str | None = None,
) -> ComputerUseResult:
    """Drive a macOS app with the n2 computer-use model until it finishes,
    hits the step cap, or exhausts the time budget."""
    if not daemon_running():
        raise ComputerUseError(
            "cua-driver daemon is not running. Start it with: "
            "open -n -g -a CuaDriver --args serve"
        )

    api_base = (api_base or os.getenv(API_BASE_ENV) or DEFAULT_API_BASE).rstrip("/")
    urls = [start_url] if start_url else None
    # Reverse-DNS shape reads as a bundle id; display names can legally
    # contain dots, so a failed bundle-id launch retries as a name.
    if _BUNDLE_ID_RE.match(app):
        try:
            launched = launch_app(bundle_id=app, urls=urls)
        except CuaCliError:
            launched = launch_app(name=app, urls=urls)
    else:
        launched = launch_app(name=app, urls=urls)
    window = pick_best_window(launched.windows)
    if window is None:
        raise ComputerUseError(
            f"{launched.name} (pid {launched.pid}) has no windows to drive"
        )

    driver = CuaComputerUseDriver(
        pid=launched.pid, window_id=window.window_id, ctrl_to_cmd=not keep_ctrl
    )
    deadline = time.monotonic() + minutes * 60
    steps: list[str] = []
    prev_request_id: str | None = None

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _task_preamble(task, launched.name)},
                _image_part(driver.screenshot()),
            ],
        }
    ]

    def result(outcome: str, final_text: str) -> ComputerUseResult:
        return ComputerUseResult(
            outcome=outcome,
            final_text=final_text,
            app_name=launched.name,
            pid=launched.pid,
            steps=steps,
        )

    with httpx.Client() as client:
        def call_model() -> tuple[dict, str | None]:
            prune_screenshots(
                messages, MAX_REQUEST_BODY_BYTES - REQUEST_BODY_HEADROOM_BYTES
            )
            data = _call_n2(client, api_base, api_key, messages, prev_request_id)
            choices = data.get("choices") or []
            message = (choices[0] or {}).get("message") if choices else None
            if not isinstance(message, dict):
                raise ComputerUseError("n2 API returned no assistant message")
            return message, data.get("request_id")

        for _ in range(MAX_STEPS):
            if time.monotonic() >= deadline:
                return result("limit", "Time budget reached before the task finished.")

            assistant, request_id = call_model()
            prev_request_id = request_id or prev_request_id
            tool_calls = assistant.get("tool_calls") or []
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )

            if not tool_calls:
                final = assistant.get("content") or "Done."
                return result("completed", final)
            if assistant.get("content"):
                steps.append(f"[thinking] {assistant['content']}")

            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                action_result = driver.execute(
                    function.get("name") or "", function.get("arguments") or "{}"
                )
                steps.append(f"[{function.get('name')}] {action_result}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id") or "",
                        "content": [
                            {"type": "text", "text": action_result},
                            _image_part(driver.screenshot()),
                        ],
                    }
                )

        # Step cap reached: ask for a closing summary.
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Stop here. Briefly summarize what you accomplished and what you found.",
                    }
                ],
            }
        )
        summary, _ = call_model()
        return result(
            "limit",
            summary.get("content") or "Reached the step limit for this task.",
        )
