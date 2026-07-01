"""Yutori MCP Server - Web monitoring and browsing automation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, NoReturn

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError

from . import __version__
from .adapter import MCPClientAdapter, YutoriAPIError
from .formatters import format_response
from .schema_utils import output_fields_to_output_schema
from .schemas import (
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
    ListScoutsInput,
    ListTasksInput,
    ResearchTaskInput,
    ScoutIdInput,
    TaskIdInput,
    UsageInput,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("yutori-mcp")


async def _invoke(tool_name: str, args: dict[str, Any]) -> str:
    """Invoke a tool handler and return the formatted response text.

    Centralizes error handling: YutoriAPIError → RuntimeError so the MCP
    framework marks the result isError=True; ValidationError re-raised as-is;
    unexpected exceptions logged and re-raised.
    """
    handler = _TOOL_HANDLERS[tool_name]
    try:
        async with MCPClientAdapter() as client:
            result, context = await handler(client, args)
        return format_response(tool_name, result, **context)
    except YutoriAPIError as e:
        raise RuntimeError(f"API Error ({e.status_code}): {e.message}") from e
    except ValidationError:
        raise
    except Exception:
        logger.exception(f"Error handling tool {tool_name}")
        raise


# ---------------------------------------------------------------------------
# Tool definitions. Each @mcp.tool function is a thin wrapper that collects
# its typed parameters into a plain dict and delegates to _invoke, which
# routes through _TOOL_HANDLERS (unchanged from the prior Server-based
# implementation).
# ---------------------------------------------------------------------------

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_IDEMPOTENT = ToolAnnotations(idempotentHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)


@mcp.tool(
    description=(
        "Get API usage statistics including active scout counts, rate limits, and activity metrics."
    ),
    annotations=_READ_ONLY,
)
async def list_api_usage(
    period: Literal["24h", "7d", "30d", "90d"] | None = None,
) -> str:
    return await _invoke("list_api_usage", {"period": period})


@mcp.tool(
    description=(
        "List all scouts for the authenticated user. "
        "Returns basic metadata; use get_scout_detail for full fields."
    ),
    annotations=_READ_ONLY,
)
async def list_scouts(
    limit: int | None = 10,
    status: Literal["active", "paused", "done"] | None = None,
    cursor: str | None = None,
) -> str:
    return await _invoke("list_scouts", {"limit": limit, "status": status, "cursor": cursor})


@mcp.tool(
    description="Get detailed information about a specific scout.",
    annotations=_READ_ONLY,
)
async def get_scout_detail(scout_id: str) -> str:
    return await _invoke("get_scout_detail", {"scout_id": scout_id})


@mcp.tool(
    description="Get paginated updates/reports for a scout. Each update contains findings from a run.",
    annotations=_READ_ONLY,
)
async def get_scout_updates(
    scout_id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> str:
    return await _invoke(
        "get_scout_updates", {"scout_id": scout_id, "cursor": cursor, "limit": limit}
    )


@mcp.tool(
    description=(
        "Create a monitoring scout for continuous web monitoring. Scouts track changes relevant to "
        "a query and alert you. Examples: 'news about Yutori', 'H100 pricing below $1.50'."
    ),
)
async def create_scout(
    query: str,
    output_interval: int | None = None,
    webhook_url: str | None = None,
    webhook_format: Literal["scout", "slack", "zapier"] | None = None,
    output_fields: list[str] | None = None,
    user_timezone: str | None = None,
    skip_email: bool | None = None,
    start_timestamp: int | None = None,
    user_location: str | None = None,
    is_public: bool | None = None,
) -> str:
    return await _invoke(
        "create_scout",
        {
            "query": query,
            "output_interval": output_interval,
            "webhook_url": webhook_url,
            "webhook_format": webhook_format,
            "output_fields": output_fields,
            "user_timezone": user_timezone,
            "skip_email": skip_email,
            "start_timestamp": start_timestamp,
            "user_location": user_location,
            "is_public": is_public,
        },
    )


@mcp.tool(
    description=(
        "Update an existing scout's query, schedule, webhook configuration, or status. "
        "Use status='paused' to pause, 'active' to resume, or 'done' to archive."
    ),
    annotations=_IDEMPOTENT,
)
async def edit_scout(
    scout_id: str,
    status: Literal["active", "paused", "done"] | None = None,
    query: str | None = None,
    output_interval: int | None = None,
    webhook_url: str | None = None,
    webhook_format: Literal["scout", "slack", "zapier"] | None = None,
    output_fields: list[str] | None = None,
    skip_email: bool | None = None,
    user_timezone: str | None = None,
    user_location: str | None = None,
    is_public: bool | None = None,
) -> str:
    return await _invoke(
        "edit_scout",
        {
            "scout_id": scout_id,
            "status": status,
            "query": query,
            "output_interval": output_interval,
            "webhook_url": webhook_url,
            "webhook_format": webhook_format,
            "output_fields": output_fields,
            "skip_email": skip_email,
            "user_timezone": user_timezone,
            "user_location": user_location,
            "is_public": is_public,
        },
    )


@mcp.tool(
    description="Permanently delete a scout and all its data. This action cannot be undone.",
    annotations=_DESTRUCTIVE,
)
async def delete_scout(scout_id: str) -> str:
    return await _invoke("delete_scout", {"scout_id": scout_id})


@mcp.tool(
    description=(
        "Execute a one-time web browsing task. The navigator agent runs a browser and "
        "operates it like a person. Returns a task_id for polling. Example: 'list employees'. "
        "Set browser='local' to use the desktop app with the user's logged-in sessions."
    ),
)
async def run_browsing_task(
    task: str,
    start_url: str,
    max_steps: int | None = None,
    require_auth: bool | None = None,
    browser: Literal["cloud", "local"] | None = None,
    output_fields: list[str] | None = None,
    webhook_url: str | None = None,
    webhook_format: Literal["scout", "slack", "zapier"] | None = None,
) -> str:
    return await _invoke(
        "run_browsing_task",
        {
            "task": task,
            "start_url": start_url,
            "max_steps": max_steps,
            "require_auth": require_auth,
            "browser": browser,
            "output_fields": output_fields,
            "webhook_url": webhook_url,
            "webhook_format": webhook_format,
        },
    )


@mcp.tool(
    description=(
        "List one-time browsing tasks for the authenticated user. "
        "Supports cursor pagination and status filtering. List status is approximate "
        "(running also covers queued and not-yet-reconciled tasks); call "
        "get_browsing_task_result for a task's authoritative status."
    ),
    annotations=_READ_ONLY,
)
async def list_browsing_tasks(
    limit: int | None = 10,
    status: Literal["running", "succeeded", "failed"] | None = None,
    cursor: str | None = None,
) -> str:
    return await _invoke(
        "list_browsing_tasks", {"limit": limit, "status": status, "cursor": cursor}
    )


@mcp.tool(
    description="Poll for browsing task status and result. Call until status is 'succeeded' or 'failed'.",
    annotations=_READ_ONLY,
)
async def get_browsing_task_result(task_id: str) -> str:
    return await _invoke("get_browsing_task_result", {"task_id": task_id})


@mcp.tool(
    description=(
        "Execute a one-time deep web research task. The research agent searches, "
        "reads, and synthesizes information from across the web. Returns a task_id for polling. "
        "Example: 'latest AI startup funding announcements'."
    ),
)
async def run_research_task(
    query: str,
    user_timezone: str | None = None,
    user_location: str | None = None,
    output_fields: list[str] | None = None,
    webhook_url: str | None = None,
    webhook_format: Literal["scout", "slack", "zapier"] | None = None,
) -> str:
    return await _invoke(
        "run_research_task",
        {
            "query": query,
            "user_timezone": user_timezone,
            "user_location": user_location,
            "output_fields": output_fields,
            "webhook_url": webhook_url,
            "webhook_format": webhook_format,
        },
    )


@mcp.tool(
    description=(
        "List one-time research tasks for the authenticated user. "
        "Supports cursor pagination and status filtering. List status is approximate "
        "(running also covers queued and not-yet-reconciled tasks); call "
        "get_research_task_result for a task's authoritative status."
    ),
    annotations=_READ_ONLY,
)
async def list_research_tasks(
    limit: int | None = 10,
    status: Literal["running", "succeeded", "failed"] | None = None,
    cursor: str | None = None,
) -> str:
    return await _invoke(
        "list_research_tasks", {"limit": limit, "status": status, "cursor": cursor}
    )


@mcp.tool(
    description="Poll for research task status and result. Call until status is 'succeeded' or 'failed'.",
    annotations=_READ_ONLY,
)
async def get_research_task_result(task_id: str) -> str:
    return await _invoke("get_research_task_result", {"task_id": task_id})


# Signature for tool handlers registered in _TOOL_HANDLERS.
ToolHandler = Callable[
    [MCPClientAdapter, dict[str, Any]],
    Awaitable[tuple[dict[str, Any], dict[str, Any]]],
]


def _scout_kwargs(
    params: BaseModel, extra_exclude: set[str] | None = None
) -> dict[str, Any]:
    """Convert a Pydantic input model into adapter kwargs.

    Drops None fields — _handle_edit_scout relies on the result being empty
    when no config fields were provided — and
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


async def _handle_list_api_usage(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = UsageInput(**arguments)
    result = await client.get_usage(period=params.period)
    return result, {}


def _make_model_kwargs_handler(
    input_class: type[BaseModel], client_method: str, context: dict[str, Any] | None = None
) -> ToolHandler:
    """Build a handler that forwards a simple input model as keyword arguments.

    ``context`` is the formatter context stamped onto the result (e.g.
    ``{"task_type": "Browsing"}`` for the list_*_tasks tools); it defaults to
    an empty context for tools whose formatter needs none.
    """

    async def handler(
        client: MCPClientAdapter, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        params = input_class(**arguments)
        return await getattr(client, client_method)(
            **params.model_dump(exclude_none=True)
        ), context or {}

    return handler


async def _handle_get_scout_detail(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = ScoutIdInput(**arguments)
    return await client.get_scout_detail(params.scout_id), {}


async def _handle_edit_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = EditScoutInput(**arguments)

    # Fetch current state for diff (also validates scout exists)
    old_scout = await client.get_scout_detail(params.scout_id)

    # Build config kwargs, excluding control fields (scout_id is passed
    # explicitly below; status is applied separately after config updates).
    config_kwargs = _scout_kwargs(params, extra_exclude={"scout_id", "status"})

    if config_kwargs:
        await client.edit_scout(scout_id=params.scout_id, **config_kwargs)

    # Apply status change after config updates. The API requires status and
    # config to be updated in separate calls, so the operation is not atomic:
    # if the status call fails after a config update succeeded, say so
    # explicitly instead of reporting a plain failure.
    if params.status is not None:
        try:
            await client.edit_scout(scout_id=params.scout_id, status=params.status)
        except YutoriAPIError as e:
            if config_kwargs:
                raise YutoriAPIError(
                    message=(
                        "Scout config changes were applied, but the status "
                        f"change to '{params.status}' failed: {e.message}. "
                        "Retry with edit_scout(scout_id, status=...) only."
                    ),
                    status_code=e.status_code,
                ) from e
            raise

    # Return old and new state for diff. Every mutation already succeeded at
    # this point, so a failed read-back must not surface as a failed edit —
    # the formatter renders a success message without the diff instead.
    try:
        new_scout = await client.get_scout_detail(params.scout_id)
    except YutoriAPIError:
        new_scout = {}
    return {"old": old_scout, "new": new_scout}, {}


async def _handle_delete_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = ScoutIdInput(**arguments)
    result = await client.delete_scout(params.scout_id)
    return result, {"scout_id": params.scout_id}


def _make_run_task_handler(
    task_type: str | None,
    input_class: type[BaseModel],
    client_method: str,
    include_browser: bool = False,
) -> ToolHandler:
    """Build a handler that parses an input schema and forwards it as scout kwargs.

    Shared shape for tools whose handler body is the one-liner
    ``client.METHOD(**_scout_kwargs(params))``: parse a schema, call the
    adapter method, and stamp the matching ``task_type`` into the formatter
    context. ``task_type=None`` (used for ``create_scout``, which has no
    task-type concept) omits the context entry entirely rather than stamping
    a null value. ``include_browser=True`` additionally surfaces
    ``params.browser`` so ``format_task_started`` can annotate the
    local/cloud distinction (research has no browser knob, so it omits the
    field). Generating these from one factory keeps the registry as the
    single place that pairs the tool name with its input schema and adapter
    method.
    """

    async def handler(
        client: MCPClientAdapter, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        params = input_class(**arguments)
        context: dict[str, Any] = {} if task_type is None else {"task_type": task_type}
        if include_browser:
            context["browser"] = params.browser  # type: ignore[attr-defined]
        return await getattr(client, client_method)(**_scout_kwargs(params)), context

    return handler


def _make_get_task_result_handler(task_type: str, client_method: str) -> ToolHandler:
    """Build a ``get_*_task_result`` handler that differs only by adapter method + task_type.

    The browsing and research variants share one shape: parse
    :class:`TaskIdInput`, call a single-argument adapter method, and stamp
    the matching ``task_type`` into the formatter context. Generating both
    from one factory keeps the registry as the single place that pairs the
    tool name with its task type.
    """

    async def handler(
        client: MCPClientAdapter, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        params = TaskIdInput(**arguments)
        return await getattr(client, client_method)(params.task_id), {
            "task_type": task_type
        }

    return handler


# Tool-name -> handler registry, consulted by _invoke() above. Mirrors
# the _TOOL_FORMATTERS registry in formatters.py so the parse/dispatch side
# of the MCP tool lifecycle is structured the same way as the format side.
_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "list_api_usage": _handle_list_api_usage,
    "list_scouts": _make_model_kwargs_handler(ListScoutsInput, "list_scouts"),
    "get_scout_detail": _handle_get_scout_detail,
    "get_scout_updates": _make_model_kwargs_handler(
        GetUpdatesInput, "get_scout_updates"
    ),
    "create_scout": _make_run_task_handler(None, CreateScoutInput, "create_scout"),
    "edit_scout": _handle_edit_scout,
    "delete_scout": _handle_delete_scout,
    "list_browsing_tasks": _make_model_kwargs_handler(
        ListTasksInput, "list_browsing_tasks", {"task_type": "Browsing"}
    ),
    "run_browsing_task": _make_run_task_handler(
        "Browsing", BrowsingTaskInput, "run_browsing_task", include_browser=True
    ),
    "get_browsing_task_result": _make_get_task_result_handler(
        "Browsing", "get_browsing_task"
    ),
    "list_research_tasks": _make_model_kwargs_handler(
        ListTasksInput, "list_research_tasks", {"task_type": "Research"}
    ),
    "run_research_task": _make_run_task_handler(
        "Research", ResearchTaskInput, "run_research_task"
    ),
    "get_research_task_result": _make_get_task_result_handler(
        "Research", "get_research_task"
    ),
}


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

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
