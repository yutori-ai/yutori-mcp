"""Yutori MCP Server - Web monitoring and browsing automation."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, NoReturn, TypeVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError

from . import __version__
from .adapter import (
    DEFAULT_ENVIRONMENT,
    ENV_VAR_ENVIRONMENT,
    ENVIRONMENT_BASE_URLS,
    MCPClientAdapter,
    YutoriAPIError,
    is_scoped_environment,
    resolve_base_url,
)
from .formatters import (
    TASK_TOOLS,
    TASK_TYPE_BROWSING,
    TASK_TYPE_RESEARCH,
    format_response,
)
from .schema_utils import output_fields_to_output_schema
from .schemas import (
    COMPUTER_USE_DEFAULT_MAX_STEPS,
    COMPUTER_USE_DEFAULT_MINUTES,
    COMPUTER_USE_DEFAULT_MODE,
    DEFAULT_LIST_LIMIT,
    BrowserChoice,
    BrowsingTaskInput,
    ComputerUseMode,
    ComputerUseTaskInput,
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


class _StrictArgsFastMCP(FastMCP):
    """FastMCP that rejects tool calls containing unknown argument names.

    FastMCP's own tool-call binding silently drops any argument name it
    doesn't recognize (it disables the low-level Server's jsonschema
    additionalProperties:false check "for backwards compatibility" - see
    mcp.server.fastmcp.server.FastMCP._setup_handlers), which defeats the
    extra="forbid" intent documented on schemas.ToolInput. This restores
    that rejection at the one point that still sees the client's raw
    argument dict, before FastMCP's own binding drops anything unknown.
    `tool.parameters`/`_tool_manager` are undocumented FastMCP internals;
    if a future `mcp` release renames them, fail open (skip validation)
    rather than break the server.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            tool = self._tool_manager.get_tool(name)
            unknown = (
                set(arguments) - set(tool.parameters.get("properties", {}))
                if tool
                else set()
            )
        except AttributeError:
            logger.warning(
                "Could not validate tool arguments against %r; skipping strict-args check",
                name,
            )
            unknown = set()
        if unknown:
            raise ToolError(
                f"Unknown argument(s) for tool {name!r}: {', '.join(sorted(unknown))}"
            )
        return await super().call_tool(name, arguments)


# Process-lifetime MCPClientAdapter singleton. Constructing MCPClientAdapter
# spins up an httpx.AsyncClient, so a fresh one per tool call means a fresh
# connection pool per call in what is otherwise a long-running stdio session.
# get_adapter() lazily creates it once and reuses it for every subsequent
# call; _lifespan() closes it when the server shuts down. Sharing one
# instance across calls is safe: mcp.server.lowlevel.Server.run() starts one
# asyncio task per incoming request (so tool calls can run concurrently), and
# httpx.AsyncClient is designed to be shared and used concurrently across
# tasks in the same event loop (that's what its connection pooling is for).
_adapter: MCPClientAdapter | None = None


def get_adapter() -> MCPClientAdapter:
    """Return the shared MCPClientAdapter, creating it on first use."""
    global _adapter
    if _adapter is None:
        _adapter = MCPClientAdapter()
    return _adapter


@asynccontextmanager
async def _lifespan(_: FastMCP) -> AsyncIterator[None]:
    """Close the shared adapter when the server shuts down.

    Registered as FastMCP's `lifespan` so it runs inside the same event loop
    as `mcp.run()` (the low-level Server enters this context manager once,
    around the whole request loop). A plain try/finally wrapped around the
    synchronous `mcp.run()` call in main() would not work here: `mcp.run()`
    drives `anyio.run(...)`, which tears its event loop down before returning
    control to the caller, leaving no valid loop left to close an
    asyncio-bound httpx client from.
    """
    try:
        yield
    finally:
        global _adapter
        if _adapter is not None:
            await _adapter.close()
            _adapter = None


mcp = _StrictArgsFastMCP("yutori-mcp", lifespan=_lifespan)


def _format_api_error(e: YutoriAPIError) -> str:
    """Render a YutoriAPIError in the stable `API Error (status): message` text shape.

    Single source of truth for this string: `_invoke()` uses it to build the
    RuntimeError message that becomes the MCP tool result's error text, and
    tests assert against this function directly instead of re-deriving the
    format inline (which previously let a test believe it was covering the
    format while never exercising it).
    """
    return f"API Error ({e.status_code}): {e.message}"


async def _invoke(tool_name: str, args: dict[str, Any]) -> str:
    """Invoke a tool handler and return the formatted response text.

    Centralizes error handling: YutoriAPIError → RuntimeError so the MCP
    framework marks the result isError=True; ValidationError re-raised as-is;
    unexpected exceptions logged and re-raised.
    """
    handler = _TOOL_HANDLERS[tool_name]
    try:
        if tool_name == "run_computer_use_task":
            from .computer_use.constants import DELIVERY_MODES
            from .computer_use.result import failure, format_result

            try:
                result, _ = await handler(None, args)  # type: ignore[arg-type]
            except ValidationError:
                raise
            # MCP callers need the same actionable result shape even when a
            # platform integration raises an error we cannot classify here.
            except Exception as error:  # noqa: BLE001
                logger.error(
                    "Computer-use task failed before the runner returned a result"
                )
                requested_mode = args.get("mode")
                delivery_mode = (
                    str(requested_mode) if requested_mode in DELIVERY_MODES else COMPUTER_USE_DEFAULT_MODE
                )
                result = failure(str(error), delivery_mode=delivery_mode)
            return format_result(result)
        client = get_adapter()
        result, context = await handler(client, args)
        return format_response(tool_name, result, **context)
    except YutoriAPIError as e:
        raise RuntimeError(_format_api_error(e)) from e
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
_DESTRUCTIVE_OPEN_WORLD = ToolAnnotations(destructiveHint=True, openWorldHint=True)


def _list_tasks_description(task_type: str) -> str:
    """Build the shared list_*_tasks tool description.

    list_browsing_tasks and list_research_tasks share identical boilerplate
    describing pagination/status filtering and pointing at the matching
    get_*_task_result tool for authoritative status; only the task label and
    get-tool name differ, and both are derivable from the task type. Mirrors
    the ``_output_fields_description`` helper in schemas.py, which extracts
    the same kind of repeated tool-facing prose.

    ``task_type`` is a ``formatters.TASK_TOOLS`` key (``TASK_TYPE_BROWSING``
    or ``TASK_TYPE_RESEARCH``). The prose label and the get-tool name are
    derived from it here, exactly as ``formatters.format_task_list`` does, so
    a caller cannot pair one task type's label with another type's tool name,
    and this text can't drift from the tool-name hints formatters.py renders
    into tool results.
    """
    _, get_tool = TASK_TOOLS[task_type]
    return (
        f"List one-time {task_type.lower()} tasks for the authenticated user. "
        "Supports cursor pagination and status filtering. List status is approximate "
        f"(running also covers queued and not-yet-reconciled tasks); call "
        f"{get_tool} for a task's authoritative status."
    )


def _get_task_result_description(task_type: str) -> str:
    """Build the shared get_*_task_result tool description.

    get_browsing_task_result and get_research_task_result share identical
    text apart from the task label, derived from the same ``task_type`` key
    ``_list_tasks_description`` above takes.
    """
    return (
        f"Poll for {task_type.lower()} task status and result. "
        "Call until status is 'succeeded' or 'failed'."
    )


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
    limit: int | None = DEFAULT_LIST_LIMIT,
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
    description=_list_tasks_description(TASK_TYPE_BROWSING),
    annotations=_READ_ONLY,
)
async def list_browsing_tasks(
    limit: int | None = DEFAULT_LIST_LIMIT,
    status: TaskListStatus = None,
    cursor: str | None = None,
) -> str:
    return await _invoke("list_browsing_tasks", locals())


@mcp.tool(
    description=_get_task_result_description(TASK_TYPE_BROWSING),
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


async def run_computer_use_task(
    task: str,
    app: str | None = None,
    start_url: str | None = None,
    minutes: float = COMPUTER_USE_DEFAULT_MINUTES,
    max_steps: int = COMPUTER_USE_DEFAULT_MAX_STEPS,
    mode: ComputerUseMode = COMPUTER_USE_DEFAULT_MODE,
    allow_foreground_fallback: bool = False,
    ctx: Context | None = None,
) -> str:
    # `ctx` is FastMCP's injected request context (excluded from the client-facing
    # input schema), used to stream per-action progress notifications while the
    # runner drives the desktop. _handle_computer_use pops it before schema parsing.
    return await _invoke("run_computer_use_task", locals())


@mcp.tool(
    description=_list_tasks_description(TASK_TYPE_RESEARCH),
    annotations=_READ_ONLY,
)
async def list_research_tasks(
    limit: int | None = DEFAULT_LIST_LIMIT,
    status: TaskListStatus = None,
    cursor: str | None = None,
) -> str:
    return await _invoke("list_research_tasks", locals())


@mcp.tool(
    description=_get_task_result_description(TASK_TYPE_RESEARCH),
    annotations=_READ_ONLY,
)
async def get_research_task_result(task_id: str) -> str:
    return await _invoke("get_research_task_result", locals())


# Signature for tool handlers registered in _TOOL_HANDLERS.
ToolHandler = Callable[
    [MCPClientAdapter, dict[str, Any]],
    Awaitable[tuple[dict[str, Any], dict[str, Any]]],
]

# Bound to BaseModel so _make_handler can tie its `context` callable's
# parameter type to the specific input_class passed at each call site
# (see _make_handler below), instead of widening it to plain BaseModel.
_InputT = TypeVar("_InputT", bound=BaseModel)


def _output_schema_kwargs(
    params: BaseModel, extra_exclude: set[str] | None = None
) -> dict[str, Any]:
    """Convert a Pydantic input model into adapter kwargs.

    Drops None fields — callers relying on an empty result when no config
    fields were provided (e.g. _apply_edit_config) should confirm that still
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
    output_fields = getattr(params, "output_fields", None)
    if output_fields is not None:
        kwargs["output_schema"] = output_fields_to_output_schema(output_fields)
    return kwargs


# -----------------------------------------------------------------------------
# Per-tool handlers. Each handler parses arguments via its input schema, calls
# the appropriate adapter method, and returns (result, context) for the
# formatter registry in formatters.py.
# -----------------------------------------------------------------------------


def _make_handler(
    input_class: type[_InputT],
    client_method: str,
    context: dict[str, Any] | Callable[[_InputT], dict[str, Any]] | None = None,
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

    ``input_class``/``context`` share the ``_InputT`` type variable so a
    context callable can access fields specific to the ``input_class`` passed
    at the same call site (e.g. ``lambda params: {"browser": params.browser}``
    for ``BrowsingTaskInput``) without a type checker widening ``params`` to
    plain ``BaseModel``.
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


async def _apply_edit_config(
    client: MCPClientAdapter, params: EditScoutInput
) -> dict[str, Any]:
    """Build and apply edit_scout's config kwargs (query, schedule, webhook, etc.).

    Excludes control fields (scout_id is passed explicitly; status is applied
    separately by _apply_edit_status). Returns the applied kwargs so the
    caller can tell whether a config update happened, which _apply_edit_status
    needs to distinguish a partial failure from a plain one.
    """
    config_kwargs = _output_schema_kwargs(params, extra_exclude={"scout_id", "status"})
    if config_kwargs:
        await client.edit_scout(scout_id=params.scout_id, **config_kwargs)
    return config_kwargs


async def _apply_edit_status(
    client: MCPClientAdapter, params: EditScoutInput, config_kwargs: dict[str, Any]
) -> None:
    """Apply edit_scout's status change, if any, after config updates.

    The API requires status and config to be updated in separate calls, so
    the operation is not atomic: if the status call fails after a config
    update succeeded, say so explicitly instead of reporting a plain failure.
    """
    if params.status is None:
        return
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


async def _handle_edit_scout(
    client: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = EditScoutInput(**arguments)

    # Fetch current state for diff (also validates scout exists)
    old_scout = await client.get_scout_detail(params.scout_id)

    config_kwargs = await _apply_edit_config(client, params)
    await _apply_edit_status(client, params, config_kwargs)

    # Return old and new state for diff. Every mutation already succeeded at
    # this point, so a failed read-back must not surface as a failed edit —
    # the formatter renders a success message without the diff instead.
    try:
        new_scout = await client.get_scout_detail(params.scout_id)
    except YutoriAPIError:
        new_scout = {}
    return {"old": old_scout, "new": new_scout}, {}


def _progress_reporter(
    ctx: Context,
    max_steps: int,
    *,
    mode: str = COMPUTER_USE_DEFAULT_MODE,
    app: str | None = None,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Turn runner events into MCP progress + log notifications.

    Progress totals are deliberately omitted: an action's `index` is a monotonic
    per-action counter and a batch step executes several actions, so max_steps is
    not an upper bound for it — a fabricated total would render a bar that
    overshoots. The human-readable message carries the step budget instead.
    """
    from .computer_use.result import describe_delivery_surface, format_action_line

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "ready":
            surface = describe_delivery_surface(mode, app)
            message = f"Computer-use runner ready; driving {surface} (up to {max_steps} steps)."
            await ctx.report_progress(progress=0, message=message)
            await ctx.info(message)
            return
        index = event.get("index", 0)
        message = format_action_line(event, index_default=0)
        if event.get("elapsed_ms") is not None:
            message += f" [{event['elapsed_ms']} ms]"
        if event.get("command"):
            message += f" $ {event['command']}"
        await ctx.report_progress(progress=index, message=message)
        await ctx.info(message)

    return on_event


async def _handle_computer_use(
    _: MCPClientAdapter, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .adapter import resolve_run_credentials_and_platform_url
    from .computer_use.lock import ComputerUseBusyError, DesktopLock
    from .computer_use.preflight import blocker_message, first_blocker
    from .computer_use.result import failure
    from .computer_use.supervisor import run_task

    arguments = dict(arguments)
    ctx: Context | None = arguments.pop("ctx", None)
    params = ComputerUseTaskInput(**arguments)
    lock = DesktopLock()
    try:
        with lock:
            blocker = first_blocker()
            if blocker is not None:
                return failure(blocker_message(blocker), delivery_mode=params.mode), {}
            api_key, api_base_url, platform_url = resolve_run_credentials_and_platform_url()
            return (
                await run_task(
                    **params.model_dump(),
                    api_key=api_key,
                    api_base_url=api_base_url,
                    platform_url=platform_url,
                    lock=lock,
                    on_event=(
                        _progress_reporter(ctx, params.max_steps, mode=params.mode, app=params.app)
                        if ctx
                        else None
                    ),
                ),
                {},
            )
    except ComputerUseBusyError as error:
        return failure(str(error), delivery_mode=params.mode), {}


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
    "delete_scout": _make_handler(
        ScoutIdInput,
        "delete_scout",
        lambda params: {"scout_id": params.scout_id},
    ),
    "list_browsing_tasks": _make_handler(
        ListTasksInput, "list_browsing_tasks", {"task_type": TASK_TYPE_BROWSING}
    ),
    "run_browsing_task": _make_handler(
        BrowsingTaskInput,
        "run_browsing_task",
        lambda params: {"task_type": TASK_TYPE_BROWSING, "browser": params.browser},
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


def _computer_use_enabled() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        resolve_base_url()
        return True
    except ValueError:
        return False


def _register_computer_use_tool() -> None:
    """Expose the macOS computer-use tool on supported hosts."""
    if "run_computer_use_task" in _TOOL_HANDLERS:
        return
    if not _computer_use_enabled():
        return
    mcp.tool(
        description=(
            "Operate a Mac with Yutori computer use. mode='foreground' (default) drives the whole "
            "visible desktop: do not touch the Mac during the run, and visible desktop content is sent "
            "to Yutori. mode='background' drives only the target app's window without taking focus, so "
            "the user can keep working; only that window's content is sent. Use it when the user asks to "
            "run something in the background or while they keep working. Background requires app."
        ),
        annotations=_DESTRUCTIVE_OPEN_WORLD,
    )(run_computer_use_task)
    _TOOL_HANDLERS["run_computer_use_task"] = _handle_computer_use


# Each auth handler imports its dependencies lazily so normal server startup
# does not pay for the interactive auth flow.


def _auth_login(environment: str | None) -> NoReturn:
    if is_scoped_environment(environment):
        from getpass import getpass

        from .credentials import mask, save_environment_key

        print(
            f"Paste a {environment} API key from https://platform.{environment}.yutori.com "
            "(input is hidden):"
        )
        try:
            key = getpass("API key: ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            raise SystemExit(1) from None
        if not key.strip():
            print("No key entered; nothing was saved.")
            raise SystemExit(1)
        path = save_environment_key(environment, key)
        print(f"Saved {environment} API key ({mask(key.strip())}) to {path}")
        raise SystemExit(0)

    from yutori.auth import run_login_flow

    result = run_login_flow(key_source="yutori-mcp")
    if result.success:
        print("Successfully authenticated!")
    else:
        print(f"Authentication failed: {result.error}")
        if result.auth_url:
            print(f"\nIf the browser didn't open, visit:\n  {result.auth_url}")
    raise SystemExit(0 if result.success else 1)


def _auth_logout(environment: str | None) -> NoReturn:
    if is_scoped_environment(environment):
        from .credentials import clear_environment_key

        removed = clear_environment_key(environment)
        print(
            f"Removed the {environment} API key."
            if removed
            else f"No {environment} API key was stored."
        )
        raise SystemExit(0)

    from yutori.auth import clear_config

    clear_config()
    print("Logged out successfully.")
    raise SystemExit(0)


def _auth_status(environment: str | None) -> NoReturn:
    if is_scoped_environment(environment):
        from yutori.auth.credentials import get_config_path

        from .credentials import mask, stored_environment_key

        stored = stored_environment_key(environment)
        override = os.environ.get("YUTORI_API_KEY")
        if override:
            print(f"Authenticated for {environment} via YUTORI_API_KEY ({mask(override)})")
        elif stored:
            print(f"Authenticated for {environment} ({mask(stored)}) from {get_config_path()}")
        else:
            print(
                f"No {environment} credential. Run "
                f"'uvx yutori-mcp --env {environment} login'."
            )
            raise SystemExit(1)
        raise SystemExit(0)

    from yutori.auth import get_auth_status

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


# Pairing CLI help with its handler keeps advertised and implemented commands in sync.
_AUTH_SUBCOMMANDS: dict[str, tuple[str, Callable[[str | None], NoReturn]]] = {
    "login": ("Log in and save API key", _auth_login),
    "logout": ("Remove saved API key", _auth_logout),
    "status": ("Show authentication status", _auth_status),
}


def _handle_auth_command(command: str, environment: str | None = None) -> NoReturn:
    _, run_command = _AUTH_SUBCOMMANDS[command]
    run_command(environment)


def main() -> None:
    """Entry point for the yutori-mcp command."""
    parser = argparse.ArgumentParser(prog="yutori-mcp")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit",
    )
    parser.add_argument(
        "--env",
        choices=tuple(ENVIRONMENT_BASE_URLS),
        help=(
            f"Yutori environment to target (default: {DEFAULT_ENVIRONMENT}). "
            f"Overrides the {ENV_VAR_ENVIRONMENT} environment variable. "
            "Auth subcommands target this explicit environment when provided; "
            "otherwise they use production."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    for name, (help_text, _) in _AUTH_SUBCOMMANDS.items():
        subparsers.add_parser(name, help=help_text)
    from .computer_use.cli import register_parser

    register_parser(subparsers)

    args = parser.parse_args()

    # The flag is forwarded via the env var (rather than threaded through to
    # get_adapter()'s lazily-constructed, process-lifetime MCPClientAdapter)
    # so adapter.resolve_base_url() stays the single resolution point
    # whether the environment came from the CLI or from an MCP client
    # config's `env` block.
    if args.env:
        os.environ[ENV_VAR_ENVIRONMENT] = args.env

    if args.command == "computer-use":
        # Public computer-use commands default to production even if a shell has
        # stale YUTORI_ENV state. Internal testing can still pass --env explicitly.
        from .computer_use.cli import apply_computer_use_environment, dispatch

        apply_computer_use_environment(args.env)
        raise SystemExit(dispatch(args.computer_use_command, args))

    # Dispatched after --env is applied, not before: `login --env <name>` has to know which
    # environment it is storing a credential for, and the old ordering ran auth first.
    #
    # Only the explicit flag counts here, never the ambient YUTORI_ENV. The README tells people
    # to export that variable, and letting it reach this line meant a plain `login` could silently
    # become a non-default paste prompt instead of the production browser flow, while `logout` cleared
    # a non-default entry instead of the real credential. Auth is an explicit act; it must not change
    # meaning because of something left set in a shell.
    if args.command in _AUTH_SUBCOMMANDS:
        _handle_auth_command(args.command, args.env)
    try:
        base_url = resolve_base_url()
    except ValueError as e:
        # An invalid YUTORI_ENV must fail at startup, not on the first tool
        # call — and never fall back silently to production.
        parser.error(str(e))
    if base_url != ENVIRONMENT_BASE_URLS[DEFAULT_ENVIRONMENT]:
        # stdout carries the stdio MCP transport; stderr is safe for humans.
        print(f"yutori-mcp: targeting non-default API {base_url}", file=sys.stderr)

    # Register only after --env has replaced any ambient value. Registering at import time
    # would let ambient dev state leak the desktop tool into an explicit --env prod server.
    _register_computer_use_tool()

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
