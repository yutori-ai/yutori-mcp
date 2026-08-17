from __future__ import annotations

from typing import Any


def format_result(result: dict[str, Any]) -> str:
    lines = [
        f"Outcome: {result.get('outcome', 'failed')}",
        f"Delivery mode: {result.get('delivery_mode', 'foreground')}",
    ]
    if result.get("final_text"):
        lines.append(f"Final text: {result['final_text']}")
    if result.get("elapsed_ms") is not None:
        lines.append(f"Elapsed: {result['elapsed_ms']} ms")
    actions = result.get("actions", [])
    if actions:
        lines.append("Actions:")
        for action in actions:
            lines.append(
                "- #{index} {tool}: {status} (raw: {raw_status}; mode: {delivery_mode}; "
                "route: {route}; refusal: {refusal_code})".format(**action)
            )
    return "\n".join(lines)


def failure(
    message: str, *, actions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "outcome": "failed",
        "delivery_mode": "foreground",
        "final_text": message,
        "actions": actions or [],
    }
