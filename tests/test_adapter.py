"""Tests for the MCP adapter error mapping and argument forwarding."""

from unittest.mock import AsyncMock, patch

import pytest

from yutori.exceptions import APIConnectionError, APIError, AuthenticationError
from yutori_mcp.adapter import (
    DEFAULT_ENVIRONMENT,
    ENV_VAR_ENVIRONMENT,
    ENVIRONMENT_BASE_URLS,
    MCPClientAdapter,
    YutoriAPIError,
    _strip_none,
    resolve_base_url,
)
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

    @pytest.mark.parametrize(
        "message,status_code",
        [
            pytest.param("Not found", 404, id="api-error"),
            pytest.param("Invalid API key", 401, id="auth-error-as-401"),
        ],
    )
    def test_error_formatted_as_text(self, message, status_code):
        err = YutoriAPIError(message=message, status_code=status_code)
        assert _format_api_error(err) == f"API Error ({status_code}): {message}"


# ---------------------------------------------------------------------------
# Adapter init
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_raises_without_api_key(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value=None):
            with pytest.raises(ValueError, match="API key required"):
                MCPClientAdapter()

    def test_creates_client_with_resolved_key(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR_ENVIRONMENT, raising=False)
        with (
            patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key"),
            patch("yutori_mcp.adapter.AsyncYutoriClient") as mock_client_cls,
        ):
            MCPClientAdapter()
            mock_client_cls.assert_called_once_with(
                api_key="yt-key",
                base_url=ENVIRONMENT_BASE_URLS[DEFAULT_ENVIRONMENT],
            )

    def test_env_var_selects_dev_base_url(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR_ENVIRONMENT, "dev")
        with (
            patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key"),
            patch("yutori_mcp.adapter.AsyncYutoriClient") as mock_client_cls,
        ):
            MCPClientAdapter()
            mock_client_cls.assert_called_once_with(
                api_key="yt-key", base_url=ENVIRONMENT_BASE_URLS["dev"]
            )

    def test_explicit_base_url_wins_over_env_var(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR_ENVIRONMENT, "dev")
        with (
            patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key"),
            patch("yutori_mcp.adapter.AsyncYutoriClient") as mock_client_cls,
        ):
            MCPClientAdapter(base_url="http://localhost:8000/v1")
            mock_client_cls.assert_called_once_with(
                api_key="yt-key", base_url="http://localhost:8000/v1"
            )

    async def test_close_closes_client(self, adapter):
        adapter._client.close = AsyncMock()
        await adapter.close()
        adapter._client.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# resolve_base_url
# ---------------------------------------------------------------------------


class TestResolveBaseUrl:
    """Environment-name -> base-URL resolution used to switch prod/dev."""

    def test_defaults_to_prod(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR_ENVIRONMENT, raising=False)
        assert resolve_base_url() == ENVIRONMENT_BASE_URLS["prod"]

    @pytest.mark.parametrize("env", sorted(ENVIRONMENT_BASE_URLS))
    def test_explicit_argument_maps_to_url(self, env):
        assert resolve_base_url(env) == ENVIRONMENT_BASE_URLS[env]

    def test_env_var_used_when_no_argument(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR_ENVIRONMENT, "dev")
        assert resolve_base_url() == ENVIRONMENT_BASE_URLS["dev"]

    def test_argument_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR_ENVIRONMENT, "dev")
        assert resolve_base_url("prod") == ENVIRONMENT_BASE_URLS["prod"]

    def test_empty_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR_ENVIRONMENT, "")
        assert resolve_base_url() == ENVIRONMENT_BASE_URLS[DEFAULT_ENVIRONMENT]

    def test_unknown_environment_raises_instead_of_hitting_prod(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR_ENVIRONMENT, "staging")
        with pytest.raises(ValueError, match="Unknown Yutori environment 'staging'"):
            resolve_base_url()


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

    @pytest.mark.parametrize(
        "adapter_method,client_ns,status,cursor",
        [
            pytest.param("list_browsing_tasks", "browsing", "succeeded", "cur-1", id="browsing"),
            pytest.param("list_research_tasks", "research", "failed", "cur-2", id="research"),
        ],
    )
    async def test_list_tasks_forwards_pagination_filters(self, adapter, adapter_method, client_ns, status, cursor):
        namespace = getattr(adapter._client, client_ns)
        namespace.list = AsyncMock(return_value={"tasks": []})
        await getattr(adapter, adapter_method)(limit=20, status=status, cursor=cursor)

        _, kwargs = namespace.list.call_args
        assert kwargs == {"limit": 20, "status": status, "cursor": cursor}

    @pytest.mark.parametrize(
        "adapter_method,client_ns",
        [
            pytest.param("list_browsing_tasks", "browsing", id="browsing"),
            pytest.param("list_research_tasks", "research", id="research"),
        ],
    )
    async def test_list_tasks_strips_none_values(self, adapter, adapter_method, client_ns):
        namespace = getattr(adapter._client, client_ns)
        namespace.list = AsyncMock(return_value={"tasks": []})
        await getattr(adapter, adapter_method)(limit=None, status=None, cursor=None)

        _, kwargs = namespace.list.call_args
        assert kwargs == {}

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
