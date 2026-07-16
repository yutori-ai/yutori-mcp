"""Thin adapter mapping MCP tool calls to the Yutori SDK async client.

Wraps AsyncYutoriClient namespace methods, preserving the interface that
server.py's _invoke()/_TOOL_HANDLERS dispatch expects. Catches SDK APIError and re-raises
as YutoriAPIError for consistent MCP error formatting. The async client is
used so slow Yutori API calls never block the MCP server's event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from yutori import AsyncYutoriClient
from yutori.auth.credentials import resolve_api_key
from yutori.exceptions import APIConnectionError, APIError, AuthenticationError

logger = logging.getLogger(__name__)

ERROR_NO_API_KEY = "API key required. Run 'uvx yutori-mcp login' or set YUTORI_API_KEY."


class YutoriAPIError(Exception):
    """Raised when the Yutori API returns an error (MCP-facing wrapper)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class MCPClientAdapter:
    """Adapter that delegates MCP tool calls to SDK client namespaces.

    _call() strips None-valued kwargs before forwarding to the SDK, so
    callers can pass optional fields unconditionally and new methods
    can't accidentally skip the filter.
    """

    def __init__(self) -> None:
        api_key = resolve_api_key()
        if not api_key:
            raise ValueError(ERROR_NO_API_KEY)
        self._client = AsyncYutoriClient(api_key=api_key)

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> MCPClientAdapter:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            await self.close()
        except Exception:
            # A close failure must not replace an in-flight handler error —
            # that would mask the real failure in the tool result.
            if exc_type is None:
                raise
            logger.warning("Failed to close Yutori client", exc_info=True)

    # -------------------------------------------------------------------------
    # Usage
    # -------------------------------------------------------------------------

    async def get_usage(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.get_usage, **kwargs)

    # -------------------------------------------------------------------------
    # Scout operations
    # -------------------------------------------------------------------------

    async def list_scouts(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.scouts.list, **kwargs)

    async def get_scout_detail(self, scout_id: str) -> dict[str, Any]:
        return await self._call(self._client.scouts.get, scout_id)

    async def create_scout(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.scouts.create, query, **kwargs)

    async def edit_scout(self, scout_id: str, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.scouts.update, scout_id, **kwargs)

    async def delete_scout(self, scout_id: str) -> dict[str, Any]:
        return await self._call(self._client.scouts.delete, scout_id)

    async def get_scout_updates(self, scout_id: str, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.scouts.get_updates, scout_id, **kwargs)

    # -------------------------------------------------------------------------
    # Browsing operations
    # -------------------------------------------------------------------------

    async def list_browsing_tasks(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.browsing.list, **kwargs)

    async def run_browsing_task(
        self, task: str, start_url: str, **kwargs: Any
    ) -> dict[str, Any]:
        return await self._call(self._client.browsing.create, task, start_url, **kwargs)

    async def get_browsing_task(self, task_id: str) -> dict[str, Any]:
        return await self._call(self._client.browsing.get, task_id)

    # -------------------------------------------------------------------------
    # Research operations
    # -------------------------------------------------------------------------

    async def list_research_tasks(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.research.list, **kwargs)

    async def run_research_task(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._client.research.create, query, **kwargs)

    async def get_research_task(self, task_id: str) -> dict[str, Any]:
        return await self._call(self._client.research.get, task_id)

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    @staticmethod
    async def _call(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Await an SDK method, converting SDK APIError to MCP YutoriAPIError.

        Filters None-valued kwargs before forwarding so callers can pass
        optional fields unconditionally without overriding SDK defaults.
        """
        try:
            return await fn(*args, **_strip_none(kwargs))
        except AuthenticationError as e:
            raise YutoriAPIError(message=str(e), status_code=401) from e
        except APIError as e:
            raise YutoriAPIError(message=e.message, status_code=e.status_code) from e
        except APIConnectionError as e:
            # Transport failures (timeouts, DNS/connect errors) carry no HTTP
            # status; surface as 503 so the MCP error format stays uniform.
            raise YutoriAPIError(message=str(e), status_code=503) from e


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove None-valued entries so SDK defaults aren't overridden."""
    return {k: v for k, v in d.items() if v is not None}
