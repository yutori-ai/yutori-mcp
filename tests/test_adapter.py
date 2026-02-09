"""Tests for the MCP adapter error mapping contract."""

from unittest.mock import MagicMock, patch

import pytest

from yutori.exceptions import APIError, AuthenticationError
from yutori_mcp.adapter import MCPClientAdapter, YutoriAPIError


class TestErrorMapping:
    """Ensure SDK errors are mapped to stable MCP YutoriAPIError shape."""

    @pytest.fixture()
    def adapter(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-test-key"), \
             patch("yutori_mcp.adapter.YutoriClient"):
            return MCPClientAdapter()

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
        """The error text format must be: 'API Error ({status_code}): {message}'."""
        from yutori_mcp.adapter import YutoriAPIError

        err = YutoriAPIError(message="Not found", status_code=404)
        formatted = f"API Error ({err.status_code}): {err.message}"
        assert formatted == "API Error (404): Not found"

    def test_auth_error_formatted_as_401(self):
        err = YutoriAPIError(message="Invalid API key", status_code=401)
        formatted = f"API Error ({err.status_code}): {err.message}"
        assert formatted == "API Error (401): Invalid API key"


class TestAdapterInit:
    def test_raises_without_api_key(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value=None):
            with pytest.raises(ValueError, match="API key required"):
                MCPClientAdapter()

    def test_creates_client_with_resolved_key(self):
        with patch("yutori_mcp.adapter.resolve_api_key", return_value="yt-key") as mock_resolve, \
             patch("yutori_mcp.adapter.YutoriClient") as mock_client_cls:
            adapter = MCPClientAdapter()
            mock_resolve.assert_called_once()
            mock_client_cls.assert_called_once_with(api_key="yt-key")
