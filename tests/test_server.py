"""Tests for server helper functions."""

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from yutori.auth.types import AuthStatus, LoginResult
from yutori_mcp import __version__
from yutori_mcp.adapter import YutoriAPIError
from yutori_mcp.formatters import (
    TASK_TYPE_BROWSING,
    TASK_TYPE_RESEARCH,
    format_scout_edited,
)
from yutori_mcp.schema_utils import (
    output_fields_to_output_schema,
    output_schema_field_names,
)
from yutori_mcp.schemas import DEFAULT_LIST_LIMIT
from yutori_mcp.server import (
    _AUTH_SUBCOMMANDS,
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

    def test_round_trips_through_output_schema_field_names(self):
        """output_schema_field_names must invert output_fields_to_output_schema.

        Drift guard: formatters._format_output_fields_diff renders diffs by
        calling output_schema_field_names() on the schema this function
        builds. If the two ever fall out of sync (e.g. this function adds a
        `required` list or changes nesting), this test catches it instead of
        the diff formatter silently degrading to "(custom schema)".
        """
        fields = ["headline", "summary", "url"]
        schema = output_fields_to_output_schema(fields)
        assert output_schema_field_names(schema) == fields


class TestTaskDescriptionHelpers:
    """`_list_tasks_description`/`_get_task_result_description` are extracted
    so the browsing and research tool descriptions cannot drift out of sync
    with each other (mirrors `_output_fields_description` in schemas.py)."""

    def test_list_tasks_description_derives_label_and_get_tool(self):
        result = _list_tasks_description(TASK_TYPE_BROWSING)
        assert result == (
            "List one-time browsing tasks for the authenticated user. "
            "Supports cursor pagination and status filtering. List status is approximate "
            "(running also covers queued and not-yet-reconciled tasks); call "
            "get_browsing_task_result for a task's authoritative status."
        )

    def test_list_tasks_description_derives_research_get_tool(self):
        """Each task type derives its own get-tool name, so the label and the
        tool name it points at can never come from different task types."""
        result = _list_tasks_description(TASK_TYPE_RESEARCH)
        assert result.startswith("List one-time research tasks")
        assert "get_research_task_result for a task's authoritative status." in result

    def test_get_task_result_description_substitutes_label(self):
        result = _get_task_result_description(TASK_TYPE_RESEARCH)
        assert (
            result
            == "Poll for research task status and result. Call until status is 'succeeded' or 'failed'."
        )

    async def test_registered_tool_descriptions_match_helpers(self):
        """Drift guard: the live tool registry must use these helpers, not
        hand-written duplicates that could diverge from the browsing variant."""
        tools = {t.name: t.description for t in await mcp.list_tools()}
        assert tools["list_browsing_tasks"] == _list_tasks_description(
            TASK_TYPE_BROWSING
        )
        assert tools["list_research_tasks"] == _list_tasks_description(
            TASK_TYPE_RESEARCH
        )
        assert tools["get_browsing_task_result"] == _get_task_result_description(
            TASK_TYPE_BROWSING
        )
        assert tools["get_research_task_result"] == _get_task_result_description(
            TASK_TYPE_RESEARCH
        )


class TestRegisteredToolLimitDefaults:
    """Drift guard: the `limit` default FastMCP advertises to callers for
    list_scouts/list_browsing_tasks/list_research_tasks comes from each
    ``@mcp.tool`` function's own signature default in server.py, not from
    schemas.DEFAULT_LIST_LIMIT (schemas.py's Pydantic field default only
    applies once a request omits the argument entirely). The two must be
    kept in sync manually, so pin them here."""

    @pytest.mark.parametrize(
        "name", ["list_scouts", "list_browsing_tasks", "list_research_tasks"]
    )
    async def test_advertised_limit_defaults_match_shared_constant(self, name):
        tools = {t.name: t.inputSchema for t in await mcp.list_tools()}
        assert tools[name]["properties"]["limit"]["default"] == DEFAULT_LIST_LIMIT


def _assert_main_exits(expected_code: int) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == expected_code


class TestMainStatusExitCode:
    """Ensure `yutori-mcp status` exits 1 when unauthenticated, 0 when authenticated."""

    @pytest.mark.parametrize(
        "status_kwargs,expected_exit_code",
        [
            pytest.param(
                {"authenticated": False, "config_path": "/tmp/.yutori/config.json"},
                1,
                id="unauthenticated",
            ),
            pytest.param(
                {
                    "authenticated": True,
                    "masked_key": "yt-abc...xyz",
                    "source": "config_file",
                    "config_path": "/tmp",
                },
                0,
                id="authenticated",
            ),
        ],
    )
    def test_status_exit_code(self, status_kwargs, expected_exit_code):
        status = AuthStatus(**status_kwargs)
        with (
            patch("sys.argv", ["yutori-mcp", "status"]),
            patch("yutori.auth.get_auth_status", return_value=status),
        ):
            _assert_main_exits(expected_exit_code)


class TestMainAuthDispatch:
    """Every registered auth subcommand runs the handler paired with it in
    `_AUTH_SUBCOMMANDS`, so the table is the only place a subcommand's CLI
    help text and its implementation are tied together."""

    @pytest.mark.parametrize("name", sorted(_AUTH_SUBCOMMANDS))
    def test_subcommand_runs_its_table_handler(self, name):
        calls = []

        def fake_handler(environment: str | None):
            calls.append((name, environment))
            raise SystemExit(0)

        help_text, _ = _AUTH_SUBCOMMANDS[name]
        table = {**_AUTH_SUBCOMMANDS, name: (help_text, fake_handler)}
        with (
            patch("sys.argv", ["yutori-mcp", name]),
            patch.dict("yutori_mcp.server._AUTH_SUBCOMMANDS", table, clear=True),
        ):
            _assert_main_exits(0)
        assert calls == [(name, None)]


def _run_failed_login(capsys, result: LoginResult) -> str:
    """Dispatch `yutori-mcp login` with `run_login_flow` stubbed to fail with `result`.

    Asserts the shared exit-code and call-arg contract every failed-login test relies
    on, then returns the captured stdout for the caller's own assertions.
    """
    with (
        patch("sys.argv", ["yutori-mcp", "login"]),
        patch("yutori.auth.run_login_flow", return_value=result) as mock_run_login_flow,
    ):
        _assert_main_exits(1)
    mock_run_login_flow.assert_called_once_with(key_source="yutori-mcp")
    return capsys.readouterr().out


class TestMainLoginAuthUrl:
    """Ensure `yutori-mcp login` surfaces auth_url on failure."""

    def test_login_failure_prints_auth_url(self, capsys):
        result = LoginResult(
            success=False,
            error="timed out",
            auth_url="https://clerk.example.com/oauth/authorize?x=1",
        )
        output = _run_failed_login(capsys, result)
        assert "https://clerk.example.com/oauth/authorize?x=1" in output

    def test_login_failure_without_auth_url(self, capsys):
        result = LoginResult(success=False, error="port in use")
        output = _run_failed_login(capsys, result)
        assert "browser" not in output.lower()


class TestMainVersionFlag:
    """Ensure `yutori-mcp --version` prints version and exits 0."""

    def test_version_flag_prints_version(self, capsys):
        with patch("sys.argv", ["yutori-mcp", "--version"]):
            _assert_main_exits(0)
        output = capsys.readouterr().out.strip()
        assert output == f"yutori-mcp {__version__}"


class TestMainEnvSelection:
    """`--env` / YUTORI_ENV select the API environment before the server runs."""

    @staticmethod
    @contextmanager
    def _run_main(argv, env=None):
        """Run main() with a patched mcp.run and a scrubbed YUTORI_ENV.

        Yields the mcp.run mock. patch.dict restores os.environ afterwards,
        so the env-var write main() performs never leaks into other tests.
        """
        import os

        environ = {k: v for k, v in os.environ.items() if k != "YUTORI_ENV"}
        environ.update(env or {})
        with (
            patch.dict("os.environ", environ, clear=True),
            patch("sys.argv", ["yutori-mcp", *argv]),
            patch.object(mcp, "run") as mock_run,
        ):
            yield mock_run

    def test_env_flag_sets_env_var_and_runs_server(self):
        import os

        with self._run_main(["--env", "dev"]) as mock_run:
            main()
            assert os.environ["YUTORI_ENV"] == "dev"
        mock_run.assert_called_once_with(transport="stdio")

    def test_dev_env_prints_target_notice_to_stderr(self, capsys):
        with self._run_main(["--env", "dev"]):
            main()
        assert "api.dev.yutori.com" in capsys.readouterr().err

    def test_prod_default_prints_no_notice(self, capsys):
        with self._run_main([]) as mock_run:
            main()
        assert capsys.readouterr().err == ""
        mock_run.assert_called_once_with(transport="stdio")

    def test_invalid_env_flag_rejected_by_argparse(self):
        with self._run_main(["--env", "staging"]):
            _assert_main_exits(2)

    def test_invalid_env_var_fails_at_startup(self, capsys):
        with self._run_main([], env={"YUTORI_ENV": "staging"}) as mock_run:
            _assert_main_exits(2)
        mock_run.assert_not_called()
        assert "Unknown Yutori environment 'staging'" in capsys.readouterr().err


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


async def _call_tool(name: str, arguments: dict) -> Any:
    """Invoke the registered tools/call handler for `name` with `arguments`."""
    handler = _call_tool_handler()
    return await handler(_call_tool_request(name, arguments))


@contextmanager
def _patched_adapter():
    """Patch server.get_adapter and yield the mock instance it returns.

    _invoke() reuses a process-lifetime adapter singleton via get_adapter()
    instead of constructing a fresh MCPClientAdapter per call, so tests patch
    the accessor function rather than the MCPClientAdapter class.
    """
    with patch("yutori_mcp.server.get_adapter") as get_adapter_mock:
        instance = AsyncMock()
        get_adapter_mock.return_value = instance
        yield instance


class TestCallToolErrorContract:
    """The MCP framework converts exceptions raised from tool functions into
    CallToolResult(isError=True, content=[str(exception)]). These tests pin
    that contract end-to-end through the registered request handler."""

    async def test_api_error_returns_iserror_with_formatted_message(self):
        with _patched_adapter() as client:
            client.list_scouts.side_effect = YutoriAPIError(
                message="boom", status_code=500
            )
            result = await _call_tool("list_scouts", {})

        assert result.root.isError is True
        assert "API Error (500): boom" in result.root.content[0].text

    async def test_success_returns_formatted_text_without_error_flag(self):
        with _patched_adapter() as client:
            client.list_scouts.return_value = {
                "scouts": [],
                "total": 0,
                "summary": {"active": 0, "paused": 0, "done": 0},
            }
            result = await _call_tool("list_scouts", {})

        assert result.root.isError is False
        assert "Found 0 scouts" in result.root.content[0].text

    @pytest.mark.parametrize(
        "tool_name,request_args,expected_kwargs,expected_label",
        [
            pytest.param(
                "list_browsing_tasks",
                {"limit": 20, "status": "succeeded", "cursor": "cur-1"},
                {"limit": 20, "status": "succeeded", "cursor": "cur-1"},
                "browsing",
                id="browsing",
            ),
            pytest.param(
                "list_research_tasks",
                {"status": "failed"},
                {"limit": 10, "status": "failed"},
                "research",
                id="research",
            ),
        ],
    )
    async def test_list_tasks_dispatches_with_filters(
        self, tool_name, request_args, expected_kwargs, expected_label
    ):
        with _patched_adapter() as client:
            mock_method = getattr(client, tool_name)
            mock_method.return_value = {
                "tasks": [],
                "total": 0,
                "filtered_total": 0,
                "summary": {"running": 0, "succeeded": 0, "failed": 0},
            }
            result = await _call_tool(tool_name, request_args)

        mock_method.assert_awaited_once_with(**expected_kwargs)
        assert result.root.isError is False
        assert f"Found 0 {expected_label} tasks" in result.root.content[0].text

    async def test_delete_scout_dispatches_with_scout_id(self):
        with _patched_adapter() as client:
            client.delete_scout.return_value = {}
            result = await _call_tool("delete_scout", {"scout_id": "scout-1"})

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
        with _patched_adapter() as client:
            result = await _call_tool("list_scouts", {"bogus": 1})

        client.list_scouts.assert_not_awaited()
        assert result.root.isError is True
        assert "bogus" in result.root.content[0].text

    async def test_known_arguments_still_accepted(self):
        with _patched_adapter() as client:
            client.list_scouts.return_value = {
                "scouts": [],
                "total": 0,
                "summary": {"active": 0, "paused": 0, "done": 0},
            }
            result = await _call_tool("list_scouts", {"limit": 5})

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
