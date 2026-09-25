"""Model-owned app selection for background runs, including a capture-free first turn."""

from __future__ import annotations

import json
from typing import Any

from yutori.navigator import N2ComputerAgent
from yutori.navigator.macos import MacOSWindowTarget
from yutori.navigator.macos.transport import CuaDriverError

from .app import find_running_app, prepare_app, ready_window
from .result import is_positive_int, structured_content
from .targeting import TargetGuardedMacOSComputer

SELECT_APP_TOOL = {
    "type": "function",
    "function": {
        "name": "select_app",
        "description": (
            "Select an application to work in without taking the user's focus. Launches it if needed, "
            "then returns its app/menu state, available window IDs, and a screenshot when a window exists. "
            "An app with no windows is valid, but coordinates and menus are unavailable until a window exists. "
            "Wait for a window or select another app. Call this before GUI actions "
            "and whenever you need another app. Optionally select a specific window ID from a previous "
            "result. Make this the only tool call in a turn; inspect its screenshot before acting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Installed application name or bundle identifier.",
                },
                "window_id": {
                    "type": "integer",
                    "description": "Optional window ID belonging to this app.",
                },
                "url": {
                    "type": "string",
                    "description": "Optional http(s) URL to open in the selected browser.",
                },
            },
            "required": ["app"],
            "additionalProperties": False,
        },
    },
}

APP_STATE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_app_state",
        "description": (
            "Read the selected app's windows and the menu items exposed by its window's accessibility "
            "snapshot, without requesting activation. Screenshots never include menus; this is the only "
            "way to read them. Zero windows is valid but menus are then unavailable. Menus may be "
            "incomplete. Call alone and inspect the result."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}
APP_MENU_TOOL = {
    "type": "function",
    "function": {
        "name": "invoke_app_menu",
        "description": (
            "Press one menu item by its exact full path from get_app_state, using background element "
            "delivery. Requires a window. The snapshot already lists submenu items, so pass the complete "
            "path to the item; top-level menu titles are refused because pressing one opens the menu on "
            "the user's screen. Unavailable, ambiguous, disabled, or stale targets are refused without "
            "foreground fallback. Call alone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 16,
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}
PASTE_TEXT_TOOL = {
    "type": "function",
    "function": {
        "name": "paste_text",
        "description": (
            "Paste text into the selected window at its text cursor, then restore the user's clipboard. "
            "Click the field first; select existing text first to replace it. Apps accept pasted text as "
            "typed input, so prefer this over typing for URLs or searches you will submit, multi-line "
            "text, and long text. It never presses Enter. Requires a window. Call alone and check the "
            "screenshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 20000},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}
APP_TOOLS = [SELECT_APP_TOOL, APP_STATE_TOOL, APP_MENU_TOOL, PASTE_TEXT_TOOL]
APP_TOOL_NAMES = {tool["function"]["name"] for tool in APP_TOOLS}
# Tools that act on the selected window, not just the selected app.
WINDOW_TOOL_NAMES = {"invoke_app_menu", "paste_text"}


class AppSelectingComputer(TargetGuardedMacOSComputer):
    selected_app: str | None = None
    selection_frame_delivered = False
    # The driver's list_apps payload behind the run's inventory: running apps carry their pid.
    app_catalog: dict[str, Any] | None = None

    def _bind_window_target(self, target: MacOSWindowTarget | None) -> None:
        super()._bind_window_target(target)
        self.selection_frame_delivered = False

    async def on_screenshot(self, raw_base64: str, _stage: str) -> None:
        observation = self.current_observation
        if observation is not None and observation.base64 == raw_base64:
            self.selection_frame_delivered = True

    async def app_inventory(self, catalog: dict[str, Any] | None = None) -> str:
        """The model-facing app list, from ``catalog`` when the caller already fetched one."""
        # cua-driver's catalog includes installed apps as well as running applications.
        if catalog is None:
            catalog = structured_content(await self._call_tool("list_apps", {}, read_only=True))
        self.app_catalog = catalog
        apps = [
            {key: app[key] for key in ("name", "bundle_id", "running") if key in app}
            for app in catalog.get("apps") or []
            if isinstance(app, dict) and isinstance(app.get("name"), str)
        ]
        return json.dumps(apps, ensure_ascii=False)

    async def select_app(self, app: str, *, window_id: int | None = None, url: str | None = None) -> dict[str, Any]:
        if not isinstance(app, str) or not app.strip():
            raise ValueError("select_app requires an application name or bundle identifier")
        if window_id is not None and not is_positive_int(window_id):
            raise ValueError("window_id must be a positive integer")
        if url is not None and (not isinstance(url, str) or not url.startswith(("https://", "http://"))):
            raise ValueError("url must be an http(s) URL")
        target = None if url is not None else await self._attach_running_app(app.strip())
        if target is None:
            target = await prepare_app(self, app.strip(), url, front=False)
        windows = (await self.list_windows(target["pid"])).get("windows") or []
        if window_id is not None:
            if not any(window.get("window_id") == window_id for window in windows):
                raise ValueError("The requested window does not belong to the selected application")
            target["window_id"] = window_id
        window = (
            MacOSWindowTarget(target["pid"], target["window_id"], app_name=target["name"])
            if isinstance(target.get("window_id"), int)
            else None
        )
        await self.set_app_target(target["pid"], window=window)
        self.selected_app = app.strip()
        self.recover_target = self.recover_selected_app
        return {
            **target,
            "windows": [{key: window[key] for key in ("window_id", "title") if key in window} for window in windows],
        }

    async def _attach_running_app(self, app: str) -> dict[str, Any] | None:
        """Skip the launch for an app that is already running with a drivable window.

        ``launch_app`` takes over a second even when the app is running. The inventory's catalog
        already names a running app's pid, and one window listing proves its window is on
        screen, so the launch, unhide and settle wait add nothing. Anything less certain (not
        in the catalog, no ready window, a pid that is gone) takes the full launch path.
        """
        running = find_running_app(self.app_catalog or {}, app)
        if running is None:
            return None
        try:
            window = await ready_window(self, running["pid"])
        except CuaDriverError:
            return None
        if window is None:
            return None
        return {"name": str(running.get("name") or app), "pid": running["pid"], "window_id": window.get("window_id")}

    async def recover_selected_app(self) -> int | None:
        if self.selected_app is None:
            return None
        # Never replay the initial URL on recovery or return to the first app after a handoff.
        return (await self.select_app(self.selected_app))["pid"]

    async def run_custom_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "select_app":
            return json.dumps(await self.select_app(**arguments), ensure_ascii=False)
        if name == "get_app_state":
            return (await self.get_app_state()).text
        if name == "invoke_app_menu":
            await self.invoke_app_menu(**arguments)
            outcome = self.action_outcomes[-1]
            state = await self.get_app_state()
            return (
                f"Menu item pressed via {outcome.route or 'accessibility'} (effect: {outcome.effect or 'unknown'}). "
                f"Fresh app state follows; verify the effect before continuing.\n{state.text}"
            )
        if name == "paste_text":
            await self.paste_text(**arguments)
            outcome = self.action_outcomes[-1]
            return (
                f"Pasted {len(arguments['text'])} characters via {outcome.route or 'keyboard'} "
                f"(effect: {outcome.effect or 'unknown'}). Check the screenshot before continuing."
            )
        raise ValueError(f"Unknown app tool: {name}")


class AppSelectingAgent(N2ComputerAgent):
    async def _resolve_native_size(self) -> tuple[int, int]:
        if not self.computer.selection_frame_delivered:
            # The SDK parses every response with dimensions, even a text-only tool call.
            # No coordinates are executed in this state; the first real capture supplies them.
            return (1000, 1000)
        return await super()._resolve_native_size()

    async def _predict_step(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        result = await super()._predict_step(items)
        output = result.get("output") or []
        answered = {item.get("call_id") for item in output if item.get("type") == "function_call_output"}
        calls = [item for item in output if item.get("type") == "function_call" and item.get("call_id") not in answered]
        selection = next((item for item in calls if item.get("name") in APP_TOOL_NAMES), None)
        has_target = self.computer.window_target_info is not None
        has_app = getattr(self.computer, "target_pid", None) is not None or has_target
        for item in calls:
            if item is selection and (item.get("name") == "select_app" or has_app) and (
                item.get("name") not in WINDOW_TOOL_NAMES or has_target
            ):
                continue
            actions = item.get("_computer_actions") or []
            observation_only = bool(actions) and all(action.get("type") in {"screenshot", "wait"} for action in actions)
            missing_frame = has_target and not self.computer.selection_frame_delivered and not observation_only
            app_observation = observation_only and has_app
            if selection is not None or (not has_target and not app_observation) or missing_frame:
                output.append(
                    {
                        "type": "function_call_output",
                        "call_id": item["call_id"],
                        "output": "[ERROR] Select an app in a separate turn. App-state reads work without a window, "
                        "but menu and paste actions require a window and coordinate actions require a fresh "
                        "screenshot. "
                        "Call app tools alone. If the capture failed, request a screenshot-only batch.",
                        "_n2_turn_id": item.get("_n2_turn_id"),
                    }
                )
        return result
