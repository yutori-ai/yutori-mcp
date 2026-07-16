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


def output_schema_field_names(schema: dict[str, Any]) -> list[str] | None:
    """Extract field names from a schema shaped like ``output_fields_to_output_schema()`` produces.

    Inverse of ``output_fields_to_output_schema``: accepts either the
    array-of-objects shape it builds (properties nested under ``items``) or a
    bare top-level object schema (properties at the top level). Returns None
    if ``schema`` doesn't match either shape (e.g. a custom JSON Schema set
    via the REST API directly, such as tuple-form ``items`` or an object
    schema with no/empty ``properties``).

    Args:
        schema: A dict, typically the ``output_schema`` field of a scout.

    Returns:
        The list of field names in insertion order, or None if the shape
        doesn't match.
    """
    items = schema.get("items")
    container = items if isinstance(items, dict) else schema
    properties = container.get("properties")
    if isinstance(properties, dict) and properties:
        return list(properties)
    return None
