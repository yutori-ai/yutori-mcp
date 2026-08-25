from __future__ import annotations

from typing import Any


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
    headline = f"Perf: total {_seconds(elapsed_ms)} over {steps} steps"
    if steps > 0:
        headline += f" ({elapsed_ms / steps / 1000:.1f}s/step)"
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


def format_result(result: dict[str, Any]) -> str:
    lines = [
        f"Outcome: {result.get('outcome', 'failed')}",
        f"Delivery mode: {result.get('delivery_mode', 'foreground')}",
    ]
    if result.get("final_text"):
        lines.append(f"Final text: {result['final_text']}")
    if result.get("elapsed_ms") is not None:
        lines.append(f"Elapsed: {result['elapsed_ms']} ms")
    if result.get("reasoning_overlay_requested"):
        state = "active" if result.get("reasoning_overlay_effective") else "unavailable"
        lines.append(f"Reasoning overlay: {state}; codec: {result.get('codec') or 'unknown'}")
    lines.extend(format_perf(result))
    actions = result.get("actions", [])
    if actions:
        lines.append("Actions:")
        for action in actions:
            line = (
                "- #{index} {tool}: {status} (raw: {raw_status}; mode: {delivery_mode}; "
                "route: {route}; refusal: {refusal_code})".format(**action)
            )
            if action.get("duration_ms") is not None:
                line += f" took {action['duration_ms']} ms"
            if action.get("command"):
                line += f" $ {action['command']}"
            lines.append(line)
    return "\n".join(lines)


def failure(message: str, *, actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "outcome": "failed",
        "delivery_mode": "foreground",
        "final_text": message,
        "actions": actions or [],
    }
