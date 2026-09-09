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

from .targeting import require_frontmost_target

_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_APP_BUNDLE_IDS = {"finder": "com.apple.finder"}
_FRONTING_SETTLE_MS = 800
# A freshly unhidden window needs a moment before its first capture; a cold launch may
# take a few polls before it has any window at all.
_BACKGROUND_SETTLE_MS = 300
_WINDOW_POLL_MS = 250
_WINDOW_POLL_ATTEMPTS = 12
_MIN_IMMEDIATE_UNTITLED_WINDOW_AREA = 60_000


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


def _best_content_window(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the frontmost content window without mistaking a tiny UI host for the app.

    SwiftUI can keep an untitled ``NSCampoLightweightUIHostWindow`` above the real window.
    CuaDriver does not expose the AppKit class name, so use a deliberately narrow proxy: when
    that untitled frontmost candidate is under one quarter the area of a titled content window,
    prefer the titled window. Larger untitled sheets and apps whose windows are all untitled
    retain the normal z-order behavior.
    """
    frontmost = max(candidates, key=lambda window: (window.get("z_index") or 0, _area(window)))
    if isinstance(frontmost.get("title"), str) and frontmost["title"].strip():
        return frontmost
    titled = [
        window for window in candidates if isinstance(window.get("title"), str) and window["title"].strip()
    ]
    if not titled:
        return frontmost
    largest_titled = max(titled, key=_area)
    if _area(frontmost) * 4 >= _area(largest_titled):
        return frontmost
    return max(titled, key=lambda window: (window.get("z_index") or 0, _area(window)))


def _content_windows(windows: list[dict[str, Any]], min_edge_points: float) -> list[dict[str, Any]]:
    """Windows whose shorter edge clears ``min_edge_points``, excluding menu-bar-strip-sized helpers.

    Shared by :func:`pick_best_window` and :func:`_best_fallback_window`, which both need the
    same "is this big enough to be real content" cut before picking among the survivors.
    """
    return [
        window for window in windows if min(window["bounds"]["width"], window["bounds"]["height"]) >= min_edge_points
    ]


def _best_fallback_window(
    windows: list[dict[str, Any]], min_edge_points: float = 100
) -> dict[str, Any] | None:
    """Choose eventual background fallback content without favoring a visible helper host."""
    if not windows:
        return None
    content = _content_windows(windows, min_edge_points)
    return _best_content_window(content) if content else max(windows, key=_area)


def pick_best_window(windows: list[dict[str, Any]], min_edge_points: float = 100) -> dict[str, Any] | None:
    """Prefer the frontmost visible current-Space content window, excluding helper strips.

    Content windows (both edges at least ``min_edge_points``) that are off screen or hidden
    still beat helper strips: a background launch leaves every window off screen, and a
    menu-bar strip can out-area the app's real window.
    """
    if not windows:
        return None
    content = _content_windows(windows, min_edge_points)
    visible = [
        window
        for window in content
        if window.get("is_on_screen") is not False and window.get("on_current_space") is not False
    ]
    on_space = [window for window in content if window.get("on_current_space") is not False]
    for candidates in (visible, on_space, content):
        if candidates:
            return _best_content_window(candidates)
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
    fallback: dict[str, Any] | None = None
    for _ in range(_WINDOW_POLL_ATTEMPTS):
        windows = _windows(await computer.list_windows(pid))
        window = pick_best_window(windows)
        if window is not None:
            # The immediate choice prefers visible windows. For the eventual fallback,
            # compare every content window so a visible lightweight SwiftUI host cannot
            # mask the app's legitimate titled window while it remains off screen.
            fallback = _best_fallback_window(windows)
            # A cold launch can briefly expose a stale offscreen UI-host record before
            # the application's real window reaches WindowServer. Give unhide time to
            # produce an on-screen target, but retain the best offscreen content window
            # for apps that intentionally keep their only window there.
            titled = isinstance(window.get("title"), str) and bool(window["title"].strip())
            substantial = _area(window) >= _MIN_IMMEDIATE_UNTITLED_WINDOW_AREA
            if window.get("is_on_screen") is not False and (titled or substantial):
                return window
        await computer.wait(_WINDOW_POLL_MS)
    if fallback is not None:
        return fallback
    raise RuntimeError(f"{app!r} is running (pid {pid}) but showed no window to target in background mode")


async def prepare_app(
    computer: MacOSComputer, app: str, start_url: str | None, *, front: bool = True
) -> dict[str, Any]:
    """Launch one allowed target application and make it drivable.

    ``front=True`` (foreground runs) fronts it and verifies that its PID actually owns the
    foreground before returning. ``front=False`` (background runs) never steals focus:
    ``launch_app`` leaves the app hidden, so it is unhidden behind the user's windows and the
    window to drive is resolved and returned as ``window_id``.
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
        await require_frontmost_target(
            computer,
            pid,
            tool="foreground setup",
            target_name=str(payload.get("name") or app),
        )
    else:
        # Unhiding is best-effort just like foreground fronting: the window may
        # already be usable. Always refresh the window list after launch: the launch
        # response can contain only a transient SwiftUI host even when the real content
        # window is ready, while list_windows returns the complete current inventory.
        with suppress(CuaDriverError):
            await computer.unhide_app(pid)
        await computer.wait(_BACKGROUND_SETTLE_MS)
        window = await _await_window(computer, pid, app)
    return {
        "name": str(payload.get("name") or app),
        "pid": pid,
        "window_id": window.get("window_id") if window else None,
    }
