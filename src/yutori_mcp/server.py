"""Yutori MCP Server - Web monitoring and browsing automation."""

from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .client import YutoriAPIError, YutoriClient
from .schemas import (
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
    ResearchTaskInput,
    ScoutIdInput,
    TaskIdInput,
)

logger = logging.getLogger(__name__)

# Tool definitions with annotations
TOOLS = [
    # Read operations
    Tool(
        name="list_scouts",
        description=(
            "List all scouts for the authenticated user. "
            "Returns basic metadata; use get_scout_detail for full fields."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
        annotations={"readOnlyHint": True},
    ),
    Tool(
        name="get_scout_detail",
        description="Get detailed information about a specific scout.",
        inputSchema=ScoutIdInput.model_json_schema(),
        annotations={"readOnlyHint": True},
    ),
    Tool(
        name="get_scout_updates",
        description="Get paginated updates/reports for a scout. Each update contains findings from a run.",
        inputSchema=GetUpdatesInput.model_json_schema(),
        annotations={"readOnlyHint": True},
    ),
    # Scout lifecycle
    Tool(
        name="create_scout",
        description=(
            "Create a monitoring scout for continuous web monitoring. Scouts track changes relevant to "
            "a query and alert you. Examples: 'news about Yutori', 'H100 pricing below $1.50'."
        ),
        inputSchema=CreateScoutInput.model_json_schema(),
    ),
    Tool(
        name="edit_scout",
        description=(
            "Update an existing scout's query, schedule, webhook configuration, or status. "
            "Use status='paused' to pause, 'active' to resume, or 'done' to archive."
        ),
        inputSchema=EditScoutInput.model_json_schema(),
        annotations={"idempotentHint": True},
    ),
    Tool(
        name="delete_scout",
        description="Permanently delete a scout and all its data. This action cannot be undone.",
        inputSchema=ScoutIdInput.model_json_schema(),
        annotations={"destructiveHint": True},
    ),
    # Browsing operations
    Tool(
        name="run_browsing_task",
        description=(
            "Execute a one-time web browsing task. The navigator agent runs a cloud browser and "
            "operates it like a person. Returns a task_id for polling. Example: 'list employees'."
        ),
        inputSchema=BrowsingTaskInput.model_json_schema(),
    ),
    Tool(
        name="get_browsing_task_result",
        description="Poll for browsing task status and result. Call until status is 'succeeded' or 'failed'.",
        inputSchema=TaskIdInput.model_json_schema(),
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
        inputSchema=ResearchTaskInput.model_json_schema(),
    ),
    Tool(
        name="get_research_task_result",
        description="Poll for research task status and result. Call until status is 'succeeded' or 'failed'.",
        inputSchema=TaskIdInput.model_json_schema(),
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
                result = _handle_tool(client, name, arguments)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except YutoriAPIError as e:
            return [TextContent(type="text", text=f"API Error ({e.status_code}): {e.message}")]
        except Exception as e:
            logger.exception(f"Error handling tool {name}")
            return [TextContent(type="text", text=f"Error: {e!s}")]

    return server


def _handle_tool(client: YutoriClient, name: str, arguments: dict) -> dict:
    """Route tool calls to the appropriate client method."""
    match name:
        # Read operations
        case "list_scouts":
            return client.list_scouts()
        case "get_scout_detail":
            params = ScoutIdInput(**arguments)
            return client.get_scout_detail(params.scout_id)
        case "get_scout_updates":
            params = GetUpdatesInput(**arguments)
            return client.get_scout_updates(
                scout_id=params.scout_id,
                cursor=params.cursor,
                limit=params.limit,
            )

        # Scout lifecycle
        case "create_scout":
            params = CreateScoutInput(**arguments)
            return client.create_scout(
                query=params.query,
                output_interval=params.output_interval,
                webhook_url=params.webhook_url,
                webhook_format=params.webhook_format,
                task_spec=params.task_spec,
                user_timezone=params.user_timezone,
                skip_email=params.skip_email,
                start_timestamp=params.start_timestamp,
                user_location=params.user_location,
                is_public=params.is_public,
            )
        case "edit_scout":
            params = EditScoutInput(**arguments)

            # Apply config updates first (so they take effect before status change)
            # Use `is not None` to correctly handle False values (e.g., is_public=False)
            has_config_updates = any(f is not None for f in [
                params.query,
                params.output_interval,
                params.webhook_url,
                params.webhook_format,
                params.task_spec,
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
                    task_spec=params.task_spec,
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

            # Return updated scout details
            return client.get_scout_detail(params.scout_id)
        case "delete_scout":
            params = ScoutIdInput(**arguments)
            return client.delete_scout(params.scout_id)

        # Browsing operations
        case "run_browsing_task":
            params = BrowsingTaskInput(**arguments)
            return client.run_browsing_task(
                task=params.task,
                start_url=params.start_url,
                max_steps=params.max_steps,
                task_spec=params.task_spec,
                webhook_url=params.webhook_url,
                webhook_format=params.webhook_format,
            )
        case "get_browsing_task_result":
            params = TaskIdInput(**arguments)
            return client.get_browsing_task(params.task_id)

        # Research operations
        case "run_research_task":
            params = ResearchTaskInput(**arguments)
            return client.run_research_task(
                query=params.query,
                user_timezone=params.user_timezone,
                user_location=params.user_location,
                task_spec=params.task_spec,
                webhook_url=params.webhook_url,
                webhook_format=params.webhook_format,
            )
        case "get_research_task_result":
            params = TaskIdInput(**arguments)
            return client.get_research_task(params.task_id)

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
