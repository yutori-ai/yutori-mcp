"""Tests for the MCP adapter error mapping and argument forwarding."""

from unittest.mock import AsyncMock, patch

import pytest

from yutori.exceptions import APIConnectionError, APIError, AuthenticationError
from yutori_mcp.adapter import MCPClientAdapter, YutoriAPIError, _strip_none
from yutori_mcp.server import _format_api_error


@pytest.fixture()
def adapter():
    with (
        patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-test-key"),
        patch("yutori_mcp.adapter.AsyncYutoriClient"),
    ):
        return MCPClientAdapter()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    """Ensure SDK errors are mapped to stable MCP YutoriAPIError shape."""

    async def test_api_error_maps_to_yutori_api_error(self, adapter):
        sdk_error = APIError(message="Scout not found", status_code=404)
        adapter._client.scouts.get = AsyncMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            await adapter.get_scout_detail("nonexistent-id")

        assert exc_info.value.status_code == 404
        assert exc_info.value.message == "Scout not found"

    async def test_authentication_error_maps_to_401(self, adapter):
        sdk_error = AuthenticationError("Invalid API key")
        adapter._client.scouts.list = AsyncMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            await adapter.list_scouts()

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.message

    async def test_authentication_handler_preferred_if_auth_error_becomes_api_subclass(
        self,
    ):
        class AuthAsApiError(APIError):
            pass

        async def raise_auth_as_api_error():
            raise AuthAsApiError("Invalid API key", status_code=403)

        with patch("yutori_mcp.adapter.AuthenticationError", AuthAsApiError):
            with pytest.raises(YutoriAPIError) as exc_info:
                await MCPClientAdapter._call(raise_auth_as_api_error)

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.message

    @pytest.mark.parametrize("code", [400, 403, 429, 500, 503])
    async def test_api_error_preserves_status_code(self, adapter, code):
        sdk_error = APIError(message=f"Error {code}", status_code=code)
        adapter._client.scouts.list = AsyncMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            await adapter.list_scouts()

        assert exc_info.value.status_code == code

    async def test_api_error_chains_original_exception(self, adapter):
        sdk_error = APIError(message="Rate limited", status_code=429)
        adapter._client.scouts.list = AsyncMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            await adapter.list_scouts()

        assert exc_info.value.__cause__ is sdk_error


class TestErrorFormattingContract:
    """Ensure server._format_api_error renders a stable text shape.

    Calls the real helper _invoke() uses (rather than re-deriving the format
    string inline), so a change to that formatting is actually caught here
    instead of only in the end-to-end assertion in
    test_server.py::TestCallToolErrorContract.
    """

    def test_api_error_formatted_as_text(self):
        err = YutoriAPIError(message="Not found", status_code=404)
        assert _format_api_error(err) == "API Error (404): Not found"

    def test_auth_error_formatted_as_401(self):
        err = YutoriAPIError(message="Invalid API key", status_code=401)
        assert _format_api_error(err) == "API Error (401): Invalid API key"


# ---------------------------------------------------------------------------
# Adapter init
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_raises_without_api_key(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value=None):
            with pytest.raises(ValueError, match="API key required"):
                MCPClientAdapter()

    def test_creates_client_with_resolved_key(self):
        with (
            patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key"),
            patch("yutori_mcp.adapter.AsyncYutoriClient") as mock_client_cls,
        ):
            MCPClientAdapter()
            mock_client_cls.assert_called_once_with(api_key="yt-key")

    async def test_context_manager_closes_client(self, adapter):
        adapter._client.close = AsyncMock()
        async with adapter:
            pass
        adapter._client.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _strip_none
# ---------------------------------------------------------------------------


class TestStripNone:
    def test_removes_none_values(self):
        assert _strip_none({"a": 1, "b": None, "c": "x"}) == {"a": 1, "c": "x"}

    def test_preserves_falsy_non_none(self):
        assert _strip_none({"a": 0, "b": False, "c": ""}) == {
            "a": 0,
            "b": False,
            "c": "",
        }

    def test_empty_dict(self):
        assert _strip_none({}) == {}

    def test_all_none(self):
        assert _strip_none({"a": None, "b": None}) == {}


# ---------------------------------------------------------------------------
# Argument forwarding parity (Codex #1, #2)
# ---------------------------------------------------------------------------


class TestGetUsageForwarding:
    """get_usage must forward period and strip None values."""

    async def test_period_forwarded(self, adapter):
        adapter._client.get_usage = AsyncMock(return_value={"num_active_scouts": 1})
        await adapter.get_usage(period="7d")

        _, kwargs = adapter._client.get_usage.call_args
        assert kwargs["period"] == "7d"

    async def test_none_period_not_forwarded(self, adapter):
        adapter._client.get_usage = AsyncMock(return_value={"num_active_scouts": 0})
        await adapter.get_usage(period=None)

        _, kwargs = adapter._client.get_usage.call_args
        assert "period" not in kwargs


class TestCreateScoutForwarding:
    """create_scout must not send None for optional fields (SDK has non-optional defaults)."""

    async def test_none_output_interval_not_forwarded(self, adapter):
        adapter._client.scouts.create = AsyncMock(return_value={"id": "s1"})
        await adapter.create_scout("test query", output_interval=None, webhook_url=None)

        _, kwargs = adapter._client.scouts.create.call_args
        assert "output_interval" not in kwargs
        assert "webhook_url" not in kwargs

    async def test_set_values_are_forwarded(self, adapter):
        adapter._client.scouts.create = AsyncMock(return_value={"id": "s1"})
        await adapter.create_scout(
            "test query", output_interval=3600, webhook_url="https://example.com"
        )

        _, kwargs = adapter._client.scouts.create.call_args
        assert kwargs["output_interval"] == 3600
        assert kwargs["webhook_url"] == "https://example.com"


class TestEditScoutForwarding:
    """edit_scout must filter None kwargs and not forward unsupported fields."""

    async def test_none_values_not_forwarded(self, adapter):
        adapter._client.scouts.update = AsyncMock(return_value={"id": "s1"})
        await adapter.edit_scout(
            "s1", query=None, output_interval=None, status="paused"
        )

        _, kwargs = adapter._client.scouts.update.call_args
        assert "query" not in kwargs
        assert "output_interval" not in kwargs
        assert kwargs["status"] == "paused"

    async def test_config_values_forwarded(self, adapter):
        adapter._client.scouts.update = AsyncMock(return_value={"id": "s1"})
        await adapter.edit_scout("s1", query="updated query", skip_email=True)

        _, kwargs = adapter._client.scouts.update.call_args
        assert kwargs["query"] == "updated query"
        assert kwargs["skip_email"] is True

    async def test_is_public_forwarded(self, adapter):
        adapter._client.scouts.update = AsyncMock(return_value={"id": "s1"})
        await adapter.edit_scout("s1", is_public=False)

        _, kwargs = adapter._client.scouts.update.call_args
        assert kwargs["is_public"] is False


class TestBrowsingAndResearchForwarding:
    """Browsing and research should forward newly supported developer API fields."""

    async def test_list_browsing_tasks_forwards_pagination_filters(self, adapter):
        adapter._client.browsing.list = AsyncMock(return_value={"tasks": []})
        await adapter.list_browsing_tasks(limit=20, status="succeeded", cursor="cur-1")

        _, kwargs = adapter._client.browsing.list.call_args
        assert kwargs == {"limit": 20, "status": "succeeded", "cursor": "cur-1"}

    async def test_list_browsing_tasks_strips_none_values(self, adapter):
        adapter._client.browsing.list = AsyncMock(return_value={"tasks": []})
        await adapter.list_browsing_tasks(limit=None, status=None, cursor=None)

        _, kwargs = adapter._client.browsing.list.call_args
        assert kwargs == {}

    async def test_list_research_tasks_forwards_pagination_filters(self, adapter):
        adapter._client.research.list = AsyncMock(return_value={"tasks": []})
        await adapter.list_research_tasks(limit=20, status="failed", cursor="cur-2")

        _, kwargs = adapter._client.research.list.call_args
        assert kwargs == {"limit": 20, "status": "failed", "cursor": "cur-2"}

    async def test_browsing_forwards_require_auth_browser_and_zapier(self, adapter):
        adapter._client.browsing.create = AsyncMock(return_value={"task_id": "t1"})
        await adapter.run_browsing_task(
            "Log in and export data",
            "https://example.com/login",
            require_auth=True,
            browser="local",
            webhook_format="zapier",
        )

        _, kwargs = adapter._client.browsing.create.call_args
        assert kwargs["require_auth"] is True
        assert kwargs["browser"] == "local"
        assert kwargs["webhook_format"] == "zapier"


class TestContextManagerErrorPaths:
    """__aexit__ must close the client on error paths without masking the
    in-flight exception."""

    async def test_client_closed_when_body_raises(self, adapter):
        adapter._client.close = AsyncMock()
        with pytest.raises(YutoriAPIError):
            async with adapter:
                raise YutoriAPIError(message="boom", status_code=500)
        adapter._client.close.assert_awaited_once()

    async def test_close_failure_does_not_mask_handler_error(self, adapter):
        adapter._client.close = AsyncMock(side_effect=RuntimeError("close failed"))
        with pytest.raises(YutoriAPIError, match="boom"):
            async with adapter:
                raise YutoriAPIError(message="boom", status_code=500)

    async def test_close_failure_on_clean_exit_propagates(self, adapter):
        adapter._client.close = AsyncMock(side_effect=RuntimeError("close failed"))
        with pytest.raises(RuntimeError, match="close failed"):
            async with adapter:
                pass


class TestTransportErrorMapping:
    """SDK APIConnectionError (transport failures) maps to YutoriAPIError(503).

    The SDK guarantees the message is never blank (it always includes the
    underlying httpx exception type); the adapter only adds the status code.
    """

    async def test_connection_error_mapped(self, adapter):
        adapter._client.scouts.list = AsyncMock(
            side_effect=APIConnectionError(
                "Network error calling the Yutori API (ConnectError): connection refused"
            )
        )
        with pytest.raises(YutoriAPIError) as exc_info:
            await adapter.list_scouts()
        assert exc_info.value.status_code == 503
        assert "ConnectError" in exc_info.value.message
