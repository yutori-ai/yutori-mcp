"""Yutori MCP Server - Web monitoring and browsing automation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError

from . import __version__
from .adapter import MCPClientAdapter, YutoriAPIError
from .formatters import TASK_TYPE_BROWSING, TASK_TYPE_RESEARCH, format_response
from .schema_utils import output_fields_to_output_schema
from .schemas import (
    BrowserChoice,
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
    ListScoutsInput,
    ListTasksInput,
    ResearchTaskInput,
    ScoutIdInput,
    ScoutStatus,
    TaskIdInput,
    TaskListStatus,
    UsageInput,
    UsagePeriod,
    WebhookFormat,
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
# Tool definitions. Each @mcp.tool function is a thin wrapper that forwards
# its typed parameters to _invoke, which routes through _TOOL_HANDLERS
# (unchanged from the prior Server-based implementation). Each wrapper's body
# is a single `return await _invoke(name, locals())` statement, so `locals()`
# at that point contains exactly the function's parameters -- no other names
# are ever bound first. This keeps the per-tool parameter list written once
# (in the signature) instead of duplicated in a hand-written dict literal that
# could silently drift out of sync with the signature. If a stray local were
# ever introduced before the return, the affected input schema's
# `extra="forbid"` config (see schemas.ToolInput) would raise immediately
# rather than silently misforwarding it.
# ---------------------------------------------------------------------------

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_IDEMPOTENT = ToolAnnotations(idempotentHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)


def _list_tasks_description(task_label: str, get_tool: str) -> str:
    """Build the shared list_*_tasks tool description.

    list_browsing_tasks and list_research_tasks share identical boilerplate
    describing pagination/status filtering and pointing at the matching
    get_*_task_result tool for authoritative status; only the task label and
    get-tool name differ. Mirrors the ``_output_fields_description`` helper
    in schemas.py, which extracts the same kind of repeated tool-facing prose.
    """
    return (
        f"List one-time {task_label} tasks for the authenticated user. "
        "Supports cursor pagination and status filtering. List status is approximate "
        f"(running also covers queued and not-yet-reconciled tasks); call "
        f"{get_tool} for a task's authoritative status."
    )


def _get_task_result_description(task_label: str) -> str:
    """Build the shared get_*_task_result tool description.

    get_browsing_task_result and get_research_task_result share identical
    text apart from the task label.
    """
    return f"Poll for {task_label} task status and result. Call until status is 'succeeded' or 'failed'."


@mcp.tool(
    description=(
        "Get API usage statistics including active scout counts, rate limits, and activity metrics."
    ),
    annotations=_READ_ONLY,
)
async def list_api_usage(
    period: UsagePeriod = None,
) -> str:
    return await _invoke("list_api_usage", locals())


@mcp.tool(
    description=(
        "List all scouts for the authenticated user. "
        "Returns basic metadata; use get_scout_detail for full fields."
    ),
    annotations=_READ_ONLY,
)
async def list_scouts(
    limit: int | None = 10,
    status: ScoutStatus = None,
    cursor: str | None = None,
) -> str:
    return await _invoke("list_scouts", locals())


@mcp.tool(
    description="Get detailed information about a specific scout.",
    annotations=_READ_ONLY,
)
async def get_scout_detail(scout_id: str) -> str:
    return await _invoke("get_scout_detail", locals())


@mcp.tool(
    description="Get paginated updates/reports for a scout. Each update contains findings from a run.",
    annotations=_READ_ONLY,
)
async def get_scout_updates(
    scout_id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> str:
    return await _invoke("get_scout_updates", locals())


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
    webhook_format: WebhookFormat = None,
    output_fields: list[str] | None = None,
    user_timezone: str | None = None,
    skip_email: bool | None = None,
    start_timestamp: int | None = None,
    user_location: str | None = None,
    is_public: bool | None = None,
) -> str:
    return await _invoke("create_scout", locals())


@mcp.tool(
    description=(
        "Update an existing scout's query, schedule, webhook configuration, or status. "
        "Use status='paused' to pause, 'active' to resume, or 'done' to archive."
    ),
    annotations=_IDEMPOTENT,
)
async def edit_scout(
    scout_id: str,
    status: ScoutStatus = None,
    query: str | None = None,
    output_interval: int | None = None,
    webhook_url: str | None = None,
    webhook_format: WebhookFormat = None,
    output_fields: list[str] | None = None,
    skip_email: bool | None = None,
    user_timezone: str | None = None,
    user_location: str | None = None,
    is_public: bool | None = None,
) -> str:
    return await _invoke("edit_scout", locals())


@mcp.tool(
    description="Permanently delete a scout and all its data. This action cannot be undone.",
    annotations=_DESTRUCTIVE,
)
async def delete_scout(scout_id: str) -> str:
    return await _invoke("delete_scout", locals())


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
    browser: BrowserChoice = None,
    output_fields: list[str] | None = None,
    webhook_url: str | None = None,
    webhook_format: WebhookFormat = None,
) -> str:
    return await _invoke("run_browsing_task", locals())


@mcp.tool(
    description=_list_tasks_description("browsing", "get_browsing_task_result"),
    annotations=_READ_ONLY,
)
async def list_browsing_tasks(
    limit: int | None = 10,
    status: TaskListStatus = None,
    cursor: str | None = None,
) -> str:
    return await _invoke("list_browsing_tasks", locals())


@mcp.tool(
    description=_get_task_result_description("browsing"),
    annotations=_READ_ONLY,
)
async def get_browsing_task_result(task_id: str) -> str:
    return await _invoke("get_browsing_task_result", locals())


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
    webhook_format: WebhookFormat = None,
) -> str:
    return await _invoke("run_research_task", locals())


@mcp.tool(
    description=_list_tasks_description("research", "get_research_task_result"),
    annotations=_READ_ONLY,
)
async def list_research_tasks(
    limit: int | None = 10,
    status: TaskListStatus = None,
    cursor: str | None = None,
) -> str:
    return await _invoke("list_research_tasks", locals())


@mcp.tool(
    description=_get_task_result_description("research"),
    annotations=_READ_ONLY,
)
async def get_research_task_result(task_id: str) -> str:
    return await _invoke("get_research_task_result", locals())


# Signature for tool handlers registered in _TOOL_HANDLERS.
ToolHandler = Callable[
    [MCPClientAdapter, dict[str, Any]],
    Awaitable[tuple[dict[str, Any], dict[str, Any]]],
]


def _output_schema_kwargs(
    params: BaseModel, extra_exclude: set[str] | None = None
) -> dict[str, Any]:
    """Convert a Pydantic input model into adapter kwargs.

    Drops None fields — callers relying on an empty result when no config
    fields were provided (e.g. _handle_edit_scout) should confirm that still
    holds — and transforms `output_fields` into the API's `output_schema`
    format when present. This is the single kwargs-building step shared by
    every handler built via ``_make_handler`` below: input classes without an
    `output_fields` field simply skip the transform, since
    `exclude={"output_fields", ...}` on a model without that field is a
    no-op. Callers can pass `extra_exclude` to drop additional fields (e.g.
    control fields that should not be forwarded to the adapter method).
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


def _make_handler(
    input_class: type[BaseModel],
    client_method: str,
    context: dict[str, Any] | Callable[[BaseModel], dict[str, Any]] | None = None,
) -> ToolHandler:
    """Build a handler that parses an input schema and forwards it as adapter kwargs.

    Shared shape for tools whose handler body is the one-liner
    ``client.METHOD(**_output_schema_kwargs(params))``: parse a schema, call the
    adapter method, and stamp a formatter context onto the result. ``context``
    is either a static dict (e.g. ``{"task_type": "Browsing"}`` for the
    list_*_tasks tools), a callable that derives the context from the parsed
    params (e.g. ``run_browsing_task`` needs ``params.browser`` so
    ``format_task_started`` can annotate the local/cloud distinction), or
    omitted entirely for tools whose formatter needs no context (e.g.
    ``create_scout``, which has no task-type concept). Generating these from
    one factory keeps the registry as the single place that pairs the tool
    name with its input schema, adapter method, and context.
    """

    async def handler(
        client: MCPClientAdapter, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        params = input_class(**arguments)
        ctx = context(params) if callable(context) else dict(context or {})
        return await getattr(client, client_method)(
            **_output_schema_kwargs(params)
        ), ctx

    return handler


async def _handle_edit_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = EditScoutInput(**arguments)

    # Fetch current state for diff (also validates scout exists)
    old_scout = await client.get_scout_detail(params.scout_id)

    # Build config kwargs, excluding control fields (scout_id is passed
    # explicitly below; status is applied separately after config updates).
    config_kwargs = _output_schema_kwargs(params, extra_exclude={"scout_id", "status"})

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


# Tool-name -> handler registry, consulted by _invoke() above. Mirrors
# the _TOOL_FORMATTERS registry in formatters.py so the parse/dispatch side
# of the MCP tool lifecycle is structured the same way as the format side.
_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "list_api_usage": _make_handler(UsageInput, "get_usage"),
    "list_scouts": _make_handler(ListScoutsInput, "list_scouts"),
    "get_scout_detail": _make_handler(ScoutIdInput, "get_scout_detail"),
    "get_scout_updates": _make_handler(GetUpdatesInput, "get_scout_updates"),
    "create_scout": _make_handler(CreateScoutInput, "create_scout"),
    "edit_scout": _handle_edit_scout,
    "delete_scout": _handle_delete_scout,
    "list_browsing_tasks": _make_handler(
        ListTasksInput, "list_browsing_tasks", {"task_type": TASK_TYPE_BROWSING}
    ),
    "run_browsing_task": _make_handler(
        BrowsingTaskInput,
        "run_browsing_task",
        lambda params: {"task_type": TASK_TYPE_BROWSING, "browser": params.browser},  # type: ignore[attr-defined]
    ),
    "get_browsing_task_result": _make_handler(
        TaskIdInput, "get_browsing_task", {"task_type": TASK_TYPE_BROWSING}
    ),
    "list_research_tasks": _make_handler(
        ListTasksInput, "list_research_tasks", {"task_type": TASK_TYPE_RESEARCH}
    ),
    "run_research_task": _make_handler(
        ResearchTaskInput, "run_research_task", {"task_type": TASK_TYPE_RESEARCH}
    ),
    "get_research_task_result": _make_handler(
        TaskIdInput, "get_research_task", {"task_type": TASK_TYPE_RESEARCH}
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
