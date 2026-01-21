"""Input schemas for MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
        description="Webhook payload format: 'scout' (default), 'slack', or 'zapier'",
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
    start_timestamp: str | None = Field(
        default=None,
        description="ISO timestamp for when monitoring should start",
    )
    user_location: str | None = Field(
        default=None,
        description="User location for geo-relevant searches. Format: 'city, region, country'",
    )
    is_public: bool | None = Field(
        default=None,
        description="Whether scout results are publicly accessible",
    )


class EditScoutInput(BaseModel):
    """Input for editing an existing scout or changing its status."""

    scout_id: str = Field(..., description="The scout's unique identifier (UUID)")
    status: Literal["active", "paused", "done"] | None = Field(
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
        description="Updated webhook format: 'scout', 'slack', or 'zapier'",
    )
    task_spec: dict[str, Any] | None = Field(
        default=None,
        description="Updated JSON Schema for structured output",
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
    is_public: bool | None = Field(
        default=None,
        description="Whether scout results are publicly accessible",
    )

    @model_validator(mode="after")
    def validate_has_changes(self) -> "EditScoutInput":
        """Ensure at least one field besides scout_id is provided."""
        fields = [
            self.status,
            self.query,
            self.output_interval,
            self.webhook_url,
            self.webhook_format,
            self.task_spec,
            self.skip_email,
            self.user_timezone,
            self.user_location,
            self.is_public,
        ]
        if not any(f is not None for f in fields):
            raise ValueError("edit_scout requires at least one field to update")
        return self


class ScoutIdInput(BaseModel):
    """Input for operations on a specific scout."""

    scout_id: str = Field(..., description="The scout's unique identifier (UUID)")


class ListScoutsInput(BaseModel):
    """Input for listing scouts with optional limit and filtering."""

    limit: int | None = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of scouts to return (1-100). Default: 10",
    )
    status: Literal["active", "paused", "done"] | None = Field(
        default=None,
        description="Filter by status: 'active', 'paused', or 'done'",
    )


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
    """Input for retrieving a browsing or research task result."""

    task_id: str = Field(..., description="The task's unique identifier")


class ResearchTaskInput(BaseModel):
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
        description="Location for contextual awareness. Format: 'city, region, country'. Default: 'San Francisco, CA, US'",
    )
    task_spec: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for structured output format",
    )
    webhook_url: str | None = Field(
        default=None,
        description="URL to receive webhook notification when research completes",
    )
    webhook_format: str | None = Field(
        default=None,
        description="Webhook payload format: 'scout' (default), 'slack', or 'zapier'",
    )
