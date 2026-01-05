"""HTTP client for the Yutori API."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.yutori.com/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0


class YutoriAPIError(Exception):
    """Raised when the Yutori API returns an error."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return f"{self.status_code}: {self.message}"


class YutoriClient:
    """HTTP client for interacting with the Yutori API."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key or os.environ.get("YUTORI_API_KEY", "")
        if not self.api_key:
            raise ValueError("API key is required. Set YUTORI_API_KEY environment variable or pass api_key parameter.")

        self.base_url = DEFAULT_BASE_URL
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Release HTTP client resources."""
        self._client.close()

    def __enter__(self) -> "YutoriClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Scout operations
    # -------------------------------------------------------------------------

    def list_scouts(self) -> dict[str, Any]:
        """List all scouts for the authenticated user (basic metadata only)."""
        return self._request("GET", "/scouting/tasks")

    def create_scout(
        self,
        query: str,
        output_interval: int | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
        task_spec: dict[str, Any] | None = None,
        user_timezone: str | None = None,
        skip_email: bool | None = None,
    ) -> dict[str, Any]:
        """Create a new monitoring scout."""
        payload: dict[str, Any] = {"query": query}
        if output_interval is not None:
            payload["output_interval"] = output_interval
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_format is not None:
            payload["webhook_format"] = webhook_format
        if task_spec is not None:
            payload["task_spec"] = task_spec
        if user_timezone is not None:
            payload["user_timezone"] = user_timezone
        if skip_email is not None:
            payload["skip_email"] = skip_email
        return self._request("POST", "/scouting/tasks", json=payload)

    def edit_scout(
        self,
        scout_id: str,
        query: str | None = None,
        output_interval: int | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
        task_spec: dict[str, Any] | None = None,
        skip_email: bool | None = None,
    ) -> dict[str, Any]:
        """Update an existing scout."""
        payload: dict[str, Any] = {}
        if query is not None:
            payload["query"] = query
        if output_interval is not None:
            payload["output_interval"] = output_interval
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_format is not None:
            payload["webhook_format"] = webhook_format
        if task_spec is not None:
            payload["task_spec"] = task_spec
        if skip_email is not None:
            payload["skip_email"] = skip_email
        return self._request("PATCH", f"/scouting/tasks/{scout_id}", json=payload)

    def pause_scout(self, scout_id: str) -> dict[str, Any]:
        """Pause a running scout."""
        return self._request("POST", f"/scouting/tasks/{scout_id}/pause")

    def resume_scout(self, scout_id: str) -> dict[str, Any]:
        """Resume a paused scout."""
        return self._request("POST", f"/scouting/tasks/{scout_id}/resume")

    def complete_scout(self, scout_id: str) -> dict[str, Any]:
        """Mark a scout as complete (archive it)."""
        return self._request("POST", f"/scouting/tasks/{scout_id}/done")

    def delete_scout(self, scout_id: str) -> dict[str, Any]:
        """Permanently delete a scout."""
        return self._request("DELETE", f"/scouting/tasks/{scout_id}")

    def get_scout_detail(self, scout_id: str) -> dict[str, Any]:
        """Get detailed information for a specific scout."""
        return self._request("GET", f"/scouting/tasks/{scout_id}")

    def get_scout_updates(
        self,
        scout_id: str,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Get updates/reports for a specific scout."""
        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        return self._request("GET", f"/scouting/tasks/{scout_id}/updates", params=params)

    # -------------------------------------------------------------------------
    # Browsing operations
    # -------------------------------------------------------------------------

    def run_browsing_task(
        self,
        task: str,
        start_url: str,
        max_steps: int | None = None,
        task_spec: dict[str, Any] | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
    ) -> dict[str, Any]:
        """Execute a one-time web browsing task using the navigator agent."""
        payload: dict[str, Any] = {"task": task, "start_url": start_url}
        if max_steps is not None:
            payload["max_steps"] = max_steps
        if task_spec is not None:
            payload["task_spec"] = task_spec
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_format is not None:
            payload["webhook_format"] = webhook_format
        return self._request("POST", "/browsing/tasks", json=payload)

    def get_browsing_task(self, task_id: str) -> dict[str, Any]:
        """Get the status and result of a browsing task."""
        return self._request("GET", f"/browsing/tasks/{task_id}")

    # -------------------------------------------------------------------------
    # Account operations
    # -------------------------------------------------------------------------

    def get_usage(self) -> dict[str, Any]:
        """Get usage statistics for the authenticated API key."""
        return self._request("GET", "/usage")

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

        response = self._client.request(
            method.upper(),
            url,
            headers=headers,
            json=json,
            params=params,
        )

        if response.status_code >= 400:
            raise YutoriAPIError(
                message=response.text or "Yutori API request failed",
                status_code=response.status_code,
            )

        if response.content:
            return response.json()
        return {}
