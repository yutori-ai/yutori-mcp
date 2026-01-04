"""Tests for input schemas."""

import pytest
from pydantic import ValidationError

from yutori_mcp.schemas import (
    BrowsingTaskInput,
    CreateScoutInput,
    EditScoutInput,
    GetUpdatesInput,
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


class TestEditScoutInput:
    def test_scout_id_required(self):
        """scout_id is required."""
        with pytest.raises(ValidationError):
            EditScoutInput()

    def test_partial_update(self):
        """Only scout_id and changed fields needed."""
        data = EditScoutInput(scout_id="abc-123", output_interval=43200)
        assert data.scout_id == "abc-123"
        assert data.query is None


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
