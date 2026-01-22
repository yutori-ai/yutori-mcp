"""Yutori MCP Server - Web monitoring and browsing automation."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .client import YutoriAPIError, YutoriClient
from .formatters import format_response
from .schemas import (
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
    ListScoutsInput,
    ResearchTaskInput,
    ScoutIdInput,
    TaskIdInput,
)

logger = logging.getLogger(__name__)


def _simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
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
            non_null = [v for v in value if not (isinstance(v, dict) and v.get("type") == "null")]
            null_types = [v for v in value if isinstance(v, dict) and v.get("type") == "null"]

            if len(non_null) == 1 and len(null_types) == 1:
                # Flatten: merge the non-null type's properties into result
                for k, v in _simplify_schema(non_null[0]).items():
                    result[k] = v
                continue

        # Recursively simplify nested structures
        if isinstance(value, dict):
            result[key] = _simplify_schema(value)
        elif isinstance(value, list):
            result[key] = [_simplify_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value

    return result


def _get_simplified_schema(model: type) -> dict[str, Any]:
    """Get a simplified JSON Schema from a Pydantic model."""
    return _simplify_schema(model.model_json_schema())


def _output_fields_to_task_spec(output_fields: list[str] | None) -> dict[str, Any] | None:
    """Convert simple output_fields list to full task_spec JSON Schema.

    Args:
        output_fields: List of field names, e.g. ['headline', 'summary', 'url']

    Returns:
        Full task_spec dict for API, or None if output_fields is None.
    """
    if output_fields is None:
        return None

    properties = {field: {"type": "string"} for field in output_fields}

    return {
        "output_schema": {
            "type": "json",
            "json_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": properties,
                },
            },
        },
    }

# Tool definitions with annotations
TOOLS = [
    # Read operations
    Tool(
        name="list_scouts",
        description=(
            "List all scouts for the authenticated user. "
            "Returns basic metadata; use get_scout_detail for full fields."
        ),
        inputSchema=_get_simplified_schema(ListScoutsInput),
        annotations={"readOnlyHint": True},
    ),
    Tool(
        name="get_scout_detail",
        description="Get detailed information about a specific scout.",
        inputSchema=_get_simplified_schema(ScoutIdInput),
        annotations={"readOnlyHint": True},
    ),
    Tool(
        name="get_scout_updates",
        description="Get paginated updates/reports for a scout. Each update contains findings from a run.",
        inputSchema=_get_simplified_schema(GetUpdatesInput),
        annotations={"readOnlyHint": True},
    ),
    # Scout lifecycle
    Tool(
        name="create_scout",
        description=(
            "Create a monitoring scout for continuous web monitoring. Scouts track changes relevant to "
            "a query and alert you. Examples: 'news about Yutori', 'H100 pricing below $1.50'."
        ),
        inputSchema=_get_simplified_schema(CreateScoutInput),
    ),
    Tool(
        name="edit_scout",
        description=(
            "Update an existing scout's query, schedule, webhook configuration, or status. "
            "Use status='paused' to pause, 'active' to resume, or 'done' to archive."
        ),
        inputSchema=_get_simplified_schema(EditScoutInput),
        annotations={"idempotentHint": True},
    ),
    Tool(
        name="delete_scout",
        description="Permanently delete a scout and all its data. This action cannot be undone.",
        inputSchema=_get_simplified_schema(ScoutIdInput),
        annotations={"destructiveHint": True},
    ),
    # Browsing operations
    Tool(
        name="run_browsing_task",
        description=(
            "Execute a one-time web browsing task. The navigator agent runs a cloud browser and "
            "operates it like a person. Returns a task_id for polling. Example: 'list employees'."
        ),
        inputSchema=_get_simplified_schema(BrowsingTaskInput),
    ),
    Tool(
        name="get_browsing_task_result",
        description="Poll for browsing task status and result. Call until status is 'succeeded' or 'failed'.",
        inputSchema=_get_simplified_schema(TaskIdInput),
        annotations={"readOnlyHint": True},
    ),
    # Research operations
    Tool(
        name="run_research_task",
        description=(
            "Execute a one-time deep web research task. The research agent searches, "
            "reads, and synthesizes information from across the web. Returns a task_id for polling. "
            "Example: 'latest AI startup funding announcements'."
        ),
        inputSchema=_get_simplified_schema(ResearchTaskInput),
    ),
    Tool(
        name="get_research_task_result",
        description="Poll for research task status and result. Call until status is 'succeeded' or 'failed'.",
        inputSchema=_get_simplified_schema(TaskIdInput),
        annotations={"readOnlyHint": True},
    ),
]


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("yutori-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            with YutoriClient() as client:
                result, context = _handle_tool(client, name, arguments)
                formatted = format_response(name, result, **context)
                return [TextContent(type="text", text=formatted)]
        except YutoriAPIError as e:
            return [TextContent(type="text", text=f"API Error ({e.status_code}): {e.message}")]
        except Exception as e:
            logger.exception(f"Error handling tool {name}")
            return [TextContent(type="text", text=f"Error: {e!s}")]

    return server


def _handle_tool(client: YutoriClient, name: str, arguments: dict) -> tuple[dict, dict]:
    """Route tool calls to the appropriate client method.

    Returns:
        Tuple of (result, context) where context contains extra info for formatting.
    """
    match name:
        # Read operations
        case "list_scouts":
            params = ListScoutsInput(**arguments)
            result = client.list_scouts(limit=params.limit, status=params.status)
            return result, {}
        case "get_scout_detail":
            params = ScoutIdInput(**arguments)
            return client.get_scout_detail(params.scout_id), {}
        case "get_scout_updates":
            params = GetUpdatesInput(**arguments)
            result = client.get_scout_updates(
                scout_id=params.scout_id,
                cursor=params.cursor,
                limit=params.limit,
            )
            return result, {}

        # Scout lifecycle
        case "create_scout":
            params = CreateScoutInput(**arguments)
            result = client.create_scout(
                query=params.query,
                output_interval=params.output_interval,
                webhook_url=params.webhook_url,
                webhook_format=params.webhook_format,
                task_spec=_output_fields_to_task_spec(params.output_fields),
                user_timezone=params.user_timezone,
                skip_email=params.skip_email,
                start_timestamp=params.start_timestamp,
                user_location=params.user_location,
                is_public=params.is_public,
            )
            return result, {}
        case "edit_scout":
            params = EditScoutInput(**arguments)

            # Fetch current state for diff (also validates scout exists)
            old_scout = client.get_scout_detail(params.scout_id)

            # Apply config updates (so they take effect before status change)
            has_config_updates = any(f is not None for f in [
                params.query,
                params.output_interval,
                params.webhook_url,
                params.webhook_format,
                params.output_fields,
                params.skip_email,
                params.user_timezone,
                params.user_location,
                params.is_public,
            ])

            if has_config_updates:
                client.edit_scout(
                    scout_id=params.scout_id,
                    query=params.query,
                    output_interval=params.output_interval,
                    webhook_url=params.webhook_url,
                    webhook_format=params.webhook_format,
                    task_spec=_output_fields_to_task_spec(params.output_fields),
                    skip_email=params.skip_email,
                    user_timezone=params.user_timezone,
                    user_location=params.user_location,
                    is_public=params.is_public,
                )

            # Apply status change after config updates
            if params.status == "paused":
                client.pause_scout(params.scout_id)
            elif params.status == "active":
                client.resume_scout(params.scout_id)
            elif params.status == "done":
                client.complete_scout(params.scout_id)

            # Return old and new state for diff
            new_scout = client.get_scout_detail(params.scout_id)
            return {"old": old_scout, "new": new_scout}, {}
        case "delete_scout":
            params = ScoutIdInput(**arguments)
            result = client.delete_scout(params.scout_id)
            return result, {"scout_id": params.scout_id}

        # Browsing operations
        case "run_browsing_task":
            params = BrowsingTaskInput(**arguments)
            result = client.run_browsing_task(
                task=params.task,
                start_url=params.start_url,
                max_steps=params.max_steps,
                task_spec=_output_fields_to_task_spec(params.output_fields),
                webhook_url=params.webhook_url,
                webhook_format=params.webhook_format,
            )
            return result, {"task_type": "Browsing"}
        case "get_browsing_task_result":
            params = TaskIdInput(**arguments)
            return client.get_browsing_task(params.task_id), {"task_type": "Browsing"}

        # Research operations
        case "run_research_task":
            params = ResearchTaskInput(**arguments)
            result = client.run_research_task(
                query=params.query,
                user_timezone=params.user_timezone,
                user_location=params.user_location,
                task_spec=_output_fields_to_task_spec(params.output_fields),
                webhook_url=params.webhook_url,
                webhook_format=params.webhook_format,
            )
            return result, {"task_type": "Research"}
        case "get_research_task_result":
            params = TaskIdInput(**arguments)
            return client.get_research_task(params.task_id), {"task_type": "Research"}

        case _:
            raise ValueError(f"Unknown tool: {name}")


async def run_server() -> None:
    """Run the MCP server using stdio transport."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Entry point for the yutori-mcp command."""
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
