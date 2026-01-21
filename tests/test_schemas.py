"""Tests for input schemas."""

import pytest
from pydantic import ValidationError

from yutori_mcp.schemas import (
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
    ListScoutsInput,
    ResearchTaskInput,
    ScoutIdInput,
    TaskIdInput,
)


class TestCreateScoutInput:
    def test_minimal_input(self):
        """Query is the only required field."""
        data = CreateScoutInput(query="Track NVIDIA stock price")
        assert data.query == "Track NVIDIA stock price"
        assert data.output_interval is None

    def test_full_input(self):
        """All fields can be provided."""
        data = CreateScoutInput(
            query="Track NVIDIA stock price",
            output_interval=86400,
            webhook_url="https://example.com/webhook",
            webhook_format="slack",
            user_timezone="America/New_York",
            skip_email=True,
        )
        assert data.output_interval == 86400
        assert data.webhook_format == "slack"

    def test_output_interval_minimum(self):
        """Output interval must be at least 1800 seconds."""
        with pytest.raises(ValidationError):
            CreateScoutInput(query="Test", output_interval=1000)

    def test_new_fields(self):
        """New fields (start_timestamp, user_location, is_public) work."""
        data = CreateScoutInput(
            query="Track NVIDIA stock price",
            start_timestamp="2026-01-20T00:00:00Z",
            user_location="New York, NY, US",
            is_public=False,
        )
        assert data.start_timestamp == "2026-01-20T00:00:00Z"
        assert data.user_location == "New York, NY, US"
        assert data.is_public is False


class TestEditScoutInput:
    def test_scout_id_required(self):
        """scout_id is required."""
        with pytest.raises(ValidationError):
            EditScoutInput()

    def test_requires_at_least_one_change(self):
        """At least one field besides scout_id must be provided."""
        with pytest.raises(ValidationError) as exc_info:
            EditScoutInput(scout_id="abc-123")
        assert "at least one field" in str(exc_info.value).lower()

    def test_partial_update(self):
        """Only scout_id and changed fields needed."""
        data = EditScoutInput(scout_id="abc-123", output_interval=43200)
        assert data.scout_id == "abc-123"
        assert data.query is None

    def test_status_paused(self):
        """Status can be set to paused."""
        data = EditScoutInput(scout_id="abc-123", status="paused")
        assert data.status == "paused"

    def test_status_active(self):
        """Status can be set to active."""
        data = EditScoutInput(scout_id="abc-123", status="active")
        assert data.status == "active"

    def test_status_done(self):
        """Status can be set to done."""
        data = EditScoutInput(scout_id="abc-123", status="done")
        assert data.status == "done"

    def test_status_invalid(self):
        """Invalid status values are rejected."""
        with pytest.raises(ValidationError):
            EditScoutInput(scout_id="abc-123", status="completed")

    def test_status_with_config(self):
        """Status and config fields can be combined."""
        data = EditScoutInput(scout_id="abc-123", status="paused", query="new query")
        assert data.status == "paused"
        assert data.query == "new query"

    def test_new_fields(self):
        """New fields (user_timezone, user_location, is_public) work."""
        data = EditScoutInput(
            scout_id="abc-123",
            user_timezone="America/New_York",
            user_location="San Francisco, CA",
            is_public=True,
        )
        assert data.user_timezone == "America/New_York"
        assert data.user_location == "San Francisco, CA"
        assert data.is_public is True


class TestBrowsingTaskInput:
    def test_required_fields(self):
        """task and start_url are required."""
        data = BrowsingTaskInput(
            task="Find AAPL stock price",
            start_url="https://finance.yahoo.com",
        )
        assert data.task == "Find AAPL stock price"
        assert data.start_url == "https://finance.yahoo.com"

    def test_missing_task(self):
        """task is required."""
        with pytest.raises(ValidationError):
            BrowsingTaskInput(start_url="https://example.com")

    def test_max_steps_range(self):
        """max_steps must be between 1 and 100."""
        data = BrowsingTaskInput(
            task="Test",
            start_url="https://example.com",
            max_steps=50,
        )
        assert data.max_steps == 50

        with pytest.raises(ValidationError):
            BrowsingTaskInput(
                task="Test",
                start_url="https://example.com",
                max_steps=150,
            )


class TestScoutIdInput:
    def test_scout_id_required(self):
        """scout_id is required."""
        data = ScoutIdInput(scout_id="abc-123")
        assert data.scout_id == "abc-123"


class TestListScoutsInput:
    def test_default_values(self):
        """Default limit is 10, status is None."""
        data = ListScoutsInput()
        assert data.limit == 10
        assert data.status is None

    def test_custom_limit(self):
        """Custom limit can be set."""
        data = ListScoutsInput(limit=50)
        assert data.limit == 50

    def test_limit_range(self):
        """Limit must be between 1 and 100."""
        data = ListScoutsInput(limit=1)
        assert data.limit == 1

        data = ListScoutsInput(limit=100)
        assert data.limit == 100

        with pytest.raises(ValidationError):
            ListScoutsInput(limit=0)

        with pytest.raises(ValidationError):
            ListScoutsInput(limit=101)

    def test_status_filter(self):
        """Status filter works with valid values."""
        data = ListScoutsInput(status="active")
        assert data.status == "active"

        data = ListScoutsInput(status="paused")
        assert data.status == "paused"

        data = ListScoutsInput(status="done")
        assert data.status == "done"

    def test_status_invalid(self):
        """Invalid status values are rejected."""
        with pytest.raises(ValidationError):
            ListScoutsInput(status="completed")

    def test_combined_params(self):
        """Limit and status can be combined."""
        data = ListScoutsInput(limit=20, status="active")
        assert data.limit == 20
        assert data.status == "active"


class TestTaskIdInput:
    def test_task_id_required(self):
        """task_id is required."""
        data = TaskIdInput(task_id="task-456")
        assert data.task_id == "task-456"


class TestGetUpdatesInput:
    def test_scout_id_required(self):
        """scout_id is required."""
        data = GetUpdatesInput(scout_id="abc-123")
        assert data.scout_id == "abc-123"

    def test_pagination(self):
        """cursor and limit are optional."""
        data = GetUpdatesInput(
            scout_id="abc-123",
            cursor="next_page",
            limit=50,
        )
        assert data.cursor == "next_page"
        assert data.limit == 50


class TestResearchTaskInput:
    def test_minimal_input(self):
        """Query is the only required field."""
        data = ResearchTaskInput(query="Research quantum computing developments")
        assert data.query == "Research quantum computing developments"
        assert data.user_timezone is None
        assert data.user_location is None

    def test_full_input(self):
        """All fields can be provided."""
        data = ResearchTaskInput(
            query="Research quantum computing developments",
            user_timezone="America/New_York",
            user_location="New York, NY, US",
            task_spec={"output_schema": {"type": "json", "json_schema": {"type": "object"}}},
            webhook_url="https://example.com/webhook",
            webhook_format="slack",
        )
        assert data.user_timezone == "America/New_York"
        assert data.user_location == "New York, NY, US"
        assert data.webhook_format == "slack"

    def test_query_required(self):
        """query is required."""
        with pytest.raises(ValidationError):
            ResearchTaskInput()
