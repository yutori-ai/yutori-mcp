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

    def test_fallback_honors_edge_floor_for_hidden_app(self):
        # Hidden-launched app (this tool's normal mode): nothing reports
        # on-screen/on-current-space, so the primary filter is empty. A wide
        # menu-bar strip has the largest area but must still lose to the real
        # window because the edge floor applies to the fallback too.
        strip = _window(1, 3840, 30, z_index=99, on_screen=False, on_space=False)
        real = _window(2, 230, 408, z_index=10, on_screen=False, on_space=False)
        assert pick_best_window([strip, real]).window_id == 2


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


class TestDrag:
    def _driver(self, monkeypatch):
        import yutori_mcp.computer_use.driver as driver_mod

        self.calls = []

        class _Result:
            is_error = False
            text = ""
            structured = None

        def fake_cua_call(tool, args):
            self.calls.append((tool, args))
            return _Result()

        monkeypatch.setattr(driver_mod, "cua_call", fake_cua_call)
        driver = CuaComputerUseDriver(pid=1, window_id=1)
        driver._capture = _Capture(window_id=1, click_width=1000, click_height=1000)
        return driver

    def test_accepts_singular_start_and_end_coordinate(self, monkeypatch):
        # n2 may send singular `start_coordinate`/`end_coordinate`; both must be
        # honored symmetrically and reach cua-driver.
        driver = self._driver(monkeypatch)
        result = driver.execute(
            "drag",
            json.dumps({"start_coordinate": [100, 200], "end_coordinate": [300, 400]}),
        )
        assert not result.startswith("[ERROR]")
        assert self.calls and self.calls[0][0] == "drag"
        args = self.calls[0][1]
        assert (args["from_x"], args["from_y"]) == (100, 200)
        assert (args["to_x"], args["to_y"]) == (300, 400)

    def test_requires_start_and_end(self, monkeypatch):
        driver = self._driver(monkeypatch)
        result = driver.execute("drag", json.dumps({"start_coordinate": [100, 200]}))
        assert result == "[ERROR] drag requires start and end coordinates"
        assert not self.calls


class TestKeyPress:
    def _driver(self, monkeypatch):
        import yutori_mcp.computer_use.driver as driver_mod

        self.calls = []

        class _Result:
            is_error = False
            text = ""
            structured = None

        def fake_cua_call(tool, args):
            self.calls.append((tool, args))
            return _Result()

        monkeypatch.setattr(driver_mod, "cua_call", fake_cua_call)
        driver = CuaComputerUseDriver(pid=1, window_id=1)
        driver._capture = _Capture(window_id=1, click_width=1000, click_height=1000)
        return driver

    def test_modifier_combo_uses_hotkey(self, monkeypatch):
        driver = self._driver(monkeypatch)
        result = driver.execute("key_press", json.dumps({"key": "ctrl+c"}))
        assert not result.startswith("[ERROR]")
        assert self.calls[0][0] == "hotkey"
        assert self.calls[0][1]["keys"] == ["cmd", "c"]  # ctrl->cmd by default

    def test_bare_punctuation_falls_back_to_text(self, monkeypatch):
        driver = self._driver(monkeypatch)
        result = driver.execute("key_press", json.dumps({"key": "."}))
        assert not result.startswith("[ERROR]")
        assert self.calls[0][0] == "type_text"
        assert self.calls[0][1]["text"] == "."

    def test_literal_plus_key_falls_back_to_text(self, monkeypatch):
        # A lone "+" must not split into empty parts and error; it is a key.
        driver = self._driver(monkeypatch)
        result = driver.execute("key_press", json.dumps({"key": "+"}))
        assert not result.startswith("[ERROR]")
        assert self.calls[0][0] == "type_text"
        assert self.calls[0][1]["text"] == "+"

    def test_modifier_plus_punctuation_is_rejected(self, monkeypatch):
        # cua-driver can't hotkey a punctuation key; don't silently drop the
        # modifier via a bare type_text.
        driver = self._driver(monkeypatch)
        for spec in ("shift+plus", "ctrl++", "cmd+/"):
            self.calls.clear()
            result = driver.execute("key_press", json.dumps({"key": spec}))
            assert result.startswith("[ERROR]"), spec
            assert "punctuation" in result
            assert not self.calls, spec

    def test_combo_ending_in_modifier_errors(self, monkeypatch):
        driver = self._driver(monkeypatch)
        result = driver.execute("key_press", json.dumps({"key": "ctrl+shift"}))
        assert result.startswith("[ERROR]")
        assert "ends in a modifier" in result

    def test_hold_key_notes_unsupported_hold(self, monkeypatch):
        # hold_key collapses to a single tap; the result must say the hold
        # duration was not applied rather than imply the key was held.
        driver = self._driver(monkeypatch)
        result = driver.execute(
            "hold_key", json.dumps({"key": "shift", "duration": 5})
        )
        assert self.calls[0][0] == "press_key"
        assert self.calls[0][1]["key"] == "shift"
        assert "not supported" in result and "duration" in result

    def test_key_press_does_not_add_hold_note(self, monkeypatch):
        driver = self._driver(monkeypatch)
        result = driver.execute("key_press", json.dumps({"key": "enter"}))
        assert "not supported" not in result
        assert self.calls[0][1]["key"] == "return"


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


class TestServerModuleOrdering:
    def test_main_guard_is_last_top_level_statement(self):
        # main() blocks forever under `python -m yutori_mcp.server`, so any
        # top-level def/assignment after the `if __name__ == "__main__"` guard
        # (e.g. _run_computer_use) would never bind and computer_use_task would
        # raise NameError. The guard must be the final top-level statement.
        import ast
        import inspect

        import yutori_mcp.server as server_mod

        tree = ast.parse(inspect.getsource(server_mod))
        guard_index = None
        for i, node in enumerate(tree.body):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
            ):
                guard_index = i
        assert guard_index is not None, "no `if __name__ == '__main__'` guard found"
        assert guard_index == len(tree.body) - 1, (
            "`if __name__ == '__main__'` must be the last top-level statement so "
            "computer-use helpers are bound before main() blocks"
        )
