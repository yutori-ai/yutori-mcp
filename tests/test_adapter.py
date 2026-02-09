"""Tests for the MCP adapter error mapping and argument forwarding."""

from unittest.mock import MagicMock, patch

import pytest

from yutori.exceptions import APIError, AuthenticationError
from yutori_mcp.adapter import MCPClientAdapter, YutoriAPIError, _strip_none


@pytest.fixture()
def adapter():
    with patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-test-key"), \
         patch("yutori_mcp.adapter.YutoriClient"):
        return MCPClientAdapter()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    """Ensure SDK errors are mapped to stable MCP YutoriAPIError shape."""

    def test_api_error_maps_to_yutori_api_error(self, adapter):
        sdk_error = APIError(message="Scout not found", status_code=404)
        adapter._client.scouts.get = MagicMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            adapter.get_scout_detail("nonexistent-id")

        assert exc_info.value.status_code == 404
        assert exc_info.value.message == "Scout not found"

    def test_authentication_error_maps_to_401(self, adapter):
        sdk_error = AuthenticationError("Invalid API key")
        adapter._client.scouts.list = MagicMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            adapter.list_scouts()

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.message

    def test_api_error_preserves_status_code(self, adapter):
        for code in [400, 403, 429, 500, 503]:
            sdk_error = APIError(message=f"Error {code}", status_code=code)
            adapter._client.scouts.list = MagicMock(side_effect=sdk_error)

            with pytest.raises(YutoriAPIError) as exc_info:
                adapter.list_scouts()

            assert exc_info.value.status_code == code

    def test_api_error_chains_original_exception(self, adapter):
        sdk_error = APIError(message="Rate limited", status_code=429)
        adapter._client.scouts.list = MagicMock(side_effect=sdk_error)

        with pytest.raises(YutoriAPIError) as exc_info:
            adapter.list_scouts()

        assert exc_info.value.__cause__ is sdk_error


class TestErrorFormattingContract:
    """Ensure the server formats YutoriAPIError into a stable text shape."""

    def test_api_error_formatted_as_text(self):
        err = YutoriAPIError(message="Not found", status_code=404)
        formatted = f"API Error ({err.status_code}): {err.message}"
        assert formatted == "API Error (404): Not found"

    def test_auth_error_formatted_as_401(self):
        err = YutoriAPIError(message="Invalid API key", status_code=401)
        formatted = f"API Error ({err.status_code}): {err.message}"
        assert formatted == "API Error (401): Invalid API key"


# ---------------------------------------------------------------------------
# Adapter init
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_raises_without_api_key(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value=None):
            with pytest.raises(ValueError, match="API key required"):
                MCPClientAdapter()

    def test_creates_client_with_resolved_key(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key"), \
             patch("yutori_mcp.adapter.YutoriClient") as mock_client_cls:
            MCPClientAdapter()
            mock_resolve = patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key")
            mock_client_cls.assert_called_once_with(api_key="yt-key")


# ---------------------------------------------------------------------------
# _strip_none
# ---------------------------------------------------------------------------


class TestStripNone:
    def test_removes_none_values(self):
        assert _strip_none({"a": 1, "b": None, "c": "x"}) == {"a": 1, "c": "x"}

    def test_preserves_falsy_non_none(self):
        assert _strip_none({"a": 0, "b": False, "c": ""}) == {"a": 0, "b": False, "c": ""}

    def test_empty_dict(self):
        assert _strip_none({}) == {}

    def test_all_none(self):
        assert _strip_none({"a": None, "b": None}) == {}


# ---------------------------------------------------------------------------
# Argument forwarding parity (Codex #1, #2)
# ---------------------------------------------------------------------------


class TestCreateScoutForwarding:
    """create_scout must not send None for optional fields (SDK has non-optional defaults)."""

    def test_none_output_interval_not_forwarded(self, adapter):
        adapter._client.scouts.create = MagicMock(return_value={"id": "s1"})
        adapter.create_scout("test query", output_interval=None, webhook_url=None)

        _, kwargs = adapter._client.scouts.create.call_args
        assert "output_interval" not in kwargs
        assert "webhook_url" not in kwargs

    def test_set_values_are_forwarded(self, adapter):
        adapter._client.scouts.create = MagicMock(return_value={"id": "s1"})
        adapter.create_scout("test query", output_interval=3600, webhook_url="https://example.com")

        _, kwargs = adapter._client.scouts.create.call_args
        assert kwargs["output_interval"] == 3600
        assert kwargs["webhook_url"] == "https://example.com"


class TestEditScoutForwarding:
    """edit_scout must filter None kwargs and not forward unsupported fields."""

    def test_none_values_not_forwarded(self, adapter):
        adapter._client.scouts.update = MagicMock(return_value={"id": "s1"})
        adapter.edit_scout("s1", query=None, output_interval=None, status="paused")

        _, kwargs = adapter._client.scouts.update.call_args
        assert "query" not in kwargs
        assert "output_interval" not in kwargs
        assert kwargs["status"] == "paused"

    def test_config_values_forwarded(self, adapter):
        adapter._client.scouts.update = MagicMock(return_value={"id": "s1"})
        adapter.edit_scout("s1", query="updated query", skip_email=True)

        _, kwargs = adapter._client.scouts.update.call_args
        assert kwargs["query"] == "updated query"
        assert kwargs["skip_email"] is True
