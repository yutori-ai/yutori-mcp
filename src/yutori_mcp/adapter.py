"""Thin adapter mapping MCP tool calls to the Yutori SDK client.

Wraps YutoriClient namespace methods, preserving the same interface that
server.py's _handle_tool() expects. Catches SDK APIError and re-raises
as YutoriAPIError for consistent MCP error formatting.
"""

from __future__ import annotations

from typing import Any

from yutori.auth.credentials import resolve_api_key
from yutori.client import YutoriClient
from yutori.exceptions import APIError, AuthenticationError

from .constants import ERROR_NO_API_KEY


class YutoriAPIError(Exception):
    """Raised when the Yutori API returns an error (MCP-facing wrapper)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class MCPClientAdapter:
    """Adapter that delegates MCP tool calls to SDK client namespaces."""

    def __init__(self) -> None:
        api_key = resolve_api_key()
        if not api_key:
            raise ValueError(ERROR_NO_API_KEY)
        self._client = YutoriClient(api_key=api_key)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MCPClientAdapter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Scout operations
    # -------------------------------------------------------------------------

    def list_scouts(self, limit: int | None = None, status: str | None = None) -> dict[str, Any]:
        return self._call(self._client.scouts.list, limit=limit, status=status)

    def get_scout_detail(self, scout_id: str) -> dict[str, Any]:
        return self._call(self._client.scouts.get, scout_id)

    def create_scout(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(self._client.scouts.create, query, **kwargs)

    def edit_scout(self, scout_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(self._client.scouts.update, scout_id, **kwargs)

    def pause_scout(self, scout_id: str) -> dict[str, Any]:
        return self._call(self._client.scouts.update, scout_id, status="paused")

    def resume_scout(self, scout_id: str) -> dict[str, Any]:
        return self._call(self._client.scouts.update, scout_id, status="active")

    def complete_scout(self, scout_id: str) -> dict[str, Any]:
        return self._call(self._client.scouts.update, scout_id, status="done")

    def delete_scout(self, scout_id: str) -> dict[str, Any]:
        return self._call(self._client.scouts.delete, scout_id)

    def get_scout_updates(
        self, scout_id: str, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        return self._call(self._client.scouts.get_updates, scout_id, limit=limit, cursor=cursor)

    # -------------------------------------------------------------------------
    # Browsing operations
    # -------------------------------------------------------------------------

    def run_browsing_task(self, task: str, start_url: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(self._client.browsing.create, task, start_url, **kwargs)

    def get_browsing_task(self, task_id: str) -> dict[str, Any]:
        return self._call(self._client.browsing.get, task_id)

    # -------------------------------------------------------------------------
    # Research operations
    # -------------------------------------------------------------------------

    def run_research_task(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(self._client.research.create, query, **kwargs)

    def get_research_task(self, task_id: str) -> dict[str, Any]:
        return self._call(self._client.research.get, task_id)

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    @staticmethod
    def _call(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Call an SDK method, converting SDK APIError to MCP YutoriAPIError."""
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            raise YutoriAPIError(message=e.message, status_code=e.status_code) from e
        except AuthenticationError as e:
            raise YutoriAPIError(message=str(e), status_code=401) from e
