"""Tests for input schemas."""

import pytest
from pydantic import ValidationError

from yutori_mcp.formatters import _SCOUT_EDIT_FIELDS
from yutori_mcp.schemas import (
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


class TestUsageInput:
    def test_default_period(self):
        """Period defaults to None."""
        data = UsageInput()
        assert data.period is None

    def test_valid_periods(self):
        """All valid period values are accepted."""
        for period in ("24h", "7d", "30d", "90d"):
            data = UsageInput(period=period)
            assert data.period == period

    def test_invalid_period_rejected(self):
        """Invalid period values are rejected."""
        with pytest.raises(ValidationError):
            UsageInput(period="1h")


class TestWebhookUrlValidation:
    """Tests for the shared _check_webhook_https validator across all schemas."""

    def test_http_url_rejected_create_scout(self):
        """CreateScoutInput rejects non-HTTPS webhook URLs."""
        with pytest.raises(ValidationError, match="webhook_url must use HTTPS"):
            CreateScoutInput(query="Test", webhook_url="http://example.com/webhook")

    def test_http_url_rejected_edit_scout(self):
        """EditScoutInput rejects non-HTTPS webhook URLs."""
        with pytest.raises(ValidationError, match="webhook_url must use HTTPS"):
            EditScoutInput(scout_id="abc-123", webhook_url="http://example.com/webhook")

    def test_http_url_rejected_browsing_task(self):
        """BrowsingTaskInput rejects non-HTTPS webhook URLs."""
        with pytest.raises(ValidationError, match="webhook_url must use HTTPS"):
            BrowsingTaskInput(
                task="Test",
                start_url="https://example.com",
                webhook_url="http://example.com/webhook",
            )

    def test_http_url_rejected_research_task(self):
        """ResearchTaskInput rejects non-HTTPS webhook URLs."""
        with pytest.raises(ValidationError, match="webhook_url must use HTTPS"):
            ResearchTaskInput(query="Test", webhook_url="http://example.com/webhook")

    def test_https_url_accepted(self):
        """HTTPS webhook URLs are accepted across all schemas."""
        data = CreateScoutInput(query="Test", webhook_url="https://example.com/webhook")
        assert data.webhook_url == "https://example.com/webhook"

    def test_none_url_accepted(self):
        """None webhook URL passes validation."""
        data = CreateScoutInput(query="Test", webhook_url=None)
        assert data.webhook_url is None


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
            start_timestamp=1737331200,
            user_location="New York, NY, US",
            is_public=False,
        )
        assert data.start_timestamp == 1737331200
        assert data.user_location == "New York, NY, US"
        assert data.is_public is False

    def test_output_fields(self):
        """output_fields accepts a list of field names."""
        data = CreateScoutInput(
            query="Track AI news",
            output_fields=["headline", "summary", "url"],
        )
        assert data.output_fields == ["headline", "summary", "url"]

    def test_output_fields_optional(self):
        """output_fields is optional and defaults to None."""
        data = CreateScoutInput(query="Track AI news")
        assert data.output_fields is None


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
        """New fields (user_timezone, user_location) work."""
        data = EditScoutInput(
            scout_id="abc-123",
            user_timezone="America/New_York",
            user_location="San Francisco, CA",
        )
        assert data.user_timezone == "America/New_York"
        assert data.user_location == "San Francisco, CA"

    def test_output_fields(self):
        """output_fields can be updated."""
        data = EditScoutInput(
            scout_id="abc-123",
            output_fields=["title", "description"],
        )
        assert data.output_fields == ["title", "description"]

    def test_diff_fields_match_schema(self):
        """_SCOUT_EDIT_FIELDS must list every editable field on EditScoutInput.

        Drift guard: when a new field is added to EditScoutInput, it must
        also be added to _SCOUT_EDIT_FIELDS in formatters.py so that edits
        to it appear in the per-field diff that format_scout_edited returns
        to the LLM client. Otherwise the change is silently invisible.
        """
        diff_fields = {field for field, _label, _fmt in _SCOUT_EDIT_FIELDS}
        editable_fields = set(EditScoutInput.model_fields) - {"scout_id"}
        assert diff_fields == editable_fields, (
            "Mismatch between _SCOUT_EDIT_FIELDS and EditScoutInput. "
            f"Missing from _SCOUT_EDIT_FIELDS: {editable_fields - diff_fields}. "
            f"Extra in _SCOUT_EDIT_FIELDS: {diff_fields - editable_fields}."
        )


class TestBrowsingTaskInput:
    def test_required_fields(self):
        """task and start_url are required."""
        data = BrowsingTaskInput(
            task="Find AAPL stock price",
            start_url="https://finance.yahoo.com",
        )
        assert data.task == "Find AAPL stock price"
        assert data.start_url == "https://finance.yahoo.com"
        assert data.require_auth is None
        assert data.browser is None

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

    def test_output_fields(self):
        """output_fields accepts a list of field names."""
        data = BrowsingTaskInput(
            task="Extract employee data",
            start_url="https://example.com/team",
            output_fields=["name", "title", "email"],
        )
        assert data.output_fields == ["name", "title", "email"]

    def test_output_fields_optional(self):
        """output_fields is optional."""
        data = BrowsingTaskInput(task="Test", start_url="https://example.com")
        assert data.output_fields is None

    def test_full_input(self):
        """Local browser, auth, and zapier webhook are accepted."""
        data = BrowsingTaskInput(
            task="Log in and export data",
            start_url="https://example.com/login",
            require_auth=True,
            browser="local",
            webhook_format="zapier",
        )
        assert data.require_auth is True
        assert data.browser == "local"
        assert data.webhook_format == "zapier"


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
            output_fields=["title", "summary", "source_url"],
            webhook_url="https://example.com/webhook",
            webhook_format="slack",
        )
        assert data.user_timezone == "America/New_York"
        assert data.user_location == "New York, NY, US"
        assert data.webhook_format == "slack"
        assert data.output_fields == ["title", "summary", "source_url"]

    def test_query_required(self):
        """query is required."""
        with pytest.raises(ValidationError):
            ResearchTaskInput()

    def test_output_fields_optional(self):
        """output_fields is optional."""
        data = ResearchTaskInput(query="Research AI")
        assert data.output_fields is None

class TestOutputFieldsDescription:
    """Tests for the _output_fields_description helper extracted in PR #94."""

    def test_without_docs_slug(self):
        """Output omits the trailing docs link when docs_slug is None."""
        from yutori_mcp.schemas import _output_fields_description

        result = _output_fields_description(["headline", "summary", "url"])
        assert "Optional: Extract structured data" in result
        assert "['headline', 'summary', 'url']" in result
        assert "https://docs.yutori.com" not in result
        assert not result.endswith(".")

    def test_with_docs_slug(self):
        """Output includes the full docs URL when docs_slug is provided."""
        from yutori_mcp.schemas import _output_fields_description

        result = _output_fields_description(
            ["name", "title", "email"],
            docs_slug="browsing-create#using-webhooks-and-a-structured-output-schema",
        )
        assert "['name', 'title', 'email']" in result
        assert (
            "https://docs.yutori.com/reference/browsing-create#using-webhooks-and-a-structured-output-schema"
            in result
        )
        assert result.endswith(".")

    def test_all_schemas_use_helper(self):
        """All four output_fields descriptions are produced by the helper."""
        from yutori_mcp.schemas import (
            BrowsingTaskInput,
            CreateScoutInput,
            EditScoutInput,
            ResearchTaskInput,
            _output_fields_description,
        )

        for model_cls in (CreateScoutInput, EditScoutInput, BrowsingTaskInput, ResearchTaskInput):
            desc = model_cls.model_fields["output_fields"].description
            assert desc is not None
            assert "Optional: Extract structured data" in desc, (
                f"{model_cls.__name__}.output_fields description does not use _output_fields_description"
            )


class TestInvalidBrowserRejected:
    """Verify that invalid browser values are rejected."""

    def test_browsing_task_rejects_invalid_browser(self):
        """BrowsingTaskInput rejects browser='remote'."""
        with pytest.raises(ValidationError):
            BrowsingTaskInput(
                task="Test task",
                start_url="https://example.com",
                browser="remote",
            )


class TestInvalidWebhookFormatRejected:
    """Verify that invalid webhook_format values are rejected."""

    def test_browsing_task_rejects_invalid_webhook_format(self):
        """BrowsingTaskInput rejects webhook_format='discord'."""
        with pytest.raises(ValidationError):
            BrowsingTaskInput(
                task="Test task",
                start_url="https://example.com",
                webhook_format="discord",
            )

    def test_research_task_rejects_invalid_webhook_format(self):
        """ResearchTaskInput rejects webhook_format='discord'."""
        with pytest.raises(ValidationError):
            ResearchTaskInput(
                query="Test query",
                webhook_format="discord",
            )

    def test_create_scout_rejects_invalid_webhook_format(self):
        """CreateScoutInput rejects webhook_format='discord'."""
        with pytest.raises(ValidationError):
            CreateScoutInput(
                query="Test query",
                webhook_format="discord",
            )

    def test_edit_scout_rejects_invalid_webhook_format(self):
        """EditScoutInput rejects webhook_format='discord'."""
        with pytest.raises(ValidationError):
            EditScoutInput(
                scout_id="abc-123",
                webhook_format="discord",
            )
