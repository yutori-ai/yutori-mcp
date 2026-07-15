"""Tests for output formatters."""

import pytest

from yutori_mcp.formatters import (
    _EXTERNAL_CONTENT_END,
    _EXTERNAL_CONTENT_START,
    _format_date,
    _format_datetime,
    _format_output_fields_diff,
    _format_sources,
    dict_to_markdown,
    format_list_scouts,
    format_response,
    format_scout_created,
    format_scout_deleted,
    format_scout_detail,
    format_scout_edited,
    format_scout_updates,
    format_task_list,
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
    # Current server emits both navigator_* (primary) and n1_* (deprecated alias) with equal values.
    _NAVIGATOR_LIMITS = {
        "requests_today": 100,
        "daily_limit": 50000,
        "remaining_requests": 49900,
        "reset_at": "2026-03-04T00:00:00+00:00",
        "per_second_limit": 20,
    }
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
        "navigator_rate_limits": _NAVIGATOR_LIMITS,
        "n1_rate_limits": _NAVIGATOR_LIMITS,
        "activity": {
            "period": "7d",
            "scout_runs": 21,
            "browsing_tasks": 5,
            "research_tasks": 3,
            "navigator_calls": 800,
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

    def test_navigator_rate_limits_shown(self):
        result = format_usage(self.USAGE_RESPONSE)
        assert "Navigator API Rate Limits" in result
        assert "Requests today: 100" in result
        assert "Per-second limit: 20" in result

    def test_navigator_rate_limits_fallback_to_deprecated_n1_key(self):
        """If only the deprecated n1_rate_limits field is present, still render the section."""
        response = {
            k: v for k, v in self.USAGE_RESPONSE.items() if k != "navigator_rate_limits"
        }
        result = format_usage(response)
        assert "Navigator API Rate Limits" in result
        assert "Requests today: 100" in result

    def test_activity_shown(self):
        result = format_usage(self.USAGE_RESPONSE)
        assert "Activity (7d)" in result
        assert "Scout runs: 21" in result
        assert "Browsing tasks: 5" in result
        assert "Research tasks: 3" in result
        assert "Navigator API calls: 800" in result

    def test_activity_falls_back_to_deprecated_n1_calls_key(self):
        """Older servers may only emit activity.n1_calls."""
        response = {
            **self.USAGE_RESPONSE,
            "activity": {
                "period": "7d",
                "scout_runs": 21,
                "browsing_tasks": 5,
                "research_tasks": 3,
                "n1_calls": 42,
            },
        }
        result = format_usage(response)
        assert "Navigator API calls: 42" in result

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
        response = {
            "scouts": [],
            "total": 0,
            "summary": {"active": 0, "paused": 0, "done": 0},
        }
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

    def test_summary_breakdown_line_exact(self):
        """Pin the exact 'Found N scouts: a active, b paused, c done.' summary line."""
        response = {
            "scouts": [{"id": "abc", "query": "test", "status": "active"}],
            "total": 6,
            "summary": {"active": 3, "paused": 2, "done": 1},
        }
        result = format_list_scouts(response)
        assert "Found 6 scouts: 3 active, 2 paused, 1 done." in result

    def test_summary_breakdown_shown_even_when_summary_missing(self):
        """Unlike format_task_list, a missing summary still renders an all-zero breakdown."""
        response = {
            "scouts": [{"id": "abc", "query": "test", "status": "active"}],
            "total": 1,
        }
        result = format_list_scouts(response)
        assert "Found 1 scouts: 0 active, 0 paused, 0 done." in result

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

    def test_cursor_hint_when_next_cursor_present(self):
        """has_more with a next_cursor surfaces a cursor-pagination hint."""
        response = {
            "scouts": [{"id": "abc", "query": "test", "status": "active"}],
            "total": 50,
            "summary": {"active": 50, "paused": 0, "done": 0},
            "has_more": True,
            "next_cursor": "scout-cur-2",
        }
        result = format_list_scouts(response)
        assert 'list_scouts(cursor="scout-cur-2")' in result

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

    def test_explicit_null_status_still_renders_status_line(self):
        """An explicit ``"status": null`` in the payload still renders ``Status: None``.

        Regression test: ``response.get("status", "unknown")`` returns ``None``
        when the key is present with a null value (the default is only used
        when the key is missing), and the pre-refactor inline code formatted
        that as ``Status: None``.
        """
        response = {"id": "abc-123", "display_name": "n", "query": "q", "status": None}
        assert "Status: None" in format_scout_detail(response)


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
            "old": {
                "id": "abc",
                "status": "active",
                "query": "old query",
                "output_interval": 86400,
            },
            "new": {
                "id": "abc",
                "status": "paused",
                "query": "new query",
                "output_interval": 86400,
            },
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
        response = {
            "task_id": "task-xyz",
            "status": "queued",
            "view_url": "https://yutori.com/tasks/xyz",
        }
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


class TestFormatTaskList:
    def test_empty_browsing_task_list(self):
        """Empty browsing task lists show the summary and empty message."""
        response = {
            "tasks": [],
            "total": 0,
            "filtered_total": 0,
            "summary": {"running": 0, "succeeded": 0, "failed": 0},
        }
        result = format_task_list(response, task_type="Browsing")

        assert "Found 0 browsing tasks" in result
        assert "No browsing tasks to display" in result

    def test_research_task_list_with_pagination(self):
        """Research task lists include task metadata and cursor hint."""
        response = {
            "tasks": [
                {
                    "task_id": "task-1",
                    "query": "Research GPU pricing",
                    "status": "succeeded",
                    "created_at": "2026-06-25T12:00:00Z",
                    "view_url": "https://platform.yutori.com/research/tasks/task-1",
                }
            ],
            "total": 12,
            "filtered_total": 8,
            "summary": {"running": 2, "succeeded": 8, "failed": 2},
            "has_more": True,
            "next_cursor": "cursor-2",
        }
        result = format_task_list(response, task_type="Research")

        assert "Found 12 research tasks: 2 running, 8 succeeded, 2 failed." in result
        assert "Showing 1 of 8 matching tasks (12 total):" in result
        assert "Research GPU pricing" in result
        assert "task-1" in result
        assert "https://platform.yutori.com/research/tasks/task-1" in result
        assert 'list_research_tasks(cursor="cursor-2")' in result
        assert 'list_research_tasks(status="succeeded")' in result
        assert "get_research_task_result(task_id)" in result

    def test_task_list_shows_rejection_reason(self):
        """Failed task list entries include rejection reason when available."""
        response = {
            "tasks": [
                {
                    "task_id": "task-2",
                    "query": "Export invoice",
                    "status": "failed",
                    "created_at": "2026-06-25T12:00:00Z",
                    "rejection_reason": "insufficient_prepaid_balance",
                }
            ],
            "total": 1,
            "summary": {"running": 0, "succeeded": 0, "failed": 1},
        }
        result = format_task_list(response, task_type="Browsing")

        assert "Rejection reason: insufficient_prepaid_balance" in result

    def test_showing_all_when_complete(self):
        """No filter and no more pages -> 'Showing all N' with no pagination hint."""
        response = {
            "tasks": [
                {"task_id": "t1", "query": "a", "status": "succeeded"},
                {"task_id": "t2", "query": "b", "status": "succeeded"},
            ],
            "total": 2,
            "filtered_total": 2,
            "summary": {"running": 0, "succeeded": 2, "failed": 0},
            "has_more": False,
        }
        result = format_task_list(response, task_type="Browsing")

        assert "Showing all 2:" in result
        assert "More tasks available" not in result

    def test_has_more_without_filter(self):
        """has_more with filtered_total == total -> unfiltered 'Showing N of T' + cursor hint."""
        response = {
            "tasks": [{"task_id": "t1", "query": "a", "status": "running"}],
            "total": 20,
            "filtered_total": 20,
            "summary": {"running": 20, "succeeded": 0, "failed": 0},
            "has_more": True,
            "next_cursor": "cursor-2",
        }
        result = format_task_list(response, task_type="Browsing")

        assert "Showing 1 of 20:" in result
        assert "matching tasks" not in result
        assert 'list_browsing_tasks(cursor="cursor-2")' in result

    def test_no_cursor_hint_when_next_cursor_missing(self):
        """has_more but no next_cursor -> no 'More tasks available' line (avoid cursor='None')."""
        response = {
            "tasks": [{"task_id": "t1", "query": "a", "status": "running"}],
            "total": 5,
            "filtered_total": 5,
            "summary": {"running": 5, "succeeded": 0, "failed": 0},
            "has_more": True,
        }
        result = format_task_list(response, task_type="Research")

        assert "More tasks available" not in result
        # Static hints are still present.
        assert 'list_research_tasks(status="succeeded")' in result
        assert "get_research_task_result(task_id)" in result

    def test_minimal_response_without_total_or_summary(self):
        """A payload with only `tasks` renders without crashing and omits a zeroed breakdown."""
        response = {"tasks": [{"task_id": "t1", "query": "q", "status": "succeeded"}]}
        result = format_task_list(response, task_type="Browsing")

        # No summary -> count only, not a misleading "0 running, 0 succeeded, 0 failed".
        assert "Found 1 browsing tasks." in result
        assert "0 succeeded" not in result
        assert "Showing all 1:" in result

    def test_null_summary_does_not_crash(self):
        """An explicit `summary: null` must not raise (regression guard for `or {}`)."""
        response = {"tasks": [{"task_id": "t1", "query": "q", "status": "succeeded"}], "summary": None}
        result = format_task_list(response, task_type="Research")

        assert "Found 1 research tasks." in result


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
        response = {
            "task_id": "task-abc",
            "status": "succeeded",
            "result": "Here are the findings...",
        }
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
        response = {
            "task_id": "task-abc",
            "status": "failed",
            "error": "Something went wrong",
        }
        result = format_task_result(response)
        assert "Task failed" in result
        assert "Something went wrong" in result


class TestFormatResponse:
    def test_routes_to_correct_formatter(self):
        """format_response routes to the right formatter."""
        # Test list_scouts routing
        response = {
            "scouts": [],
            "total": 0,
            "summary": {"active": 0, "paused": 0, "done": 0},
        }
        result = format_response("list_scouts", response)
        assert "Found 0 scouts" in result

    def test_unknown_tool_uses_dict_to_markdown(self):
        """Unknown tools fall back to dict_to_markdown."""
        response = {"some": "data", "nested": {"key": "value"}}
        result = format_response("unknown_tool", response)
        assert "some: data" in result
        assert "key: value" in result


class TestFormatSources:
    def test_no_sources(self):
        """Returns empty list when no sources or citations."""
        assert _format_sources({}) == []
        assert _format_sources({"sources": None}) == []
        assert _format_sources({"sources": []}) == []

    def test_dict_sources_with_url_and_title(self):
        """Dict sources format as 'title: url'."""
        response = {
            "sources": [
                {"url": "https://example.com", "title": "Example"},
                {"url": "https://other.com", "title": "Other"},
            ]
        }
        lines = _format_sources(response)
        assert "Sources:" in lines
        assert "  - Example: https://example.com" in lines
        assert "  - Other: https://other.com" in lines

    def test_dict_source_without_title_uses_url(self):
        """Dict source with no title falls back to url."""
        response = {"sources": [{"url": "https://example.com"}]}
        lines = _format_sources(response)
        assert "  - https://example.com: https://example.com" in lines

    def test_string_sources(self):
        """String sources are formatted as-is."""
        response = {"sources": ["https://example.com", "https://other.com"]}
        lines = _format_sources(response)
        assert "  - https://example.com" in lines
        assert "  - https://other.com" in lines

    def test_citations_key(self):
        """Also works with 'citations' key."""
        response = {"citations": [{"url": "https://example.com", "title": "Cited"}]}
        lines = _format_sources(response)
        assert "  - Cited: https://example.com" in lines

    def test_truncation_at_max_items(self):
        """Sources beyond max_items are truncated with count."""
        response = {
            "sources": [
                {"url": f"https://{i}.com", "title": f"S{i}"} for i in range(15)
            ]
        }
        lines = _format_sources(response, max_items=10)
        assert "  ... and 5 more" in lines
        # Should have header + 10 items + truncation line + blank line prefix
        source_lines = [line for line in lines if line.startswith("  - ")]
        assert len(source_lines) == 10

    def test_custom_indent(self):
        """Custom indent is applied to all source lines."""
        response = {"sources": [{"url": "https://example.com", "title": "Ex"}]}
        lines = _format_sources(response, indent="")
        assert "- Ex: https://example.com" in lines
        # No leading spaces
        assert "  - Ex: https://example.com" not in lines

    def test_starts_with_blank_line(self):
        """Output starts with a blank line for spacing."""
        response = {"sources": [{"url": "https://example.com", "title": "Ex"}]}
        lines = _format_sources(response)
        assert lines[0] == ""
        assert lines[1] == "Sources:"

    def test_scout_detail_includes_sources(self):
        """format_scout_detail includes sources via _format_sources."""
        response = {
            "id": "abc-123",
            "query": "test",
            "status": "active",
            "sources": [
                {"url": "https://example.com", "title": "Example Source"},
            ],
        }
        result = format_scout_detail(response)
        assert "Sources:" in result
        assert "Example Source: https://example.com" in result

    def test_scout_updates_include_sources(self):
        """format_scout_updates includes sources on individual updates."""
        response = {
            "updates": [
                {
                    "created_at": "2026-01-20T05:00:00Z",
                    "content": "Found results",
                    "citations": [{"url": "https://cited.com", "title": "Cited"}],
                }
            ],
            "has_more": False,
        }
        result = format_scout_updates(response)
        assert "Sources:" in result
        assert "Cited: https://cited.com" in result

    def test_task_result_includes_sources(self):
        """format_task_result includes sources with no indent."""
        response = {
            "task_id": "task-1",
            "status": "succeeded",
            "result": "Done",
            "sources": [{"url": "https://src.com", "title": "Src"}],
        }
        result = format_task_result(response)
        assert "Sources:" in result
        assert "- Src: https://src.com" in result


class TestFormatterNullSafety:
    """Regression tests for present-but-null fields in API responses."""

    def test_list_scouts_with_explicit_null_query(self):
        response = {
            "scouts": [{"id": "abc", "query": None, "status": "active"}],
            "total": 1,
            "summary": {"active": 1, "paused": 0, "done": 0},
        }
        result = format_list_scouts(response)
        assert "Untitled" in result

    def test_scout_created_with_explicit_null_query(self):
        response = {"id": "s1", "query": None, "status": "active"}
        result = format_scout_created(response)
        assert "Scout created successfully" in result


class TestTimestampFormatting:
    """_format_date/_format_datetime accept ISO strings and Unix s/ms ints."""

    def test_list_scouts_with_unix_ms_next_run(self):
        assert _format_date(1769997854699) == "2026-02-02"
        response = {
            "scouts": [
                {
                    "id": "abc",
                    "query": "q",
                    "status": "active",
                    "next_output_timestamp": 1769997854699,
                }
            ],
            "total": 1,
            "summary": {"active": 1, "paused": 0, "done": 0},
        }
        result = format_list_scouts(response)
        assert "Next: 2026-02-02" in result

    @pytest.mark.parametrize(
        "timestamp",
        [
            # Same instant expressed as Unix seconds and Unix milliseconds.
            pytest.param(1769997854, id="seconds"),
            pytest.param(1769997854699, id="milliseconds"),
        ],
    )
    def test_format_datetime_unix(self, timestamp):
        assert _format_datetime(timestamp) == "2026-02-02 02:04 UTC"


class TestFormatTaskResultUnrecognizedStatus:
    """Statuses outside the known set must not be reported as completed."""

    def test_cancelled_not_reported_completed(self):
        response = {"task_id": "t1", "status": "cancelled"}
        result = format_task_result(response)
        assert "Task completed" not in result
        assert "unrecognized status 'cancelled'" in result

    def test_unrecognized_status_still_surfaces_result(self):
        response = {"task_id": "t1", "status": "cancelled", "result": "partial data"}
        result = format_task_result(response)
        assert "Task completed" not in result
        assert "partial data" in result


class TestSourcesExternalContentMarkers:
    """Source titles/URLs are web-derived and must sit inside the markers."""

    def test_sources_wrapped_in_markers(self):
        lines = _format_sources(
            {"sources": [{"url": "https://x.com", "title": "Injected Title"}]}
        )
        start = lines.index(_EXTERNAL_CONTENT_START)
        end = lines.index(_EXTERNAL_CONTENT_END)
        assert start < lines.index("  - Injected Title: https://x.com") < end


class TestFormatScoutEditedOutputFields:
    """output_fields edits are echoed back by the API as output_schema."""

    @staticmethod
    def _schema(fields):
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {f: {"type": "string"} for f in fields},
            },
        }

    def test_output_schema_change_appears_in_diff(self):
        response = {
            "old": {
                "id": "s1",
                "query": "q",
                "output_schema": self._schema(["headline"]),
            },
            "new": {
                "id": "s1",
                "query": "q",
                "output_schema": self._schema(["headline", "url"]),
            },
        }
        result = format_scout_edited(response)
        assert "Output fields: headline → headline, url" in result
        assert "no changes detected" not in result


class TestFormatTaskResultUnrecognizedStatusDetails:
    """The unrecognized-status branch surfaces all diagnostic fields."""

    def test_rejection_reason_and_error_shown(self):
        response = {
            "task_id": "t1",
            "status": "expired",
            "rejection_reason": "billing_limit_reached",
            "error": "task expired after 24h",
        }
        result = format_task_result(response)
        assert "unrecognized status 'expired'" in result
        assert "Rejection reason: billing_limit_reached" in result
        assert "Error: task expired after 24h" in result

    def test_message_key_used_as_error_fallback(self):
        response = {"task_id": "t1", "status": "expired", "message": "gone"}
        result = format_task_result(response)
        assert "Error: gone" in result


class TestFormatOutputFieldsDiffShapes:
    """The diff formatter tolerates schemas not produced by this MCP."""

    def test_unset_to_schema_transition(self):
        schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"headline": {"type": "string"}}},
        }
        response = {
            "old": {"id": "s1", "query": "q"},
            "new": {"id": "s1", "query": "q", "output_schema": schema},
        }
        result = format_scout_edited(response)
        assert "Output fields: (not set) → headline" in result

    def test_custom_schema_shapes_do_not_crash(self):
        # JSON Schema tuple-form items (a list, not a dict)
        assert (
            _format_output_fields_diff({"items": [{"type": "string"}]})
            == "(custom schema)"
        )
        # Object-form schema without items
        assert (
            _format_output_fields_diff({"type": "object", "properties": {}})
            == "(custom schema)"
        )
        assert _format_output_fields_diff(None) == "(not set)"
