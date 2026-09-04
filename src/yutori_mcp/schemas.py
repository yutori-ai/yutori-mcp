"""Input schemas for MCP tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _check_webhook_https(v: str | None) -> str | None:
    """Validate that webhook_url is an HTTPS URL with a host."""
    if v is None:
        return v
    parsed = urlparse(v)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("webhook_url must use HTTPS (https://) and include a host")
    return v


HttpsWebhookUrl = Annotated[str | None, AfterValidator(_check_webhook_https)]


class ToolInput(BaseModel):
    """Base class for all tool input schemas.

    ``extra="forbid"`` guards direct model instantiation (in handler factories
    and tests). FastMCP's own tool-call binding would otherwise silently drop
    unknown fields at the protocol level before they reach handlers, but
    ``server._StrictArgsFastMCP`` restores that rejection at the point that
    still sees the client's raw argument dict, so the constraint is enforced
    at runtime there too.
    """

    model_config = ConfigDict(extra="forbid")


WebhookFormat = Literal["scout", "slack", "zapier"] | None

ScoutStatus = Literal["active", "paused", "done"] | None
TaskListStatus = Literal["running", "succeeded", "failed"] | None
UsagePeriod = Literal["24h", "7d", "30d", "90d"] | None
BrowserChoice = Literal["cloud", "local"] | None

# Shared field descriptions, defined once so the call sites cannot drift out
# of sync as the schemas evolve.
_SCOUT_ID_DESCRIPTION = "The scout's unique identifier (UUID)"
_WEBHOOK_FORMAT_DESCRIPTION = (
    "Webhook payload format: 'scout' (default), 'slack', or 'zapier'"
)
_IS_PUBLIC_DESCRIPTION = "Whether scout results are publicly accessible"
_LIST_CURSOR_DESCRIPTION = "Pagination cursor from a previous list response"

# Minimum allowed `output_interval` in seconds, shared by CreateScoutInput and
# EditScoutInput so the `ge=` validation bound and the "30 minutes" prose
# describing it cannot drift apart if the minimum ever changes.
_MIN_OUTPUT_INTERVAL_SECONDS = 1800

# Default `limit` for list_scouts/list_browsing_tasks/list_research_tasks,
# shared with the matching `@mcp.tool` function signatures in server.py.
# FastMCP builds each tool's advertised JSON Schema `default` from the Python
# function signature, not from this module's Pydantic field default, so
# server.py can't simply rely on `_limit_field()`'s default -- it needs its
# own literal. Centralizing the number here means the two cannot drift apart.
DEFAULT_LIST_LIMIT = 10


def _output_fields_description(example: list[str], docs_slug: str | None = None) -> str:
    """Build the shared `output_fields` field description.

    Four input schemas (CreateScout, EditScout, BrowsingTask, ResearchTask)
    expose the same `output_fields` shape with the same boilerplate prose;
    only the example field names and the optional docs link differ. This
    helper keeps the wording in one place so the four call sites cannot
    drift as we tweak the description over time.

    `docs_slug` is the per-tool fragment after `https://docs.yutori.com/reference/`.
    Pass ``None`` to omit the trailing "(see example at: ...)" link, matching
    the existing EditScoutInput description which has no docs link today.
    """
    base = (
        "Optional: Extract structured data as an array of objects with these field names. "
        f"Example: {example!r}. "
        "If omitted, returns human-readable text. "
        "For complex schemas, call the Yutori REST API directly"
    )
    if docs_slug is None:
        return base
    return base + f" (see example at: https://docs.yutori.com/reference/{docs_slug})."


def _output_fields_field(example: list[str], docs_slug: str | None = None) -> Any:
    """Build the shared `output_fields` Field (text from `_output_fields_description`).

    Four input schemas repeat the identical `Field(default=None, min_length=1, ...)`
    shape for `output_fields`; only the example names and docs slug differ. Keeping
    the Field construction here too (not just the description text) means the
    `min_length=1` constraint cannot drift out of sync across the four schemas.
    """
    return Field(
        default=None,
        min_length=1,
        description=_output_fields_description(example, docs_slug),
    )


def _webhook_format_field() -> Any:
    """Build the shared `webhook_format` Field (text from `_WEBHOOK_FORMAT_DESCRIPTION`).

    CreateScoutInput, EditScoutInput, BrowsingTaskInput, and ResearchTaskInput
    each repeat the identical `Field(default=None, description=_WEBHOOK_FORMAT_DESCRIPTION)`
    shape for `webhook_format`. Centralizing the Field construction (not just the
    description text) means the four call sites cannot drift apart.
    """
    return Field(default=None, description=_WEBHOOK_FORMAT_DESCRIPTION)


def _output_interval_field(description: str) -> Any:
    """Build the shared `output_interval` Field (bound is always `_MIN_OUTPUT_INTERVAL_SECONDS`).

    CreateScoutInput and EditScoutInput each repeat the identical
    `Field(default=None, ge=_MIN_OUTPUT_INTERVAL_SECONDS, description=...)` shape;
    only the description wording differs. Centralizing the Field construction
    means the `ge=` bound cannot drift out of sync, mirroring `_webhook_url_field`
    below.
    """
    return Field(default=None, ge=_MIN_OUTPUT_INTERVAL_SECONDS, description=description)


def _webhook_url_field(description: str) -> Any:
    """Build the shared `webhook_url` Field (type is always `HttpsWebhookUrl`).

    CreateScoutInput, EditScoutInput, BrowsingTaskInput, and ResearchTaskInput
    each repeat the identical `Field(default=None, description=...)` shape for
    `webhook_url`; only the description wording differs per call site.
    Centralizing the Field construction means the shared `default=None` cannot
    drift out of sync, mirroring `_webhook_format_field` above.
    """
    return Field(default=None, description=description)


def _is_public_field() -> Any:
    """Build the shared `is_public` Field (text from `_IS_PUBLIC_DESCRIPTION`).

    CreateScoutInput and EditScoutInput each repeat the identical
    `Field(default=None, description=_IS_PUBLIC_DESCRIPTION)` shape for
    `is_public`. Centralizing the Field construction mirrors
    `_webhook_format_field` above.
    """
    return Field(default=None, description=_IS_PUBLIC_DESCRIPTION)


def _scout_id_field() -> Any:
    """Build the shared required `scout_id` Field (text from `_SCOUT_ID_DESCRIPTION`).

    EditScoutInput, ScoutIdInput, and GetUpdatesInput each repeat the identical
    `Field(..., description=_SCOUT_ID_DESCRIPTION)` shape for `scout_id`.
    Centralizing the Field construction mirrors `_webhook_format_field`/
    `_is_public_field` above, so the three call sites cannot drift apart.
    """
    return Field(..., description=_SCOUT_ID_DESCRIPTION)


def _cursor_field() -> Any:
    """Build the shared list-pagination `cursor` Field (text from `_LIST_CURSOR_DESCRIPTION`).

    ListScoutsInput and ListTasksInput each repeat the identical
    `Field(default=None, description=_LIST_CURSOR_DESCRIPTION)` shape for
    `cursor`. Centralizing the Field construction mirrors `_webhook_format_field`
    and `_is_public_field` above. GetUpdatesInput's `cursor` field uses different
    description wording ("previous response" vs. "previous list response"), so
    it is intentionally not routed through this helper.
    """
    return Field(default=None, description=_LIST_CURSOR_DESCRIPTION)


def _limit_field(noun: str, *, default: int | None = DEFAULT_LIST_LIMIT) -> Any:
    """Build the shared pagination `limit` Field (1-100, optional "Default: N" suffix).

    ListScoutsInput, ListTasksInput, and GetUpdatesInput each repeat the same
    `Field(..., ge=1, le=100, description="Maximum number of {noun} to return
    (1-100)...")` shape; only the noun and whether a client-side default is
    advertised differ. Centralizing the `ge=1`/`le=100` bounds here means they
    cannot drift apart across the three schemas.
    """
    description = f"Maximum number of {noun} to return (1-100)"
    if default is not None:
        description += f". Default: {default}"
    return Field(default=default, ge=1, le=100, description=description)


class UsageInput(ToolInput):
    """Input for retrieving API usage statistics."""

    period: UsagePeriod = Field(
        default=None,
        description="Time range for activity counts: '24h' (default), '7d', '30d', or '90d'",
    )


class CreateScoutInput(ToolInput):
    """Input for creating a new monitoring scout.

    Scouts enable continuous monitoring of the web at a configurable schedule
    for tracking any changes relevant to a query. Example queries:
    - "anytime a startup in SF announces seed funding"
    - "when H100 pricing per hour drops below $1.50"
    - "latest news and product updates about Yutori"
    """

    query: str = Field(
        ...,
        description=(
            "Natural language description of what to monitor. Examples: "
            "'Tell me about the latest news, product updates, or announcements about Yutori', "
            "'when H100 pricing per hour drops below $1.50', "
            "'anytime a startup in SF announces seed funding'"
        ),
    )
    output_interval: int | None = _output_interval_field(
        f"Seconds between scout runs. Minimum {_MIN_OUTPUT_INTERVAL_SECONDS} "
        "(30 minutes). Default: 86400 (daily)"
    )
    webhook_url: HttpsWebhookUrl = _webhook_url_field(
        "HTTPS URL to receive webhook notifications when updates are available. "
        "Must use https://. Confirm the URL with the user before setting."
    )
    webhook_format: WebhookFormat = _webhook_format_field()
    output_fields: list[str] | None = _output_fields_field(
        ["headline", "summary", "url"],
        docs_slug="scouts-create#using-scheduling-webhooks-and-a-structured-output-schema",
    )
    user_timezone: str | None = Field(
        default=None,
        description="Timezone for scheduling. Example: 'America/New_York'. Default: 'America/Los_Angeles'",
    )
    skip_email: bool | None = Field(
        default=None,
        description="If true, skip email notifications (useful with webhooks)",
    )
    start_timestamp: int | None = Field(
        default=None,
        description="Unix timestamp for when monitoring should start (0 = immediately)",
    )
    user_location: str | None = Field(
        default=None,
        description="User location for geo-relevant searches. Format: 'city, region, country'",
    )
    is_public: bool | None = _is_public_field()


class EditScoutInput(ToolInput):
    """Input for editing an existing scout or changing its status."""

    scout_id: str = _scout_id_field()
    status: ScoutStatus = Field(
        default=None,
        description=(
            "Change scout status: 'active' (resume monitoring), "
            "'paused' (stop temporarily), 'done' (archive permanently)"
        ),
    )
    query: str | None = Field(
        default=None,
        description="Updated monitoring query",
    )
    output_interval: int | None = _output_interval_field(
        f"Updated run interval in seconds. Minimum {_MIN_OUTPUT_INTERVAL_SECONDS} (30 minutes)"
    )
    webhook_url: HttpsWebhookUrl = _webhook_url_field(
        "Updated HTTPS webhook URL. Must use https://. Confirm the URL with the user before setting."
    )
    webhook_format: WebhookFormat = _webhook_format_field()
    output_fields: list[str] | None = _output_fields_field(
        ["headline", "summary", "url"]
    )
    skip_email: bool | None = Field(
        default=None,
        description="Updated email notification preference",
    )
    user_timezone: str | None = Field(
        default=None,
        description="Timezone for scheduling. Example: 'America/New_York'",
    )
    user_location: str | None = Field(
        default=None,
        description="User location for geo-relevant searches",
    )
    is_public: bool | None = _is_public_field()

    @model_validator(mode="after")
    def validate_has_changes(self) -> "EditScoutInput":
        """Ensure at least one field besides scout_id is provided."""
        if not self.model_dump(exclude={"scout_id"}, exclude_none=True):
            raise ValueError("edit_scout requires at least one field to update")
        return self


class ScoutIdInput(ToolInput):
    """Input for operations on a specific scout."""

    scout_id: str = _scout_id_field()


class ListScoutsInput(ToolInput):
    """Input for listing scouts with optional limit and filtering."""

    limit: int | None = _limit_field("scouts")
    status: ScoutStatus = Field(
        default=None,
        description="Filter by status: 'active', 'paused', or 'done'",
    )
    cursor: str | None = _cursor_field()


class ListTasksInput(ToolInput):
    """Input for listing one-time browsing or research tasks."""

    limit: int | None = _limit_field("tasks")
    status: TaskListStatus = Field(
        default=None,
        description="Filter by status: 'running', 'succeeded', or 'failed'",
    )
    cursor: str | None = _cursor_field()


class GetUpdatesInput(ToolInput):
    """Input for retrieving scout updates."""

    scout_id: str = _scout_id_field()
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor from a previous response",
    )
    limit: int | None = _limit_field("updates", default=None)


class BrowsingTaskInput(ToolInput):
    """Input for running a one-time browsing task.

    The Browsing API enables automation of browser-based workflows.
    An AI agent runs its own cloud browser and operates it like a person -
    clicking, typing, scrolling, and navigating for you. Examples:
    - Fill forms on websites
    - Extract structured data from complex web pages
    - Automate multi-step workflows that require authentication
    """

    task: str = Field(
        ...,
        description=(
            "Natural language instruction for the navigator agent. Examples: "
            "'Give me a list of all employees (names and titles) of Yutori', "
            "'Fill out the contact form with my information', "
            "'Extract product prices from this page'"
        ),
    )
    start_url: str = Field(
        ...,
        description="The URL where the navigator should begin. Example: 'https://yutori.com'",
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of browser actions (1-100). Default: 25",
    )
    require_auth: bool | None = Field(
        default=None,
        description=(
            "If true, use an auth-optimized cloud browser provider for login flows. "
            "Only applies when browser is 'cloud' (default)."
        ),
    )
    browser: BrowserChoice = Field(
        default=None,
        description=(
            "Where to run the browser. 'cloud' (default) uses Yutori's cloud browser. "
            "'local' uses Yutori Local with the user's logged-in sessions on the desktop. "
            "Requires the desktop app to be running."
        ),
    )
    output_fields: list[str] | None = _output_fields_field(
        ["name", "title", "email"],
        docs_slug="browsing-create#using-webhooks-and-a-structured-output-schema",
    )
    webhook_url: HttpsWebhookUrl = _webhook_url_field(
        "HTTPS URL to receive webhook notification when task completes. Must use https://."
    )
    webhook_format: WebhookFormat = _webhook_format_field()

COMPUTER_USE_DEFAULT_MINUTES = 30
COMPUTER_USE_MAX_MINUTES = 60
# Shared with computer_use/cli.py's `--max-steps` argparse default and server.py's
# run_computer_use_task signature default, mirroring COMPUTER_USE_DEFAULT_MINUTES above so the
# three call sites cannot drift apart if the default ever changes. The same holds for
# COMPUTER_USE_DEFAULT_MODE and the `mode` / `allow_foreground_fallback` fields below, which
# cli.py's `--mode` / `--allow-foreground-fallback` and server.py's signature mirror.
COMPUTER_USE_DEFAULT_MAX_STEPS = 60
# Mirrors computer_use/constants.py DELIVERY_MODES (this module stays free of computer_use
# imports so the schema can load without the runtime); a test pins the two together.
ComputerUseMode = Literal["foreground", "background"]
COMPUTER_USE_DEFAULT_MODE: ComputerUseMode = "foreground"


class ComputerUseTaskInput(ToolInput):
    task: str = Field(..., description="Task to perform on the Mac desktop or in the target app's window")
    app: str | None = Field(default=None, description="Optional application to target")
    start_url: str | None = Field(
        default=None, description="Optional URL to open in the target app"
    )
    minutes: float = Field(
        default=COMPUTER_USE_DEFAULT_MINUTES,
        ge=1,
        le=COMPUTER_USE_MAX_MINUTES,
        description=f"Absolute run deadline in minutes (1-{COMPUTER_USE_MAX_MINUTES})",
    )
    max_steps: int = Field(
        default=COMPUTER_USE_DEFAULT_MAX_STEPS,
        ge=1,
        description="Maximum model turns before stopping the run; one turn may contain multiple actions",
    )
    mode: ComputerUseMode = Field(
        default=COMPUTER_USE_DEFAULT_MODE,
        description=(
            "'foreground' (default) drives the whole visible desktop; the user must not touch the Mac. "
            "'background' drives only the target app's window without taking focus, so the user can keep "
            "working; only that window is captured. Requires app."
        ),
    )
    allow_foreground_fallback: bool = Field(
        default=False,
        description=(
            "Background mode only. When true, an action the driver reports as not landing in the "
            "background is retried once with the target window fronted briefly and the prior app restored."
        ),
    )

    @model_validator(mode="after")
    def require_app_for_start_url(self) -> ComputerUseTaskInput:
        if self.start_url is not None and self.app is None:
            raise ValueError("start_url requires app")
        return self

    @model_validator(mode="after")
    def require_app_for_background(self) -> ComputerUseTaskInput:
        if self.mode == "background" and self.app is None:
            raise ValueError("mode='background' requires app")
        return self

    @model_validator(mode="after")
    def require_background_for_fallback(self) -> ComputerUseTaskInput:
        if self.allow_foreground_fallback and self.mode != "background":
            raise ValueError("allow_foreground_fallback requires mode='background'")
        return self


class TaskIdInput(ToolInput):
    """Input for retrieving a browsing or research task result."""

    task_id: str = Field(..., description="The task's unique identifier")


class ResearchTaskInput(ToolInput):
    """Input for running a one-time research task.

    The Research API executes deep web research on any topic.
    An AI agent searches, reads, and synthesizes information from across the web.
    Examples:
    - Research competitive landscape for a product
    - Summarize recent news about a company
    - Find technical documentation or specifications
    """

    query: str = Field(
        ...,
        description=(
            "Natural language description of what to research. Examples: "
            "'What are the latest developments in quantum computing from the past week?', "
            "'Research the competitive landscape for AI code assistants', "
            "'Find pricing information for cloud GPU providers'"
        ),
    )
    user_timezone: str | None = Field(
        default=None,
        description="Timezone for contextual awareness. Example: 'America/New_York'. Default: 'America/Los_Angeles'",
    )
    user_location: str | None = Field(
        default=None,
        description=(
            "Location for contextual awareness. Format: 'city, region, country'. "
            "Default: 'San Francisco, CA, US'"
        ),
    )
    output_fields: list[str] | None = _output_fields_field(
        ["title", "summary", "source_url"],
        docs_slug="research-create#using-webhooks-and-a-structured-output-schema",
    )
    webhook_url: HttpsWebhookUrl = _webhook_url_field(
        "HTTPS URL to receive webhook notification when research completes. Must use https://."
    )
    webhook_format: WebhookFormat = _webhook_format_field()
