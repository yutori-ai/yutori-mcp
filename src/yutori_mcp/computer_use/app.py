"""MCP-owned target-app launch policy: front the app for foreground runs, reveal it quietly for background ones."""

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
_APP_BUNDLE_IDS = {"finder": "com.apple.finder"}
_FRONTING_SETTLE_MS = 800
# A freshly unhidden window needs a moment before its first capture; a cold launch may
# take a few polls before it has any window at all.
_BACKGROUND_SETTLE_MS = 300
_WINDOW_POLL_MS = 250
_WINDOW_POLL_ATTEMPTS = 12


def structured_content(result: dict[str, Any]) -> dict[str, Any]:
    """The structured payload of a ``_call_tool`` result, tolerating either key casing.

    The driver protocol has used both ``structuredContent`` (MCP-style) and
    ``structured_content`` across releases; every caller wants "whichever one is present,
    or an empty dict" rather than caring which.
    """
    value = result.get("structuredContent") or result.get("structured_content") or {}
    return value if isinstance(value, dict) else {}


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


def _area(window: dict[str, Any]) -> float:
    return float(window["bounds"]["width"]) * float(window["bounds"]["height"])


def pick_best_window(windows: list[dict[str, Any]], min_edge_points: float = 100) -> dict[str, Any] | None:
    """Prefer the frontmost visible current-Space content window, excluding helper strips.

    Content windows (both edges at least ``min_edge_points``) that are off screen or hidden
    still beat helper strips: a background launch leaves every window off screen, and a
    menu-bar strip can out-area the app's real window.
    """
    if not windows:
        return None
    content = [
        window for window in windows if min(window["bounds"]["width"], window["bounds"]["height"]) >= min_edge_points
    ]
    visible = [
        window
        for window in content
        if window.get("is_on_screen") is not False and window.get("on_current_space") is not False
    ]
    on_space = [window for window in content if window.get("on_current_space") is not False]
    for candidates in (visible, on_space, content):
        if candidates:
            return max(candidates, key=lambda window: (window.get("z_index") or 0, _area(window)))
    return max(windows, key=_area)


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
    # The pinned SDK has no public list_apps convenience method. Its generic hook
    # retains deadline/Stop cancellation while keeping the transport SDK-owned.
    result = await computer._call_tool("list_apps", {}, read_only=True)
    return _find_running_app(structured_content(result), requested)


async def _await_window(computer: MacOSComputer, pid: int, app: str) -> dict[str, Any]:
    for _ in range(_WINDOW_POLL_ATTEMPTS):
        window = pick_best_window(_windows(await computer.list_windows(pid)))
        if window is not None:
            return window
        await computer.wait(_WINDOW_POLL_MS)
    raise RuntimeError(f"{app!r} is running (pid {pid}) but showed no window to target in background mode")


async def prepare_app(
    computer: MacOSComputer, app: str, start_url: str | None, *, front: bool = True
) -> dict[str, Any]:
    """Launch one allowed target application and make it drivable.

    ``front=True`` (foreground runs) best-effort fronts it. ``front=False`` (background runs)
    never steals focus: ``launch_app`` leaves the app hidden, so it is unhidden behind the
    user's windows and the window to drive is resolved and returned as ``window_id``.
    """
    urls = [start_url] if start_url else None
    bundle_id = app if _BUNDLE_ID_PATTERN.match(app) else _APP_BUNDLE_IDS.get(app.casefold())
    launch_error: CuaDriverToolError | None = None
    try:
        if bundle_id is not None:
            try:
                payload = await computer.launch_app(bundle_id=bundle_id, urls=urls)
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
    if front:
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
    else:
        await computer.unhide_app(pid)
        if window is None:
            window = await _await_window(computer, pid, app)
        await computer.wait(_BACKGROUND_SETTLE_MS)
    return {
        "name": str(payload.get("name") or app),
        "pid": pid,
        "window_id": window.get("window_id") if window else None,
    }
