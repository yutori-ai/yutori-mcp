"""Yutori MCP Server - Web monitoring and browsing automation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, NoReturn

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pydantic import BaseModel

from . import __version__
from .adapter import MCPClientAdapter, YutoriAPIError
from .formatters import format_response
from .schema_utils import get_simplified_schema, output_fields_to_output_schema
from .schemas import (
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
    ListScoutsInput,
    ResearchTaskInput,
    ScoutIdInput,
    TaskIdInput,
    UsageInput,
)

logger = logging.getLogger(__name__)


# Tool definitions with annotations
TOOLS = [
    # Usage
    Tool(
        name="list_api_usage",
        description=(
            "Get API usage statistics including active scout counts, rate limits, and activity metrics."
        ),
        inputSchema=get_simplified_schema(UsageInput),
        annotations={"readOnlyHint": True},
    ),
    # Read operations
    Tool(
        name="list_scouts",
        description=(
            "List all scouts for the authenticated user. "
            "Returns basic metadata; use get_scout_detail for full fields."
        ),
        inputSchema=get_simplified_schema(ListScoutsInput),
        annotations={"readOnlyHint": True},
    ),
    Tool(
        name="get_scout_detail",
        description="Get detailed information about a specific scout.",
        inputSchema=get_simplified_schema(ScoutIdInput),
        annotations={"readOnlyHint": True},
    ),
    Tool(
        name="get_scout_updates",
        description="Get paginated updates/reports for a scout. Each update contains findings from a run.",
        inputSchema=get_simplified_schema(GetUpdatesInput),
        annotations={"readOnlyHint": True},
    ),
    # Scout lifecycle
    Tool(
        name="create_scout",
        description=(
            "Create a monitoring scout for continuous web monitoring. Scouts track changes relevant to "
            "a query and alert you. Examples: 'news about Yutori', 'H100 pricing below $1.50'."
        ),
        inputSchema=get_simplified_schema(CreateScoutInput),
    ),
    Tool(
        name="edit_scout",
        description=(
            "Update an existing scout's query, schedule, webhook configuration, or status. "
            "Use status='paused' to pause, 'active' to resume, or 'done' to archive."
        ),
        inputSchema=get_simplified_schema(EditScoutInput),
        annotations={"idempotentHint": True},
    ),
    Tool(
        name="delete_scout",
        description="Permanently delete a scout and all its data. This action cannot be undone.",
        inputSchema=get_simplified_schema(ScoutIdInput),
        annotations={"destructiveHint": True},
    ),
    # Browsing operations
    Tool(
        name="run_browsing_task",
        description=(
            "Execute a one-time web browsing task. The navigator agent runs a browser and "
            "operates it like a person. Returns a task_id for polling. Example: 'list employees'. "
            "Set browser='local' to use the desktop app with the user's logged-in sessions."
        ),
        inputSchema=get_simplified_schema(BrowsingTaskInput),
    ),
    Tool(
        name="get_browsing_task_result",
        description="Poll for browsing task status and result. Call until status is 'succeeded' or 'failed'.",
        inputSchema=get_simplified_schema(TaskIdInput),
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
        inputSchema=get_simplified_schema(ResearchTaskInput),
    ),
    Tool(
        name="get_research_task_result",
        description="Poll for research task status and result. Call until status is 'succeeded' or 'failed'.",
        inputSchema=get_simplified_schema(TaskIdInput),
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
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            with MCPClientAdapter() as client:
                result, context = _handle_tool(client, name, arguments)
                formatted = format_response(name, result, **context)
                return [TextContent(type="text", text=formatted)]
        except YutoriAPIError as e:
            return [
                TextContent(
                    type="text", text=f"API Error ({e.status_code}): {e.message}"
                )
            ]
        except Exception as e:
            logger.exception(f"Error handling tool {name}")
            return [TextContent(type="text", text=f"Error: {e!s}")]

    return server


# Signature for tool handlers registered in _TOOL_HANDLERS.
ToolHandler = Callable[
    [MCPClientAdapter, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]
]


def _handle_tool(
    client: MCPClientAdapter, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Route tool calls to the appropriate handler.

    Returns:
        Tuple of (result, context) where context contains extra info for formatting.
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    return handler(client, arguments)


def _scout_kwargs(
    params: BaseModel, extra_exclude: set[str] | None = None
) -> dict[str, Any]:
    """Convert a Pydantic input model into adapter kwargs.

    Drops None fields (matches adapter._strip_none centralization) and
    transforms `output_fields` into the API's `output_schema` format.
    Callers can pass `extra_exclude` to drop additional fields (e.g. control
    fields that are not part of the scout config payload).
    """
    exclude = {"output_fields"} | (extra_exclude or set())
    kwargs = params.model_dump(exclude=exclude, exclude_none=True)
    if getattr(params, "output_fields", None) is not None:
        kwargs["output_schema"] = output_fields_to_output_schema(params.output_fields)
    return kwargs


# -----------------------------------------------------------------------------
# Per-tool handlers. Each handler parses arguments via its input schema, calls
# the appropriate adapter method, and returns (result, context) for the
# formatter registry in formatters.py.
# -----------------------------------------------------------------------------


def _handle_list_api_usage(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = UsageInput(**arguments)
    result = client.get_usage(period=params.period)
    return result, {}


def _handle_list_scouts(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = ListScoutsInput(**arguments)
    return client.list_scouts(**_scout_kwargs(params)), {}


def _handle_get_scout_detail(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = ScoutIdInput(**arguments)
    return client.get_scout_detail(params.scout_id), {}


def _handle_get_scout_updates(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = GetUpdatesInput(**arguments)
    return client.get_scout_updates(**_scout_kwargs(params)), {}


def _handle_create_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = CreateScoutInput(**arguments)
    return client.create_scout(**_scout_kwargs(params)), {}


def _handle_edit_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = EditScoutInput(**arguments)

    # Fetch current state for diff (also validates scout exists)
    old_scout = client.get_scout_detail(params.scout_id)

    # Build config kwargs, excluding control fields (scout_id is passed
    # explicitly below; status is applied separately after config updates).
    config_kwargs = _scout_kwargs(params, extra_exclude={"scout_id", "status"})

    if config_kwargs:
        client.edit_scout(scout_id=params.scout_id, **config_kwargs)

    # Apply status change after config updates
    if params.status is not None:
        client.edit_scout(scout_id=params.scout_id, status=params.status)

    # Return old and new state for diff
    new_scout = client.get_scout_detail(params.scout_id)
    return {"old": old_scout, "new": new_scout}, {}


def _handle_delete_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = ScoutIdInput(**arguments)
    result = client.delete_scout(params.scout_id)
    return result, {"scout_id": params.scout_id}


def _handle_run_browsing_task(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = BrowsingTaskInput(**arguments)
    return client.run_browsing_task(**_scout_kwargs(params)), {
        "task_type": "Browsing",
        "browser": params.browser,
    }


def _handle_run_research_task(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = ResearchTaskInput(**arguments)
    return client.run_research_task(**_scout_kwargs(params)), {
        "task_type": "Research",
    }


def _make_get_task_result_handler(task_type: str, client_method: str) -> ToolHandler:
    """Build a ``get_*_task_result`` handler that defers only by adapter method + task_type.

    The browsing and research variants previously had two near-identical
    handlers; both parse :class:`TaskIdInput`, call a single-argument
    adapter method, and stamp the matching ``task_type`` into the formatter
    context. Generating both from one factory keeps the registry as the
    single place that pairs the tool name with its task type.
    """

    def handler(
        client: MCPClientAdapter, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        params = TaskIdInput(**arguments)
        return getattr(client, client_method)(params.task_id), {"task_type": task_type}

    return handler


# Tool-name -> handler registry, consulted by _handle_tool() above. Mirrors
# the _TOOL_FORMATTERS registry in formatters.py so the parse/dispatch side
# of the MCP tool lifecycle is structured the same way as the format side.
_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "list_api_usage": _handle_list_api_usage,
    "list_scouts": _handle_list_scouts,
    "get_scout_detail": _handle_get_scout_detail,
    "get_scout_updates": _handle_get_scout_updates,
    "create_scout": _handle_create_scout,
    "edit_scout": _handle_edit_scout,
    "delete_scout": _handle_delete_scout,
    "run_browsing_task": _handle_run_browsing_task,
    "get_browsing_task_result": _make_get_task_result_handler("Browsing", "get_browsing_task"),
    "run_research_task": _handle_run_research_task,
    "get_research_task_result": _make_get_task_result_handler("Research", "get_research_task"),
}


async def run_server() -> None:
    """Run the MCP server using stdio transport."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


# CLI auth subcommand name -> argparse `help` text. Iterated over to register
# subparsers and consulted to decide whether to dispatch to _handle_auth_command.
_AUTH_SUBCOMMANDS: dict[str, str] = {
    "login": "Log in and save API key",
    "logout": "Remove saved API key",
    "status": "Show authentication status",
}


def _handle_auth_command(command: str) -> NoReturn:
    """Run an auth subcommand (login/logout/status) and exit.

    Imports ``yutori.auth`` lazily so the default ``yutori-mcp`` server-startup
    path does not pay the auth-flow import cost. Always raises ``SystemExit``;
    the ``NoReturn`` annotation lets ``main()`` rely on that contract instead
    of guarding the call with a ``return``.
    """
    from yutori.auth import clear_config, get_auth_status, run_login_flow

    if command == "login":
        result = run_login_flow(key_source="yutori-mcp")
        if result.success:
            print("Successfully authenticated!")
        else:
            print(f"Authentication failed: {result.error}")
            if result.auth_url:
                print(f"\nIf the browser didn't open, visit:\n  {result.auth_url}")
        raise SystemExit(0 if result.success else 1)

    if command == "logout":
        clear_config()
        print("Logged out successfully.")
        raise SystemExit(0)

    if command == "status":
        status = get_auth_status()
        if status.authenticated:
            print(f"Authenticated (API key: {status.masked_key})")
            if status.source == "config_file":
                print(f"  Source: {status.config_path}")
            elif status.source == "env_var":
                print("  Source: YUTORI_API_KEY environment variable")
        else:
            print("Not authenticated. Run 'uvx yutori-mcp login' to authenticate.")
            raise SystemExit(1)
        raise SystemExit(0)

    # Defensive: every name in _AUTH_SUBCOMMANDS must have a branch above.
    # Reaching here means the dispatch table and this helper drifted apart.
    raise ValueError(f"Unhandled auth subcommand: {command!r}")


def main() -> None:
    """Entry point for the yutori-mcp command."""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(prog="yutori-mcp")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in _AUTH_SUBCOMMANDS.items():
        subparsers.add_parser(name, help=help_text)

    args = parser.parse_args()

    if args.command in _AUTH_SUBCOMMANDS:
        _handle_auth_command(args.command)

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
