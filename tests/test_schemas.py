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
    ListTasksInput,
    ResearchTaskInput,
    ScoutIdInput,
    TaskIdInput,
    UsageInput,
    _output_fields_description,
)

# Minimal required kwargs for each input class that supports webhook/output_fields.
# Used by parametrized cross-class validation tests — add a row here when a new
# input class is introduced that shares these shared validators.
_ALL_INPUT_CLASSES = [
    pytest.param(CreateScoutInput, {"query": "Test"}, id="CreateScoutInput"),
    pytest.param(EditScoutInput, {"scout_id": "abc-123"}, id="EditScoutInput"),
    pytest.param(
        BrowsingTaskInput,
        {"task": "Test", "start_url": "https://example.com"},
        id="BrowsingTaskInput",
    ),
    pytest.param(ResearchTaskInput, {"query": "Test"}, id="ResearchTaskInput"),
]


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

    @pytest.mark.parametrize("cls,required_kwargs", _ALL_INPUT_CLASSES)
    def test_http_url_rejected(self, cls, required_kwargs):
        """Every input class rejects non-HTTPS webhook URLs."""
        with pytest.raises(ValidationError, match="webhook_url must use HTTPS"):
            cls(**required_kwargs, webhook_url="http://example.com/webhook")

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

    @pytest.mark.parametrize("status", ["paused", "active", "done"])
    def test_status_valid(self, status):
        """Status can be set to any of the valid literal values."""
        data = EditScoutInput(scout_id="abc-123", status=status)
        assert data.status == status

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
        # The output_fields input is sent to (and returned by) the API as
        # output_schema; the diff table uses the response-side name.
        editable_fields = (editable_fields - {"output_fields"}) | {"output_schema"}
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

    def test_cursor_defaults_none_and_is_settable(self):
        """Cursor defaults to None and accepts a pagination token."""
        assert ListScoutsInput().cursor is None
        assert ListScoutsInput(cursor="next-page").cursor == "next-page"


class TestListTasksInput:
    def test_default_values(self):
        """Default limit is 10, status and cursor are None."""
        data = ListTasksInput()
        assert data.limit == 10
        assert data.status is None
        assert data.cursor is None

    def test_custom_limit_and_cursor(self):
        """Limit and cursor can be set together."""
        data = ListTasksInput(limit=50, cursor="next-page")
        assert data.limit == 50
        assert data.cursor == "next-page"

    def test_limit_range(self):
        """Limit must be between 1 and 100."""
        assert ListTasksInput(limit=1).limit == 1
        assert ListTasksInput(limit=100).limit == 100

        with pytest.raises(ValidationError):
            ListTasksInput(limit=0)

        with pytest.raises(ValidationError):
            ListTasksInput(limit=101)

    def test_status_filter(self):
        """Status filter accepts the task-list statuses from the REST API."""
        for status in ("running", "succeeded", "failed"):
            assert ListTasksInput(status=status).status == status

    def test_status_invalid(self):
        """Detail-only statuses are rejected for list filters."""
        with pytest.raises(ValidationError):
            ListTasksInput(status="queued")


class TestCursorFieldSharedDescription:
    """ListScoutsInput and ListTasksInput share the same `_cursor_field()` construction.

    Drift guard: both schemas' `cursor` field must keep the identical
    description text so the two call sites cannot silently diverge.
    """

    def test_descriptions_match(self):
        assert (
            ListScoutsInput.model_fields["cursor"].description
            == ListTasksInput.model_fields["cursor"].description
        )


class TestScoutIdFieldSharedDescription:
    """EditScoutInput, ScoutIdInput, and GetUpdatesInput share `_scout_id_field()`.

    Drift guard: all three schemas' `scout_id` field must keep the identical
    description text (and stay required) so the call sites cannot silently
    diverge.
    """

    def test_descriptions_match(self):
        description = EditScoutInput.model_fields["scout_id"].description
        assert ScoutIdInput.model_fields["scout_id"].description == description
        assert GetUpdatesInput.model_fields["scout_id"].description == description

    @pytest.mark.parametrize(
        "cls,extra_kwargs",
        [
            pytest.param(EditScoutInput, {"query": "q"}, id="EditScoutInput"),
            pytest.param(ScoutIdInput, {}, id="ScoutIdInput"),
            pytest.param(GetUpdatesInput, {}, id="GetUpdatesInput"),
        ],
    )
    def test_scout_id_required(self, cls, extra_kwargs):
        with pytest.raises(ValidationError):
            cls(**extra_kwargs)


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
        result = _output_fields_description(["headline", "summary", "url"])
        assert "Optional: Extract structured data" in result
        assert "['headline', 'summary', 'url']" in result
        assert "https://docs.yutori.com" not in result
        assert not result.endswith(".")

    def test_with_docs_slug(self):
        """Output includes the full docs URL when docs_slug is provided."""
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
        for model_cls in (
            CreateScoutInput,
            EditScoutInput,
            BrowsingTaskInput,
            ResearchTaskInput,
        ):
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


@pytest.mark.parametrize("cls,required_kwargs", _ALL_INPUT_CLASSES)
def test_invalid_webhook_format_rejected(cls, required_kwargs):
    """Every input class rejects invalid webhook_format values."""
    with pytest.raises(ValidationError):
        cls(**required_kwargs, webhook_format="discord")


class TestExtraFieldsRejected:
    """Unknown arguments must fail validation instead of being silently dropped."""

    def test_create_scout_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            CreateScoutInput(query="Test", frequency="daily")


@pytest.mark.parametrize("cls,required_kwargs", _ALL_INPUT_CLASSES)
def test_empty_output_fields_rejected(cls, required_kwargs):
    """An empty output_fields list would produce a degenerate output schema."""
    with pytest.raises(ValidationError):
        cls(**required_kwargs, output_fields=[])


class TestWebhookUrlRequiresHost:
    def test_https_without_host_rejected(self):
        with pytest.raises(ValidationError, match="webhook_url must use HTTPS"):
            CreateScoutInput(query="Test", webhook_url="https://")

    def test_https_with_host_accepted(self):
        data = CreateScoutInput(query="Test", webhook_url="https://example.com")
        assert data.webhook_url == "https://example.com"


class TestEditScoutIsPublic:
    def test_is_public_accepted(self):
        """is_public is editable (SDK >=0.8.0 forwards it to the PATCH route)."""
        data = EditScoutInput(scout_id="abc-123", is_public=False)
        assert data.is_public is False
