"""JSON Schema utilities for MCP tool definitions."""

from __future__ import annotations

from typing import Any


def _is_null_schema(v: Any) -> bool:
    return isinstance(v, dict) and v.get("type") == "null"


def simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Simplify JSON Schema for MCP clients by flattening anyOf with null.

    Pydantic generates `anyOf: [{type: X}, {type: null}]` for optional fields.
    MCP clients don't always understand this, showing "unknown" for types.
    This function converts such patterns to just `{type: X}` while preserving
    other properties like default, description, minimum, maximum, enum, etc.
    """
    if not isinstance(schema, dict):
        return schema

    result = {}
    for key, value in schema.items():
        if key == "anyOf" and isinstance(value, list) and len(value) == 2:
            # Check if this is the pattern: [{actual_type}, {type: null}]
            non_null = [v for v in value if not _is_null_schema(v)]
            null_types = [v for v in value if _is_null_schema(v)]

            if len(non_null) == 1 and len(null_types) == 1:
                # Flatten: merge the non-null type's properties into result
                for k, v in simplify_schema(non_null[0]).items():
                    result[k] = v
                continue

        # Recursively simplify nested structures
        if isinstance(value, dict):
            result[key] = simplify_schema(value)
        elif isinstance(value, list):
            result[key] = [
                simplify_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def get_simplified_schema(model: type) -> dict[str, Any]:
    """Get a simplified JSON Schema from a Pydantic model."""
    return simplify_schema(model.model_json_schema())


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
