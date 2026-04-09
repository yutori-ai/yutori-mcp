"""Tests for output formatters."""

from yutori_mcp.formatters import (
    dict_to_markdown,
    format_list_scouts,
    format_response,
    format_scout_created,
    format_scout_deleted,
    format_scout_detail,
    format_scout_edited,
    format_scout_updates,
    format_task_result,
    format_task_started,
    format_usage,
)


class TestDictToMarkdown:
    def test_simple_dict(self):
        """Simple dict converts to key: value lines."""
        result = dict_to_markdown({"name": "John", "age": 30})
        assert "name: John" in result
        assert "age: 30" in result

    def test_nested_dict(self):
        """Nested dicts are indented."""
        result = dict_to_markdown({"user": {"name": "John", "age": 30}})
        assert "user:" in result
        assert "name: John" in result

    def test_list_of_dicts(self):
        """Lists of dicts use bullet format."""
        result = dict_to_markdown([{"name": "John"}, {"name": "Jane"}])
        assert "- name: John" in result
        assert "- name: Jane" in result

    def test_empty_values_skipped(self):
        """Empty strings and None values are skipped."""
        result = dict_to_markdown({"name": "John", "email": "", "phone": None})
        assert "name: John" in result
        assert "email" not in result
        assert "phone" not in result


class TestFormatUsage:
    USAGE_RESPONSE = {
        "num_active_scouts": 3,
        "active_scout_ids": ["id-1", "id-2", "id-3"],
        "rate_limits": {
            "requests_today": 500,
            "daily_limit": 10000,
            "remaining_requests": 9500,
            "reset_at": "2026-03-04T00:00:00+00:00",
            "status": "available",
        },
        "n1_rate_limits": {
            "requests_today": 100,
            "daily_limit": 50000,
            "remaining_requests": 49900,
            "reset_at": "2026-03-04T00:00:00+00:00",
            "per_second_limit": 20,
        },
        "activity": {
            "period": "7d",
            "scout_runs": 21,
            "browsing_tasks": 5,
            "research_tasks": 3,
            "n1_calls": 800,
        },
    }

    def test_active_scouts_shown(self):
        result = format_usage(self.USAGE_RESPONSE)
        assert "Active Scouts: 3" in result
        assert "id-1" in result
        assert "id-2" in result

    def test_rate_limits_shown(self):
        result = format_usage(self.USAGE_RESPONSE)
        assert "API Rate Limits (available)" in result
        assert "Requests today: 500" in result
        assert "Daily limit: 10000" in result
        assert "Remaining: 9500" in result

    def test_n1_rate_limits_shown(self):
        result = format_usage(self.USAGE_RESPONSE)
        assert "n1 API Rate Limits" in result
        assert "Requests today: 100" in result
        assert "Per-second limit: 20" in result

    def test_activity_shown(self):
        result = format_usage(self.USAGE_RESPONSE)
        assert "Activity (7d)" in result
        assert "Scout runs: 21" in result
        assert "Browsing tasks: 5" in result
        assert "Research tasks: 3" in result
        assert "n1 API calls: 800" in result

    def test_unavailable_rate_limits(self):
        """When rate limits are unavailable, don't show request counts."""
        response = {
            **self.USAGE_RESPONSE,
            "rate_limits": {
                "requests_today": None,
                "daily_limit": None,
                "remaining_requests": None,
                "reset_at": "2026-03-04T00:00:00+00:00",
                "status": "unavailable",
            },
        }
        result = format_usage(response)
        assert "API Rate Limits (unavailable)" in result
        # Should not show request counts when unavailable
        assert "Requests today: None" not in result

    def test_many_active_scouts_truncated(self):
        """More than 5 active scouts shows truncation hint."""
        response = {
            **self.USAGE_RESPONSE,
            "num_active_scouts": 8,
            "active_scout_ids": [f"id-{i}" for i in range(8)],
        }
        result = format_usage(response)
        assert "Active Scouts: 8" in result
        assert "... and 3 more" in result

    def test_format_response_routes_to_usage(self):
        """format_response correctly routes list_api_usage."""
        result = format_response("list_api_usage", self.USAGE_RESPONSE)
        assert "Active Scouts: 3" in result


class TestFormatListScouts:
    def test_empty_list(self):
        """Empty scouts list shows appropriate message."""
        response = {"scouts": [], "total": 0, "summary": {"active": 0, "paused": 0, "done": 0}}
        result = format_list_scouts(response)
        assert "Found 0 scouts" in result
        assert "No scouts to display" in result

    def test_with_scouts(self):
        """Scouts are listed with key details."""
        response = {
            "scouts": [
                {
                    "id": "abc-123",
                    "display_name": "Test Scout",
                    "query": "monitor something",
                    "status": "active",
                    "output_interval": 86400,
                    "next_output_timestamp": "2026-01-21T05:00:00Z",
                }
            ],
            "total": 1,
            "summary": {"active": 1, "paused": 0, "done": 0},
            "has_more": False,
        }
        result = format_list_scouts(response)
        assert "Found 1 scouts" in result
        assert "Test Scout" in result
        assert "abc-123" in result
        assert "active" in result

    def test_has_more_hint(self):
        """When has_more is true, shows hint to increase limit."""
        response = {
            "scouts": [{"id": "abc", "query": "test", "status": "active"}],
            "total": 50,
            "summary": {"active": 50, "paused": 0, "done": 0},
            "has_more": True,
        }
        result = format_list_scouts(response)
        assert "limit=50" in result

    def test_shows_rejection_reason(self):
        """Scout list includes rejection reason when present."""
        response = {
            "scouts": [
                {
                    "id": "abc-123",
                    "query": "monitor something",
                    "status": "paused",
                    "rejection_reason": "invalid_query",
                }
            ],
            "total": 1,
            "summary": {"active": 0, "paused": 1, "done": 0},
        }
        result = format_list_scouts(response)
        assert "Rejection reason: invalid_query" in result


class TestFormatScoutDetail:
    def test_full_detail(self):
        """All scout details are formatted."""
        response = {
            "id": "abc-123",
            "display_name": "My Scout",
            "query": "monitor AI news",
            "status": "active",
            "output_interval": 86400,
            "next_output_timestamp": "2026-01-21T05:00:00Z",
            "user_timezone": "America/New_York",
            "created_at": "2026-01-01T00:00:00Z",
            "skip_email": False,
            "is_public": True,
        }
        result = format_scout_detail(response)
        assert "Scout: My Scout" in result
        assert "ID: abc-123" in result
        assert "Status: active" in result
        assert "monitor AI news" in result
        assert "daily" in result
        assert "Email notifications: enabled" in result
        assert "Public: yes" in result

    def test_shows_rejection_reason(self):
        """Scout detail includes rejection reason when present."""
        response = {
            "id": "abc-123",
            "query": "monitor AI news",
            "status": "paused",
            "rejection_reason": "invalid_query",
        }
        result = format_scout_detail(response)
        assert "Rejection reason: invalid_query" in result

    def test_shows_sources(self):
        """Scout detail shows sources when present."""
        response = {
            "id": "abc-123",
            "display_name": "My Scout",
            "query": "monitor AI news",
            "status": "active",
            "sources": [
                {"url": "https://example.com", "title": "Example"},
                "https://plain-url.com",
            ],
        }
        result = format_scout_detail(response)
        assert "Sources:" in result
        assert "  - Example: https://example.com" in result
        assert "  - https://plain-url.com" in result

    def test_shows_citations_fallback(self):
        """Scout detail falls back to 'citations' key when 'sources' is absent."""
        response = {
            "id": "abc-123",
            "query": "monitor AI news",
            "status": "active",
            "citations": [{"url": "https://cite.com", "title": "Cite"}],
        }
        result = format_scout_detail(response)
        assert "Sources:" in result
        assert "  - Cite: https://cite.com" in result

    def test_sources_truncated_at_10(self):
        """Sources list is capped at 10 entries with overflow message."""
        response = {
            "id": "abc-123",
            "query": "test",
            "status": "active",
            "sources": [{"url": f"https://s{i}.com", "title": f"S{i}"} for i in range(15)],
        }
        result = format_scout_detail(response)
        assert "S9" in result
        assert "S10" not in result
        assert "... and 5 more" in result


class TestFormatScoutCreated:
    def test_created_confirmation(self):
        """Creation shows confirmation with key details."""
        response = {
            "id": "new-scout-123",
            "display_name": "New Scout",
            "query": "track something",
            "status": "active",
            "output_interval": 86400,
            "next_output_timestamp": "2026-01-21T00:00:00Z",
        }
        result = format_scout_created(response)
        assert "Scout created successfully" in result
        assert "new-scout-123" in result
        assert "New Scout" in result


class TestFormatScoutEdited:
    def test_with_diff(self):
        """Shows changes when old and new state provided."""
        response = {
            "old": {"id": "abc", "status": "active", "query": "old query", "output_interval": 86400},
            "new": {"id": "abc", "status": "paused", "query": "new query", "output_interval": 86400},
        }
        result = format_scout_edited(response)
        assert "Scout updated successfully" in result
        assert "Status:" in result
        assert "active" in result
        assert "paused" in result
        assert "Query:" in result

    def test_no_changes(self):
        """Handles case where no changes detected."""
        response = {
            "old": {"id": "abc", "status": "active"},
            "new": {"id": "abc", "status": "active"},
        }
        result = format_scout_edited(response)
        assert "no changes detected" in result


class TestFormatScoutDeleted:
    def test_deletion_confirmation(self):
        """Shows deletion confirmation with ID."""
        response = {}
        result = format_scout_deleted(response, scout_id="deleted-123")
        assert "Scout deleted" in result
        assert "deleted-123" in result
        assert "cannot be undone" in result


class TestFormatScoutUpdates:
    def test_no_updates(self):
        """Empty updates shows appropriate message."""
        response = {"updates": []}
        result = format_scout_updates(response)
        assert "No updates found" in result

    def test_with_updates(self):
        """Updates are formatted with timestamps."""
        response = {
            "updates": [
                {"created_at": "2026-01-20T05:00:00Z", "content": "Found some results"},
                {"created_at": "2026-01-19T05:00:00Z", "content": "No new findings"},
            ],
            "has_more": False,
        }
        result = format_scout_updates(response)
        assert "Found 2 update(s)" in result
        assert "Update #1" in result
        assert "Update #2" in result

    def test_with_unix_timestamp(self):
        """Updates with Unix timestamp in milliseconds are formatted correctly."""
        response = {
            "updates": [
                {"timestamp": 1769997854699, "content": "Update with Unix timestamp"},
            ],
            "has_more": False,
        }
        result = format_scout_updates(response)
        assert "Found 1 update(s)" in result
        assert "2026-02-02 02:04 UTC" in result


class TestFormatTaskStarted:
    def test_research_task(self):
        """Research task shows ID and poll hint."""
        response = {"task_id": "task-abc", "status": "queued"}
        result = format_task_started(response, task_type="Research")
        assert "Research task started" in result
        assert "task-abc" in result
        assert "get_research_task_result" in result

    def test_browsing_task(self):
        """Browsing task shows ID and poll hint."""
        response = {"task_id": "task-xyz", "status": "queued", "view_url": "https://yutori.com/tasks/xyz"}
        result = format_task_started(response, task_type="Browsing")
        assert "Browsing task started" in result
        assert "task-xyz" in result
        assert "get_browsing_task_result" in result
        assert "https://yutori.com/tasks/xyz" in result

    def test_shows_rejection_reason(self):
        """Failed create output includes rejection reason when present."""
        response = {
            "task_id": "task-abc",
            "status": "failed",
            "rejection_reason": "billing_limit_reached",
        }
        result = format_task_started(response, task_type="Research")
        assert "Research task failed to start" in result
        assert "Rejection reason: billing_limit_reached" in result
        assert "Poll with" not in result


class TestFormatTaskResult:
    def test_in_progress(self):
        """In-progress task shows status and poll hint."""
        response = {"task_id": "task-abc", "status": "running"}
        result = format_task_result(response)
        assert "Task in progress" in result
        assert "running" in result
        assert "Poll again" in result

    def test_completed(self):
        """Completed task shows result."""
        response = {"task_id": "task-abc", "status": "succeeded", "result": "Here are the findings..."}
        result = format_task_result(response)
        assert "Task completed" in result
        assert "succeeded" in result
        assert "Here are the findings" in result

    def test_failed_shows_rejection_reason(self):
        """Failed task output includes rejection reason when present."""
        response = {
            "task_id": "task-abc",
            "status": "failed",
            "error": "Rejected",
            "rejection_reason": "billing_limit_reached",
        }
        result = format_task_result(response)
        assert "Task failed" in result
        assert "Rejection reason: billing_limit_reached" in result

    def test_failed(self):
        """Failed task shows error."""
        response = {"task_id": "task-abc", "status": "failed", "error": "Something went wrong"}
        result = format_task_result(response)
        assert "Task failed" in result
        assert "Something went wrong" in result

    def test_completed_with_sources(self):
        """Completed task includes sources without extra indent."""
        response = {
            "task_id": "task-abc",
            "status": "succeeded",
            "result": "Some findings",
            "sources": [
                {"url": "https://example.com", "title": "Example"},
                "https://plain.com",
            ],
        }
        result = format_task_result(response)
        assert "Sources:" in result
        assert "- Example: https://example.com" in result
        assert "- https://plain.com" in result
        # Ensure no extra indent (unlike scout detail which uses "  -")
        for line in result.split("\n"):
            if "Example:" in line:
                assert line.startswith("- "), f"Expected no indent, got: {line!r}"


class TestFormatResponse:
    def test_routes_to_correct_formatter(self):
        """format_response routes to the right formatter."""
        # Test list_scouts routing
        response = {"scouts": [], "total": 0, "summary": {"active": 0, "paused": 0, "done": 0}}
        result = format_response("list_scouts", response)
        assert "Found 0 scouts" in result

    def test_unknown_tool_uses_dict_to_markdown(self):
        """Unknown tools fall back to dict_to_markdown."""
        response = {"some": "data", "nested": {"key": "value"}}
        result = format_response("unknown_tool", response)
        assert "some: data" in result
        assert "key: value" in result
