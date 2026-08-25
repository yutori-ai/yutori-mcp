"""MCP-owned target-app launch and fronting policy."""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

from yutori.navigator.macos import MacOSComputer
from yutori.navigator.macos.transport import (
    CuaDriverError,
    CuaDriverToolError,
    CuaDriverUncertainActionError,
)

_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_FRONTING_SETTLE_MS = 800


def _windows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for window in payload.get("windows") or []:
        if not isinstance(window, dict) or window.get("window_id") is None:
            continue
        bounds = window.get("bounds")
        if not isinstance(bounds, dict) or not all(
            isinstance(bounds.get(edge), (int, float)) for edge in ("width", "height")
        ):
            continue
        windows.append(window)
    return windows


def pick_best_window(windows: list[dict[str, Any]], min_edge_points: float = 100) -> dict[str, Any] | None:
    """Prefer a visible current-Space window, excluding helper strips."""
    if not windows:
        return None
    visible = [
        window
        for window in windows
        if window.get("is_on_screen") is not False
        and window.get("on_current_space") is not False
        and min(window["bounds"]["width"], window["bounds"]["height"]) >= min_edge_points
    ]
    if visible:
        return max(visible, key=lambda window: window.get("z_index") or 0)
    return max(windows, key=lambda window: window["bounds"]["width"] * window["bounds"]["height"])


def _is_missing_app(error: CuaDriverToolError) -> bool:
    return "APP_NOT_INSTALLED" in str(error).upper()


def _find_running_app(payload: dict[str, Any], requested: str) -> dict[str, Any] | None:
    requested = requested.casefold()
    for candidate in payload.get("apps") or []:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("pid"), int) or candidate["pid"] <= 0:
            continue
        identities = candidate.get("name"), candidate.get("bundle_id")
        if any(isinstance(value, str) and value.casefold() == requested for value in identities):
            return candidate
    return None


async def _running_app(computer: MacOSComputer, requested: str) -> dict[str, Any] | None:
    # SDK 0.9.1 has no public list_apps convenience method. Its generic hook
    # retains deadline/Stop cancellation while keeping the transport SDK-owned.
    result = await computer._call_tool("list_apps", {}, read_only=True)
    payload = result.get("structuredContent") or result.get("structured_content") or {}
    return _find_running_app(payload if isinstance(payload, dict) else {}, requested)


async def prepare_app(computer: MacOSComputer, app: str, start_url: str | None) -> dict[str, Any]:
    """Launch and best-effort front one allowed target application."""
    urls = [start_url] if start_url else None
    launch_error: CuaDriverToolError | None = None
    try:
        if _BUNDLE_ID_PATTERN.match(app):
            try:
                payload = await computer.launch_app(bundle_id=app, urls=urls)
            except CuaDriverToolError as error:
                if not _is_missing_app(error):
                    raise
                payload = await computer.launch_app(name=app, urls=urls)
        else:
            payload = await computer.launch_app(name=app, urls=urls)
    except CuaDriverToolError as error:
        if not _is_missing_app(error):
            raise
        launch_error = error
        payload = {}
    pid = payload.get("pid")
    if not isinstance(pid, int):
        running = await _running_app(computer, app)
        if running is None:
            if launch_error is not None:
                raise launch_error
            raise RuntimeError(f"launch_app returned no pid for {app!r}")
        payload = running
        pid = running["pid"]

    window = pick_best_window(_windows(payload))
    try:
        await computer.bring_to_front(pid, window.get("window_id") if window else None)
    except CuaDriverUncertainActionError:
        pass
    except CuaDriverToolError:
        with suppress(CuaDriverError):
            await computer.bring_to_front(pid)
    except CuaDriverError:
        pass
    await computer.wait(_FRONTING_SETTLE_MS)
    return {"name": str(payload.get("name") or app), "pid": pid}
