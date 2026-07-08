"""Tests for server helper functions."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from yutori.auth.types import AuthStatus, LoginResult
from yutori_mcp import __version__
from yutori_mcp.adapter import YutoriAPIError
from yutori_mcp.formatters import format_scout_edited
from yutori_mcp.schema_utils import output_fields_to_output_schema
from yutori_mcp.server import (
    _get_task_result_description,
    _handle_edit_scout,
    _list_tasks_description,
    main,
    mcp,
)


class TestOutputFieldsToOutputSchema:
    def test_none_returns_none(self):
        """None input returns None."""
        assert output_fields_to_output_schema(None) is None

    def test_empty_list_rejected(self):
        """Empty list would produce a degenerate schema, so reject it."""
        with pytest.raises(ValueError, match="at least one field"):
            output_fields_to_output_schema([])

    def test_single_field(self):
        """Single field is converted correctly."""
        result = output_fields_to_output_schema(["headline"])
        assert result == {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                },
            },
        }

    def test_multiple_fields(self):
        """Multiple fields are all converted to string properties."""
        result = output_fields_to_output_schema(["headline", "summary", "url"])
        expected_properties = {
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "url": {"type": "string"},
        }
        assert result["items"]["properties"] == expected_properties

    def test_output_is_array_type(self):
        """Output schema is always array type."""
        result = output_fields_to_output_schema(["field1"])
        assert result["type"] == "array"

    def test_items_are_objects(self):
        """Array items are always objects."""
        result = output_fields_to_output_schema(["field1"])
        assert result["items"]["type"] == "object"


class TestTaskDescriptionHelpers:
    """`_list_tasks_description`/`_get_task_result_description` are extracted
    so the browsing and research tool descriptions cannot drift out of sync
    with each other (mirrors `_output_fields_description` in schemas.py)."""

    def test_list_tasks_description_substitutes_label_and_get_tool(self):
        result = _list_tasks_description("browsing", "get_browsing_task_result")
        assert result == (
            "List one-time browsing tasks for the authenticated user. "
            "Supports cursor pagination and status filtering. List status is approximate "
            "(running also covers queued and not-yet-reconciled tasks); call "
            "get_browsing_task_result for a task's authoritative status."
        )

    def test_get_task_result_description_substitutes_label(self):
        result = _get_task_result_description("research")
        assert (
            result
            == "Poll for research task status and result. Call until status is 'succeeded' or 'failed'."
        )

    async def test_registered_tool_descriptions_match_helpers(self):
        """Drift guard: the live tool registry must use these helpers, not
        hand-written duplicates that could diverge from the browsing variant."""
        tools = {t.name: t.description for t in await mcp.list_tools()}
        assert tools["list_browsing_tasks"] == _list_tasks_description(
            "browsing", "get_browsing_task_result"
        )
        assert tools["list_research_tasks"] == _list_tasks_description(
            "research", "get_research_task_result"
        )
        assert tools["get_browsing_task_result"] == _get_task_result_description(
            "browsing"
        )
        assert tools["get_research_task_result"] == _get_task_result_description(
            "research"
        )


class TestMainStatusExitCode:
    """Ensure `yutori-mcp status` exits 1 when unauthenticated, 0 when authenticated."""

    def test_status_unauthenticated_exits_1(self):
        status = AuthStatus(authenticated=False, config_path="/tmp/.yutori/config.json")
        with (
            patch("sys.argv", ["yutori-mcp", "status"]),
            patch("yutori.auth.get_auth_status", return_value=status),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_status_authenticated_exits_0(self):
        status = AuthStatus(
            authenticated=True,
            masked_key="yt-abc...xyz",
            source="config_file",
            config_path="/tmp",
        )
        with (
            patch("sys.argv", ["yutori-mcp", "status"]),
            patch("yutori.auth.get_auth_status", return_value=status),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


class TestMainLoginAuthUrl:
    """Ensure `yutori-mcp login` surfaces auth_url on failure."""

    def test_login_failure_prints_auth_url(self, capsys):
        result = LoginResult(
            success=False,
            error="timed out",
            auth_url="https://clerk.example.com/oauth/authorize?x=1",
        )
        with (
            patch("sys.argv", ["yutori-mcp", "login"]),
            patch(
                "yutori.auth.run_login_flow", return_value=result
            ) as mock_run_login_flow,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        mock_run_login_flow.assert_called_once_with(key_source="yutori-mcp")
        output = capsys.readouterr().out
        assert "https://clerk.example.com/oauth/authorize?x=1" in output

    def test_login_failure_without_auth_url(self, capsys):
        result = LoginResult(success=False, error="port in use")
        with (
            patch("sys.argv", ["yutori-mcp", "login"]),
            patch(
                "yutori.auth.run_login_flow", return_value=result
            ) as mock_run_login_flow,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        mock_run_login_flow.assert_called_once_with(key_source="yutori-mcp")
        output = capsys.readouterr().out
        assert "browser" not in output.lower()


class TestMainVersionFlag:
    """Ensure `yutori-mcp --version` prints version and exits 0."""

    def test_version_flag_prints_version(self, capsys):
        with patch("sys.argv", ["yutori-mcp", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        output = capsys.readouterr().out.strip()
        assert output == f"yutori-mcp {__version__}"


class TestEditScoutPartialFailure:
    """The API needs separate config/status calls, so a mid-sequence failure
    must report that the config portion was already applied."""

    async def test_status_failure_after_config_update_reports_partial_application(self):
        client = AsyncMock()
        client.get_scout_detail.return_value = {
            "id": "s1",
            "status": "active",
            "query": "q",
        }
        client.edit_scout.side_effect = [
            {"id": "s1"},  # config update succeeds
            YutoriAPIError(message="server error", status_code=500),  # status fails
        ]

        with pytest.raises(YutoriAPIError) as exc_info:
            await _handle_edit_scout(
                client, {"scout_id": "s1", "query": "new q", "status": "paused"}
            )

        assert "config changes were applied" in exc_info.value.message.lower()
        assert "server error" in exc_info.value.message
        assert exc_info.value.status_code == 500

    async def test_status_only_failure_propagates_unwrapped(self):
        client = AsyncMock()
        client.get_scout_detail.return_value = {"id": "s1", "status": "active"}
        client.edit_scout.side_effect = YutoriAPIError(
            message="server error", status_code=500
        )

        with pytest.raises(YutoriAPIError) as exc_info:
            await _handle_edit_scout(client, {"scout_id": "s1", "status": "paused"})

        assert exc_info.value.message == "server error"


def _call_tool_handler():
    """Return the registered tools/call request handler from the FastMCP server."""
    return mcp._mcp_server.request_handlers[CallToolRequest]


def _call_tool_request(name, arguments):
    return CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=arguments),
    )


@contextmanager
def _patched_adapter():
    """Patch MCPClientAdapter and yield the mock used as the async-with client."""
    with patch("yutori_mcp.server.MCPClientAdapter") as adapter_cls:
        instance = AsyncMock()
        adapter_cls.return_value = instance
        instance.__aenter__.return_value = instance
        yield instance


class TestCallToolErrorContract:
    """The MCP framework converts exceptions raised from tool functions into
    CallToolResult(isError=True, content=[str(exception)]). These tests pin
    that contract end-to-end through the registered request handler."""

    async def test_api_error_returns_iserror_with_formatted_message(self):
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            client.list_scouts.side_effect = YutoriAPIError(
                message="boom", status_code=500
            )
            result = await handler(_call_tool_request("list_scouts", {}))

        assert result.root.isError is True
        assert "API Error (500): boom" in result.root.content[0].text

    async def test_success_returns_formatted_text_without_error_flag(self):
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            client.list_scouts.return_value = {
                "scouts": [],
                "total": 0,
                "summary": {"active": 0, "paused": 0, "done": 0},
            }
            result = await handler(_call_tool_request("list_scouts", {}))

        assert result.root.isError is False
        assert "Found 0 scouts" in result.root.content[0].text

    async def test_list_browsing_tasks_dispatches_with_filters(self):
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            client.list_browsing_tasks.return_value = {
                "tasks": [],
                "total": 0,
                "filtered_total": 0,
                "summary": {"running": 0, "succeeded": 0, "failed": 0},
            }
            result = await handler(
                _call_tool_request(
                    "list_browsing_tasks",
                    {"limit": 20, "status": "succeeded", "cursor": "cur-1"},
                )
            )

        client.list_browsing_tasks.assert_awaited_once_with(
            limit=20, status="succeeded", cursor="cur-1"
        )
        assert result.root.isError is False
        assert "Found 0 browsing tasks" in result.root.content[0].text

    async def test_list_research_tasks_dispatches_with_filters(self):
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            client.list_research_tasks.return_value = {
                "tasks": [],
                "total": 0,
                "filtered_total": 0,
                "summary": {"running": 0, "succeeded": 0, "failed": 0},
            }
            result = await handler(
                _call_tool_request("list_research_tasks", {"status": "failed"})
            )

        client.list_research_tasks.assert_awaited_once_with(limit=10, status="failed")
        assert result.root.isError is False
        assert "Found 0 research tasks" in result.root.content[0].text

    async def test_delete_scout_dispatches_with_scout_id(self):
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            client.delete_scout.return_value = {}
            result = await handler(
                _call_tool_request("delete_scout", {"scout_id": "scout-1"})
            )

        client.delete_scout.assert_awaited_once_with(scout_id="scout-1")
        assert result.root.isError is False
        assert "Scout deleted" in result.root.content[0].text
        assert "scout-1" in result.root.content[0].text

    async def test_unknown_argument_rejected(self):
        # FastMCP itself extracts only known parameters from the request,
        # silently dropping unknown ones. _StrictArgsFastMCP.call_tool
        # restores the old Server-based additionalProperties:false-style
        # rejection by checking the raw argument dict before FastMCP's own
        # binding drops anything unrecognized.
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            result = await handler(_call_tool_request("list_scouts", {"bogus": 1}))

        client.list_scouts.assert_not_awaited()
        assert result.root.isError is True
        assert "bogus" in result.root.content[0].text

    async def test_known_arguments_still_accepted(self):
        handler = _call_tool_handler()
        with _patched_adapter() as client:
            client.list_scouts.return_value = {
                "scouts": [],
                "total": 0,
                "summary": {"active": 0, "paused": 0, "done": 0},
            }
            result = await handler(_call_tool_request("list_scouts", {"limit": 5}))

        assert result.root.isError is False


class TestEditScoutReadBackFailure:
    """A failed post-edit state fetch must not report the edit as failed."""

    async def test_read_back_failure_returns_success_without_diff(self):
        client = AsyncMock()
        client.get_scout_detail.side_effect = [
            {"id": "s1", "status": "active", "query": "q"},  # pre-edit fetch
            YutoriAPIError(message="rate limited", status_code=429),  # read-back
        ]
        client.edit_scout.return_value = {"id": "s1"}

        result, context = await _handle_edit_scout(
            client, {"scout_id": "s1", "query": "new q"}
        )
        assert result["new"] == {}

        rendered = format_scout_edited(result, **context)
        assert "Scout updated successfully" in rendered
        assert "Could not fetch the updated scout state" in rendered
