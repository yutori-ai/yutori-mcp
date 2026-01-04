"""Input schemas for MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateScoutInput(BaseModel):
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
    output_interval: int | None = Field(
        default=None,
        ge=1800,
        description="Seconds between scout runs. Minimum 1800 (30 minutes). Default: 86400 (daily)",
    )
    webhook_url: str | None = Field(
        default=None,
        description="URL to receive webhook notifications when updates are available",
    )
    webhook_format: str | None = Field(
        default=None,
        description="Webhook payload format: 'scout' (default) or 'slack'",
    )
    task_spec: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for structured output format",
    )
    user_timezone: str | None = Field(
        default=None,
        description="Timezone for scheduling. Example: 'America/New_York'. Default: 'America/Los_Angeles'",
    )
    skip_email: bool | None = Field(
        default=None,
        description="If true, skip email notifications (useful with webhooks)",
    )


class EditScoutInput(BaseModel):
    """Input for editing an existing scout."""

    scout_id: str = Field(..., description="The scout's unique identifier (UUID)")
    query: str | None = Field(
        default=None,
        description="Updated monitoring query",
    )
    output_interval: int | None = Field(
        default=None,
        ge=1800,
        description="Updated run interval in seconds. Minimum 1800 (30 minutes)",
    )
    webhook_url: str | None = Field(
        default=None,
        description="Updated webhook URL",
    )
    webhook_format: str | None = Field(
        default=None,
        description="Updated webhook format",
    )
    task_spec: dict[str, Any] | None = Field(
        default=None,
        description="Updated JSON Schema for structured output",
    )
    skip_email: bool | None = Field(
        default=None,
        description="Updated email notification preference",
    )


class ScoutIdInput(BaseModel):
    """Input for operations on a specific scout."""

    scout_id: str = Field(..., description="The scout's unique identifier (UUID)")


class GetUpdatesInput(BaseModel):
    """Input for retrieving scout updates."""

    scout_id: str = Field(..., description="The scout's unique identifier (UUID)")
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor from a previous response",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of updates to return (1-100)",
    )


class BrowsingTaskInput(BaseModel):
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
    task_spec: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for structured output format",
    )
    webhook_url: str | None = Field(
        default=None,
        description="URL to receive webhook notification when task completes",
    )
    webhook_format: str | None = Field(
        default=None,
        description="Webhook payload format: 'scout' (default) or 'slack'",
    )


class TaskIdInput(BaseModel):
    """Input for retrieving a browsing task result."""

    task_id: str = Field(..., description="The browsing task's unique identifier")
