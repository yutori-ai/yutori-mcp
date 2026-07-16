"""Unit tests for the computer-use pure logic (no cua-driver or API needed)."""

from __future__ import annotations

import json

from yutori_mcp.computer_use.cua import CuaWindow, pick_best_window
from yutori_mcp.computer_use.driver import (
    CuaComputerUseDriver,
    _Capture,
    normalize_key,
    normalize_modifier,
)
from yutori_mcp.computer_use.loop import prune_screenshots


def _window(window_id, width, height, z_index, on_screen=True, on_space=True):
    return CuaWindow(
        window_id=window_id,
        title="",
        is_on_screen=on_screen,
        on_current_space=on_space,
        width=width,
        height=height,
        z_index=z_index,
    )


class TestPickBestWindow:
    def test_empty(self):
        assert pick_best_window([]) is None

    def test_prefers_frontmost_meaningful_window(self):
        parent = _window(1, 1600, 1000, z_index=10)
        dialog = _window(2, 400, 300, z_index=20)
        assert pick_best_window([parent, dialog]).window_id == 2

    def test_edge_floor_excludes_menu_strips(self):
        # Calculator-style 2560x30 strips outrank the real window in z-order
        # and have a large area; the edge floor must exclude them even when
        # they report on-screen.
        strip = _window(1, 2560, 30, z_index=99)
        real = _window(2, 230, 408, z_index=10)
        assert pick_best_window([strip, real]).window_id == 2

    def test_off_screen_windows_excluded(self):
        off = _window(1, 800, 600, z_index=99, on_screen=False)
        real = _window(2, 230, 408, z_index=10)
        assert pick_best_window([off, real]).window_id == 2

    def test_fallback_largest_when_nothing_qualifies(self):
        small = _window(1, 50, 50, z_index=99)
        off = _window(2, 800, 600, z_index=10, on_screen=False)
        assert pick_best_window([small, off]).window_id == 2


class TestKeyNormalization:
    def test_key_names(self):
        assert normalize_key("enter") == "return"
        assert normalize_key("esc") == "escape"
        assert normalize_key("backspace") == "delete"
        assert normalize_key("period") == "."
        assert normalize_key("a") == "a"

    def test_modifiers(self):
        assert normalize_modifier("ctrl", ctrl_to_cmd=True) == "cmd"
        assert normalize_modifier("ctrl", ctrl_to_cmd=False) == "ctrl"
        assert normalize_modifier("alt", ctrl_to_cmd=True) == "option"
        assert normalize_modifier("meta", ctrl_to_cmd=True) == "cmd"


class TestDenormalization:
    def _driver(self, width, height):
        driver = CuaComputerUseDriver(pid=1, window_id=1)
        driver._capture = _Capture(window_id=1, click_width=width, click_height=height)
        return driver

    def test_scales_to_capture_dims(self):
        driver = self._driver(460, 816)
        assert driver._to_click_pixels((500, 500)) == (230, 408)
        assert driver._to_click_pixels((0, 0)) == (0, 0)
        assert driver._to_click_pixels((1000, 1000)) == (460, 816)

    def test_requires_capture(self):
        driver = CuaComputerUseDriver(pid=1, window_id=1)
        result = driver.execute("left_click", json.dumps({"coordinates": [10, 10]}))
        assert result.startswith("[ERROR]")

    def test_unsupported_action(self):
        driver = self._driver(100, 100)
        assert driver.execute("teleport", "{}").startswith("[ERROR] Unsupported")

    def test_click_requires_coordinates(self):
        driver = self._driver(100, 100)
        assert driver.execute("left_click", "{}") == "[ERROR] click requires coordinates"


class TestPruneScreenshots:
    def _message_with_image(self, tag):
        return {
            "role": "tool",
            "tool_call_id": tag,
            "content": [
                {"type": "text", "text": "ok"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 500}},
            ],
        }

    def test_noop_when_under_budget(self):
        messages = [self._message_with_image("a")]
        assert prune_screenshots(messages, 1_000_000) == 0

    def test_drops_oldest_keeps_newest(self):
        messages = [self._message_with_image(tag) for tag in ("a", "b", "c")]
        dropped = prune_screenshots(messages, 1200)
        assert dropped == 2
        # Newest image survives; older ones are replaced with placeholders.
        assert any(
            part.get("type") == "image_url" for part in messages[2]["content"]
        )
        assert all(
            part.get("type") != "image_url" for part in messages[0]["content"]
        )
        assert "[Earlier screenshot omitted.]" in json.dumps(messages[0])
