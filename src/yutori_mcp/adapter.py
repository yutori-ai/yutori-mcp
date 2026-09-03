"""Thin adapter mapping MCP tool calls to the Yutori SDK async client.

Wraps AsyncYutoriClient namespace methods, preserving the interface that
server.py's _invoke()/_TOOL_HANDLERS dispatch expects. Catches SDK APIError and re-raises
as YutoriAPIError for consistent MCP error formatting. The async client is
used so slow Yutori API calls never block the MCP server's event loop.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from yutori import AsyncYutoriClient
from .credentials import resolve_api_key_for_environment
from yutori.config import DEFAULT_BASE_URL
from yutori.exceptions import APIConnectionError, APIError, AuthenticationError

ERROR_NO_API_KEY = "API key required. Run 'uvx yutori-mcp login' or set YUTORI_API_KEY."

# Environment selection: name -> API base URL. `prod` must stay the default
# so existing installs keep hitting the production API unchanged; `dev` exists
# to point the server at the development stack for testing. server.py's
# `--env` flag derives its choices from this dict, so adding an environment
# here is the only change needed to expose it on the CLI.
ENVIRONMENT_BASE_URLS: dict[str, str] = {
    "prod": DEFAULT_BASE_URL,
    "dev": "https://api.dev.yutori.com/v1",
}
DEFAULT_ENVIRONMENT = "prod"
ENV_VAR_ENVIRONMENT = "YUTORI_ENV"

# The developer platform that shows each Navigator run, keyed like ENVIRONMENT_BASE_URLS
# so a run made against the dev API links to the dev platform and never the other way round.
ENVIRONMENT_PLATFORM_URLS: dict[str, str] = {
    "prod": "https://platform.yutori.com",
    "dev": "https://platform.dev.yutori.com",
}


def current_environment() -> str:
    """The environment name this process is targeting."""
    return os.environ.get(ENV_VAR_ENVIRONMENT) or DEFAULT_ENVIRONMENT


def _resolve_environment_url(mapping: dict[str, str], environment: str | None) -> str:
    """Look up ``environment`` (or the ambient one) in ``mapping``.

    Shared resolution order for every "environment name -> URL" mapping in this
    module: explicit ``environment`` argument, then the ``YUTORI_ENV`` environment
    variable, then prod. Raises ``ValueError`` for names not in ``mapping`` so a
    typo fails loudly instead of silently targeting production.
    """
    name = environment or current_environment()
    try:
        return mapping[name]
    except KeyError:
        valid = ", ".join(sorted(mapping))
        raise ValueError(
            f"Unknown Yutori environment {name!r}; expected one of: {valid}"
        ) from None


def resolve_base_url(environment: str | None = None) -> str:
    """Return the API base URL for an environment name.

    Resolution order: explicit ``environment`` argument, then the
    ``YUTORI_ENV`` environment variable, then prod.
    """
    return _resolve_environment_url(ENVIRONMENT_BASE_URLS, environment)


def resolve_platform_url(environment: str | None = None) -> str:
    """Return the developer-platform URL for an environment name.

    Same resolution order as ``resolve_base_url`` so the run link a computer-use
    result carries always points at the platform that recorded the run.
    """
    return _resolve_environment_url(ENVIRONMENT_PLATFORM_URLS, environment)


def is_scoped_environment(environment: str | None) -> bool:
    """Whether ``environment`` explicitly names a non-default environment to target.

    None (unset) and the literal ``DEFAULT_ENVIRONMENT`` both mean "use the SDK's single
    top-level credential/config", the behavior every install had before per-environment
    support existed. Shared by server.py's auth subcommands so a future change to what counts
    as "the default" only needs to happen here.
    """
    return bool(environment) and environment != DEFAULT_ENVIRONMENT


def resolve_run_credentials(environment: str | None = None) -> tuple[str | None, str]:
    """The (api_key, base_url) pair a computer-use run should authenticate with.

    Resolves both from the same environment so a run never mixes a key from one
    environment with another's API — the same pairing MCPClientAdapter.__init__ does
    for the scout/browsing/research tools.
    """
    name = environment or current_environment()
    return resolve_api_key_for_environment(name), resolve_base_url(name)


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

    Process-lifetime singleton: server.py's get_adapter() constructs one of
    these and reuses it for every tool call, closing it explicitly via
    close() from a FastMCP lifespan hook on shutdown. There is no
    async-context-manager protocol here (no __aenter__/__aexit__) — nothing
    scopes a single call to one adapter instance anymore.
    """

    def __init__(self, base_url: str | None = None) -> None:
        # Resolved per environment, exactly as the computer-use path does. With credentials
        # now split by environment, reading the SDK's single key here meant every browsing,
        # research and scout call sent a production key at the dev API.
        api_key = resolve_api_key_for_environment(current_environment())
        if not api_key:
            raise ValueError(ERROR_NO_API_KEY)
        self._client = AsyncYutoriClient(
            api_key=api_key, base_url=base_url or resolve_base_url()
        )

    async def close(self) -> None:
        await self._client.close()

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
    async def _call(
        fn: Callable[..., Awaitable[dict[str, Any]]], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
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
