from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from yutori.navigator import N2ComputerAgent
from yutori.navigator.n2 import N2Observation

from yutori_mcp.computer_use import app_selection
from yutori_mcp.computer_use.app_selection import AppSelectingAgent, AppSelectingComputer, SELECT_APP_TOOL


def computer() -> AppSelectingComputer:
    return AppSelectingComputer(scope="window", presentation=False, transport=SimpleNamespace(), owns_transport=False)


def call(name: str, call_id: str) -> dict:
    return {"type": "function_call", "name": name, "call_id": call_id, "_n2_turn_id": "turn"}


def bootstrap_agent(computer: Any = None) -> AppSelectingAgent:
    """An `AppSelectingAgent` with `__init__` skipped, wired to `computer`.

    `_predict_step`/`_resolve_native_size` only read `self.computer`, so tests that drive them
    directly don't need the full SDK constructor. `computer` defaults to an unselected
    `SimpleNamespace`, the shape every window-target check in those methods expects.
    """
    agent = object.__new__(AppSelectingAgent)
    agent.computer = (
        computer if computer is not None else SimpleNamespace(window_target_info=None, selection_frame_delivered=False)
    )
    return agent


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize(
    "names",
    [
        ["select_app", "computer_batch"],
        ["computer_batch", "select_app"],
        ["select_app", "select_app", "bash"],
        ["computer_batch"],
    ],
)
async def test_selection_is_a_model_turn_boundary(
    monkeypatch: pytest.MonkeyPatch, selected: bool, names: list[str]
) -> None:
    output = [call(name, str(i)) for i, name in enumerate(names)]
    monkeypatch.setattr(N2ComputerAgent, "_predict_step", AsyncMock(return_value={"output": output}))
    agent = bootstrap_agent(
        SimpleNamespace(
            window_target_info={"pid": 1} if selected else None,
            current_observation=object() if selected else None,
            selection_frame_delivered=selected,
        )
    )
    result = await agent._predict_step([])
    refused = {item["call_id"] for item in result["output"] if item["type"] == "function_call_output"}
    if "select_app" in names:
        assert refused == {str(i) for i in range(len(names)) if i != names.index("select_app")}
    else:
        assert refused == (set() if selected else {"0"})


async def test_no_duplicate_results_for_malformed_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    output = [call("select_app", "a"), {"type": "function_call_output", "call_id": "a", "output": "invalid"}]
    monkeypatch.setattr(N2ComputerAgent, "_predict_step", AsyncMock(return_value={"output": output}))
    agent = bootstrap_agent()
    assert len((await agent._predict_step([]))["output"]) == 2


async def test_bootstrap_does_not_capture_the_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = AsyncMock(return_value=(600, 800))
    monkeypatch.setattr(N2ComputerAgent, "_resolve_native_size", resolve)
    agent = bootstrap_agent()
    assert await agent._resolve_native_size() == (1000, 1000)
    resolve.assert_not_awaited()
    agent.computer.window_target_info = {"pid": 42}
    agent.computer.selection_frame_delivered = True
    assert await agent._resolve_native_size() == (600, 800)
    resolve.assert_awaited_once()


async def test_switch_and_recovery_bind_current_app_without_replaying_url(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare = AsyncMock(
        side_effect=[
            {"name": "Safari", "pid": 1, "window_id": 10},
            {"name": "Notes", "pid": 2, "window_id": 20},
            {"name": "Notes", "pid": 3, "window_id": 30},
        ]
    )
    monkeypatch.setattr(app_selection, "prepare_app", prepare)
    instance = computer()
    instance.list_windows = AsyncMock(
        side_effect=[
            {"windows": [{"window_id": 10, "title": "Page"}]},
            {"windows": [{"window_id": 20, "title": "Note"}, {"window_id": 21, "title": "Other note"}]},
            {"windows": [{"window_id": 30, "title": "Note"}]},
        ]
    )
    await instance.select_app("Safari", url="https://example.com")
    instance._current_observation = object()
    instance._native_size = (123, 456)
    result = await instance.select_app("Notes", window_id=21)
    assert result["window_id"] == 21
    assert instance.window_target_info["window_id"] == 21
    assert instance.current_observation is None
    assert instance._native_size is None
    assert await instance.recover_target() == 3
    assert instance.selected_app == "Notes"
    assert instance.window_target_info["pid"] == 3
    assert [(c.args[1:], c.kwargs) for c in prepare.await_args_list] == [
        (("Safari", "https://example.com"), {"front": False}),
        (("Notes", None), {"front": False}),
        (("Notes", None), {"front": False}),
    ]


async def test_rejects_window_from_another_app_without_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_selection, "prepare_app", AsyncMock(return_value={"name": "Notes", "pid": 2, "window_id": 20})
    )
    instance = computer()
    instance.list_windows = AsyncMock(return_value={"windows": [{"window_id": 20}]})
    with pytest.raises(ValueError, match="does not belong"):
        await instance.select_app("Notes", window_id=99)
    assert instance.window_target_info is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"app": ""},
        {"app": 5},
        {"app": "Notes", "window_id": True},
        {"app": "Notes", "url": "file:///tmp/x"},
        {"app": "Notes", "window_id": -1},
    ],
)
async def test_invalid_selection_never_launches(monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any]) -> None:
    prepare = AsyncMock()
    monkeypatch.setattr(app_selection, "prepare_app", prepare)
    with pytest.raises(ValueError):
        await computer().select_app(**arguments)
    prepare.assert_not_awaited()


async def test_inventory_contains_installed_and_running_apps_without_windows() -> None:
    instance = computer()
    instance._call_tool = AsyncMock(
        return_value={
            "structuredContent": {
                "apps": [
                    {"name": "Notes", "bundle_id": "com.apple.Notes"},
                    {"name": "Editor", "bundle_id": "org.editor", "pid": 12, "windows": ["private title"]},
                ]
            }
        }
    )
    inventory = json.loads(await instance.app_inventory())
    assert inventory == [
        {"name": "Notes", "bundle_id": "com.apple.Notes"},
        {"name": "Editor", "bundle_id": "org.editor"},
    ]
    instance._call_tool.assert_awaited_once_with("list_apps", {}, read_only=True)


@pytest.mark.parametrize("capture_fails", [False, True])
async def test_real_sdk_loop_selects_apps_without_initial_screen_and_rejects_stale_calls(capture_fails: bool) -> None:
    stream = io.BytesIO()
    Image.new("RGB", (40, 30)).save(stream, format="PNG")
    frame = N2Observation(
        encoded_bytes=stream.getvalue(),
        media_type="image/png",
        capture_id=1,
        native_width=40,
        native_height=30,
        encoded_width=40,
        encoded_height=30,
    )

    class WindowComputer:
        def __init__(self) -> None:
            self.window_target_info = None
            self.selection_frame_delivered = False
            self.current_observation = None
            self.selections = []
            self.clicks = []
            self.frames = []
            self.failed_capture = False

        async def run_custom_tool(self, name: str, arguments: dict[str, Any]) -> str:
            assert name == "select_app"
            self.window_target_info = {"app_name": arguments["app"]}
            self.current_observation = None
            self.selection_frame_delivered = False
            self.selections.append(arguments["app"])
            return json.dumps(self.window_target_info)

        async def screenshot(self) -> N2Observation:
            assert self.window_target_info is not None, "Captured before selecting an app"
            if capture_fails and self.window_target_info["app_name"] == "Notes" and not self.failed_capture:
                self.failed_capture = True
                raise RuntimeError("capture unavailable")
            self.current_observation = frame
            self.frames.append(self.window_target_info["app_name"])
            return frame

        async def on_screenshot(self, raw_base64: str, stage: str) -> None:
            self.selection_frame_delivered = True

        async def get_dimensions(self) -> tuple[int, int]:
            assert self.window_target_info is not None
            return (40, 30)

        async def click(self, x: int, y: int, **kwargs: Any) -> None:
            self.clicks.append((self.window_target_info["app_name"], x, y))

    def tool(name: str, arguments: dict[str, Any], id: str) -> dict[str, Any]:
        return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}

    batch = {"actions": [{"name": "left_click", "arguments": {"coordinates": [500, 500]}}]}
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [tool("select_app", {"app": "Safari"}, "1"), tool("computer_batch", batch, "2")],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "tool_calls": [tool("select_app", {"app": "Notes"}, "3")]}}]},
        {"choices": [{"message": {"role": "assistant", "tool_calls": [tool("computer_batch", batch, "4")]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "Done"}}]},
    ]
    if capture_fails:
        retry = {"actions": [{"name": "screenshot", "arguments": {}}]}
        responses[2:2] = [
            {"choices": [{"message": {"role": "assistant", "tool_calls": [tool("computer_batch", batch, "stale")]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [tool("computer_batch", retry, "retry")]}}]},
        ]
    requests = []

    async def create(**kwargs: Any) -> dict[str, Any]:
        requests.append(copy.deepcopy(kwargs))
        return responses.pop(0)

    instance = WindowComputer()
    agent = AppSelectingAgent(
        computer=instance,
        tools=[SELECT_APP_TOOL],
        completions=SimpleNamespace(create=create),
        callbacks=[instance],
        compactor=None,
        screenshot_delay=0,
    )
    async for _ in agent.run("Read Safari then work in Notes"):
        pass
    assert instance.selections == ["Safari", "Notes"]
    assert instance.clicks == [("Notes", 20, 15)]
    assert instance.frames[:2] == ["Safari", "Notes"]
    assert "image_url" not in json.dumps(requests[0]["messages"])
    assert "Select an app in a separate turn" in json.dumps(requests[1]["messages"])


async def test_internal_dimension_capture_cannot_unlock_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = computer()
    instance._target_window = SimpleNamespace(pid=1, window_id=2, title=None, app_name="Notes")

    async def predict(_self: N2ComputerAgent, _items: list[dict[str, Any]]) -> dict[str, Any]:
        instance._current_observation = object()
        return {"output": [call("computer_batch", "stale")]}

    monkeypatch.setattr(N2ComputerAgent, "_predict_step", predict)
    agent = bootstrap_agent(instance)
    response = await agent._predict_step([])
    assert response["output"][-1]["type"] == "function_call_output"
    assert "capture failed" in response["output"][-1]["output"]
