"""JSON Schema utilities for MCP tool definitions."""

from __future__ import annotations

from typing import Any


def output_fields_to_output_schema(
    output_fields: list[str] | None,
) -> dict[str, Any] | None:
    """Convert simple output_fields list to JSON Schema for output_schema parameter.

    Args:
        output_fields: List of field names, e.g. ['headline', 'summary', 'url']

    Returns:
        JSON Schema dict for API output_schema parameter, or None if output_fields is None.
    """
    if output_fields is None:
        return None
    if not output_fields:
        raise ValueError("output_fields must contain at least one field name")

    properties = {field: {"type": "string"} for field in output_fields}

    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
        },
    }
