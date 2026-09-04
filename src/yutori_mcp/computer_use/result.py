from __future__ import annotations

from typing import Any

from .constants import DELIVERY_MODE_BACKGROUND, DELIVERY_MODE_FOREGROUND

REDACTED = "[REDACTED]"


def redact(text: str, secret: str) -> str:
    """Scrub ``secret`` out of ``text`` before it can reach the protocol stream or logs.

    Shared by the runner (exception text) and the supervisor (stderr diagnostics and
    protocol stdout lines) so the child's API key never survives past this one point.
    """
    return text.replace(secret, REDACTED)


def _seconds(ms: Any) -> str:
    return f"{ms / 1000:.1f}s" if isinstance(ms, (int, float)) else "?"


def _phase(label: str, total_ms: Any, count: Any, unit: str) -> str:
    text = f"{label} {_seconds(total_ms)}"
    if isinstance(count, int) and count > 0:
        text += f" over {count} {unit}"
        if isinstance(total_ms, (int, float)):
            text += f" ({total_ms / count / 1000:.1f}s avg)"
    return text


def format_perf(result: dict[str, Any]) -> list[str]:
    """Render the phase breakdown when the runner supplies one."""
    elapsed_ms = result.get("elapsed_ms")
    steps = result.get("steps")
    if not isinstance(elapsed_ms, (int, float)) or not isinstance(steps, int):
        return []
    headline = f"Perf: total {_seconds(elapsed_ms)} over {steps} model turns"
    if steps > 0:
        headline += f" ({elapsed_ms / steps / 1000:.1f}s/turn)"
    lines = [headline]
    timings = result.get("timings")
    if isinstance(timings, dict):
        lines.append(
            "  "
            + " | ".join(
                [
                    _phase(
                        "model",
                        timings.get("model_ms"),
                        timings.get("model_calls"),
                        "calls",
                    ),
                    _phase(
                        "actions",
                        timings.get("action_ms"),
                        timings.get("tool_calls"),
                        "tool calls",
                    ),
                    _phase(
                        "screenshots",
                        timings.get("screenshot_ms"),
                        timings.get("screenshots"),
                        "captures",
                    ),
                    f"settle {_seconds(timings.get('settle_ms'))}",
                    f"polling {_seconds(timings.get('polling_ms'))}",
                    f"shell {_seconds(timings.get('shell_ms'))}",
                    f"other {_seconds(timings.get('other_ms'))}",
                ]
            )
        )
    return lines


def describe_delivery_surface(mode: str, app: str | None) -> str:
    """What a "ready" event is driving, phrased for the CLI and MCP progress renderers.

    Both callers announce this identically ("driving the desktop" vs. "driving the {app}
    window in the background") when a run starts, so the phrase lives here once rather than
    as two independently-typed string literals that could drift.
    """
    return f"the {app} window in the background" if mode == DELIVERY_MODE_BACKGROUND else "the desktop"


def format_action_line(event: dict[str, Any], *, index_default: Any = None) -> str:
    """The "action #N: tool -> status" prefix shared by the CLI and MCP progress renderers.

    Only the part that is byte-identical between the two callers lives here; each caller
    appends its own elapsed-time and command formatting, which intentionally differ (a
    multi-line indented command preview for a terminal vs. a single-line MCP log message).
    """
    index = event.get("index", index_default)
    line = f"action #{index}: {event.get('tool')} -> {event.get('status')}"
    if event.get("refusal_code"):
        line += f" ({event['refusal_code']})"
    if event.get("escalated"):
        line += " [fronted]"
    if event.get("duration_ms") is not None:
        line += f" took {event['duration_ms']} ms"
    return line


def _window_target_line(target: Any) -> str | None:
    if not isinstance(target, dict) or target.get("pid") is None:
        return None
    label = target.get("app_name") or target.get("title") or "window"
    return f"Window target: {label} (pid {target['pid']}, window {target.get('window_id')})"


def format_result(result: dict[str, Any]) -> str:
    lines = [
        f"Outcome: {result.get('outcome', 'failed')}",
        f"Delivery mode: {result.get('delivery_mode', DELIVERY_MODE_FOREGROUND)}",
    ]
    if result.get("run_url"):
        lines.append(f"Run: {result['run_url']}")
    if (window_target := _window_target_line(result.get("window_target"))) is not None:
        lines.append(window_target)
    if result.get("final_text"):
        lines.append(f"Final text: {result['final_text']}")
    if result.get("elapsed_ms") is not None:
        lines.append(f"Elapsed: {result['elapsed_ms']} ms")
    if result.get("reasoning_overlay_requested"):
        state = "active" if result.get("reasoning_overlay_effective") else "unavailable"
        # Background runs show a menu bar item (latest frame + Stop) instead of the overlay.
        # Either way the run also offers the activity window, opened from that menu.
        surface = "Menu bar status" if result.get("delivery_mode") == DELIVERY_MODE_BACKGROUND else "Reasoning overlay"
        lines.append(f"{surface}: {state}; codec: {result.get('codec') or 'unknown'}")
    escalations = result.get("fallback_escalations") or 0
    skips = result.get("fallback_skips") or 0
    refusals = result.get("background_refusals") or 0
    if result.get("delivery_mode") == DELIVERY_MODE_BACKGROUND or escalations or skips or refusals:
        line = f"Delivery: {escalations} foreground escalation(s), {refusals} background refusal(s)"
        if skips:
            # Foreground retries the SDK withheld because the window had already changed.
            line += f", {skips} retry(ies) skipped after the window changed"
        lines.append(line)
    if result.get("preview_frames"):
        lines.append(f"Activity window: {result['preview_frames']} frame(s) streamed while it was open")
    lines.extend(format_perf(result))
    actions = result.get("actions", [])
    if actions:
        lines.append("Actions:")
        for action in actions:
            line = (
                "- #{index} {tool}: {status} (raw: {raw_status}; mode: {delivery_mode}; "
                "route: {route}; refusal: {refusal_code})".format(**action)
            )
            if action.get("effect"):
                line += f"; effect: {action['effect']}"
            if action.get("escalated"):
                line += " [fronted]"
            if action.get("duration_ms") is not None:
                line += f" took {action['duration_ms']} ms"
            if action.get("command"):
                line += f" $ {action['command']}"
            lines.append(line)
    return "\n".join(lines)


def terminal_result(
    outcome: str,
    message: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    delivery_mode: str = DELIVERY_MODE_FOREGROUND,
) -> dict[str, Any]:
    """Build the supervisor-side terminal result dict every caller returns to the host.

    Single source of truth for this shape. The supervisor synthesizes one of these
    whenever it has to conclude a run itself rather than relay the runner's own
    terminal event — a failed protocol exchange (``failure`` below), an expired
    deadline (``limit``), or a cancelled task (``aborted``) — and all three carried
    hand-written copies of the same four keys, so a shape change had to be made in
    three places at once.
    """
    return {
        "outcome": outcome,
        "delivery_mode": delivery_mode,
        "final_text": message,
        "actions": actions or [],
    }


def failure(
    message: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    delivery_mode: str = DELIVERY_MODE_FOREGROUND,
) -> dict[str, Any]:
    """The terminal result for a run that could not be carried out."""
    return terminal_result("failed", message, actions=actions, delivery_mode=delivery_mode)
