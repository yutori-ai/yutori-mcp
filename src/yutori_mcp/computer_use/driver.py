"""Maps n2's coordinate action vocabulary onto cua-driver's pixel tools.

n2 emits tool calls named ``left_click``/``type``/``key_press``/… with
coordinates in a resolution-independent 0-1000 space; cua-driver pixel actions
expect window-local screenshot pixels. The driver captures each observation,
remembers its dimensions, and denormalizes every coordinate against the most
recent capture, so the image the model saw and the click space always agree.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field

from .cua import CuaCliError, cua_call, cua_screenshot, list_windows, pick_best_window

N2_COORDINATE_SCALE = 1000
SCREENSHOT_QUALITY = 75
MAX_WAIT_SECONDS = 10
# cua-driver caps its own get_window_state PNG at this long side by default;
# capping our captures to the same value keeps clicks in the driver's
# documented coordinate space.
MAX_IMAGE_LONG_SIDE = 1568

# n2 key names -> cua-driver key names. Unlisted names pass through.
_KEY_NAME_MAP = {
    "enter": "return",
    "esc": "escape",
    "backspace": "delete",
    "page_up": "pageup",
    "page_down": "pagedown",
}

# n2 word-form punctuation -> literal characters.
_PUNCTUATION_MAP = {
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "quote": "'",
    "backquote": "`",
    "bracketleft": "[",
    "bracketright": "]",
}

_MODIFIER_NAMES = {
    "ctrl",
    "control",
    "shift",
    "alt",
    "option",
    "meta",
    "cmd",
    "command",
    "super",
}


def normalize_modifier(name: str, ctrl_to_cmd: bool) -> str:
    lower = name.lower()
    if lower in ("ctrl", "control"):
        return "cmd" if ctrl_to_cmd else "ctrl"
    if lower == "alt":
        return "option"
    if lower in ("meta", "command", "super"):
        return "cmd"
    return lower


def normalize_key(name: str) -> str:
    lower = name.lower()
    return _PUNCTUATION_MAP.get(lower) or _KEY_NAME_MAP.get(lower, lower)


def _coord(value: object) -> tuple[float, float] | None:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return (float(value[0]), float(value[1]))
    return None


@dataclass
class _Capture:
    window_id: int
    click_width: int
    click_height: int


@dataclass
class CuaComputerUseDriver:
    """Executes n2 tool calls against one macOS app via cua-driver."""

    pid: int
    window_id: int
    ctrl_to_cmd: bool = True
    _capture: _Capture | None = field(default=None, init=False)

    # -- observation ---------------------------------------------------------

    def _resolve_window(self) -> int:
        """Re-pick the observation window on every capture, so a dialog/sheet
        the app opens (a new frontmost window) becomes the observation target
        and the parent is observed again once the dialog closes."""
        try:
            windows = list_windows(self.pid)
        except CuaCliError:
            return self.window_id
        best = pick_best_window(windows)
        if best:
            self.window_id = best.window_id
        return self.window_id

    def screenshot(self) -> str:
        """Capture the current observation and return it as a data URL."""
        window_id = self._resolve_window()
        data, width, height = cua_screenshot(
            window_id, SCREENSHOT_QUALITY, MAX_IMAGE_LONG_SIDE
        )
        self._capture = _Capture(window_id, width, height)
        return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"

    # -- actions -------------------------------------------------------------

    def _to_click_pixels(self, point: tuple[float, float]) -> tuple[int, int]:
        # Assumes the loop's one-tool-call-per-turn contract
        # (parallel_tool_calls=false): the loop screenshots after every action,
        # so a batch of several tool calls would have its later actions
        # denormalized against a frame the model never saw.
        if self._capture is None:
            raise CuaCliError("no screenshot captured yet — cannot denormalize coordinates")
        return (
            round(point[0] / N2_COORDINATE_SCALE * self._capture.click_width),
            round(point[1] / N2_COORDINATE_SCALE * self._capture.click_height),
        )

    def _run(self, tool: str, args: dict, ok_text: str) -> str:
        result = cua_call(tool, args)
        if result.is_error:
            return f"[ERROR] {tool} failed: {result.text[:300]}"
        return ok_text

    def _click(self, point: tuple[float, float], count: int, ok_text: str) -> str:
        x, y = self._to_click_pixels(point)
        assert self._capture is not None
        return self._run(
            "click",
            {"pid": self.pid, "window_id": self._capture.window_id, "x": x, "y": y, "count": count},
            ok_text,
        )

    @staticmethod
    def _split_key_spec(spec: str) -> list[str]:
        """Split a key spec into modifier tokens followed by the final key.

        Splits on ``+`` but preserves a literal ``+`` as the final key. A naive
        ``spec.split("+")`` turns the ``+`` key into empty tokens — so ``"+"``
        yields no key at all and ``"ctrl++"`` drops the key and looks
        modifier-only — so a spec that ends in ``+`` means the final key is
        itself ``+``.
        """
        stripped = spec.strip()
        if not stripped:
            return []
        parts = [part for part in (p.strip() for p in stripped.split("+")) if part]
        if stripped.endswith("+"):
            parts.append("+")
        return parts

    def _press_key_spec(self, spec: str) -> str:
        parts = self._split_key_spec(spec)
        if not parts:
            return "[ERROR] key_press requires a key"
        *modifier_tokens, key_token = parts
        if modifier_tokens and key_token.lower() in _MODIFIER_NAMES:
            return f'[ERROR] key combo "{spec}" ends in a modifier'
        modifiers = [normalize_modifier(p, self.ctrl_to_cmd) for p in modifier_tokens]
        key = normalize_key(key_token)
        window_id = self._capture.window_id if self._capture else None
        # cua-driver's press_key/hotkey know named keys and alphanumerics only
        # ("Unknown key name: +"), so any punctuation key has to use a text
        # insert instead of the key tools.
        if len(key) == 1 and not key.isalnum():
            if modifiers:
                # A punctuation key held with modifiers (e.g. cmd+/) can't go
                # through the key tools, and a bare type_text would silently
                # drop the modifiers — so surface it instead of sending a
                # keystroke that doesn't match what the model asked for.
                return (
                    f'[ERROR] key combo "{spec}" is not supported: cua-driver '
                    "cannot send punctuation together with modifier keys."
                )
            # Bare punctuation falls back to a text insert. That path can no-op
            # in apps without a text field (verified against Calculator), so the
            # result tells the model to check the screenshot rather than
            # claiming the keystroke landed.
            return self._run(
                "type_text",
                {"pid": self.pid, "window_id": window_id, "text": key},
                f"Sent '{key}' as text input (best effort — verify in the screenshot; "
                "click the on-screen control if it did not register).",
            )
        if modifiers:
            return self._run(
                "hotkey",
                {"pid": self.pid, "window_id": window_id, "keys": [*modifiers, key]},
                f"Pressed {'+'.join([*modifiers, key])}.",
            )
        return self._run(
            "press_key",
            {"pid": self.pid, "window_id": window_id, "key": key},
            f"Pressed {key}.",
        )

    def execute(self, name: str, arguments: str | dict) -> str:
        """Execute one n2 tool call; returns a short result string.

        ``[ERROR] …`` results are surfaced to the model as the tool result so
        it can react; they never raise.
        """
        action = name.lower()
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                args = {}
        else:
            args = arguments or {}
        if not isinstance(args, dict):
            args = {}
        point = _coord(args.get("coordinates") or args.get("coordinate") or args.get("center_coordinates"))

        try:
            if action == "screenshot":
                # The loop captures a fresh screenshot after every action, so
                # an explicit screenshot action is a no-op yielding the frame.
                return "Screenshot captured."

            if action in ("left_click", "click"):
                if not point:
                    return "[ERROR] click requires coordinates"
                return self._click(point, 1, f"Clicked at ({point[0]:g}, {point[1]:g}).")

            if action == "double_click":
                if not point:
                    return "[ERROR] double_click requires coordinates"
                return self._click(point, 2, f"Double-clicked at ({point[0]:g}, {point[1]:g}).")

            if action == "triple_click":
                if not point:
                    return "[ERROR] triple_click requires coordinates"
                return self._click(point, 3, f"Triple-clicked at ({point[0]:g}, {point[1]:g}).")

            if action == "right_click":
                if not point:
                    return "[ERROR] right_click requires coordinates"
                x, y = self._to_click_pixels(point)
                assert self._capture is not None
                return self._run(
                    "right_click",
                    {"pid": self.pid, "window_id": self._capture.window_id, "x": x, "y": y},
                    f"Right-clicked at ({point[0]:g}, {point[1]:g}).",
                )

            if action == "middle_click":
                return "[ERROR] middle_click is not supported on this desktop"

            if action in ("mouse_move", "hover"):
                # cua-driver has no backgrounded pointer-move; hovering is a no-op.
                return "Hover is not supported on this desktop; proceed without it."

            if action in ("drag", "left_click_drag"):
                start = _coord(args.get("start_coordinates") or args.get("start_coordinate"))
                end = point or _coord(args.get("end_coordinates") or args.get("end_coordinate"))
                if not start or not end:
                    return "[ERROR] drag requires start and end coordinates"
                from_x, from_y = self._to_click_pixels(start)
                to_x, to_y = self._to_click_pixels(end)
                assert self._capture is not None
                return self._run(
                    "drag",
                    {
                        "pid": self.pid,
                        "window_id": self._capture.window_id,
                        "from_x": from_x,
                        "from_y": from_y,
                        "to_x": to_x,
                        "to_y": to_y,
                    },
                    f"Dragged ({start[0]:g}, {start[1]:g}) -> ({end[0]:g}, {end[1]:g}).",
                )

            if action == "scroll":
                direction = str(args.get("direction") or "down").lower()
                if direction not in ("up", "down", "left", "right"):
                    return f"[ERROR] unsupported scroll direction: {direction}"
                # n2's unit is ~10% of the screen; cua-driver scrolls by
                # keystroke repetitions, so approximate each unit as 3 lines.
                units = args.get("amount") if isinstance(args.get("amount"), (int, float)) else 3
                amount = min(max(round(units * 3), 1), 50)
                window_id = self._capture.window_id if self._capture else None
                return self._run(
                    "scroll",
                    {"pid": self.pid, "window_id": window_id, "direction": direction, "amount": amount, "by": "line"},
                    f"Scrolled {direction} by {units:g}.",
                )

            if action in ("type", "text"):
                text = args.get("text")
                if not isinstance(text, str) or not text:
                    return "[ERROR] type requires text"
                window_id = self._capture.window_id if self._capture else None
                shown = text if len(text) <= 60 else text[:60] + "…"
                return self._run(
                    "type_text",
                    {"pid": self.pid, "window_id": window_id, "text": text},
                    f'Typed "{shown}".',
                )

            if action in ("key_press", "key", "hold_key"):
                raw = args.get("key") or args.get("key_comb") or args.get("text")
                if not isinstance(raw, str) or not raw:
                    return "[ERROR] key_press requires a key"
                # Space-separated specs are a sequence ("down down enter");
                # each spec may itself be a +-combo.
                results = []
                for spec in raw.split():
                    result = self._press_key_spec(spec)
                    results.append(result)
                    if result.startswith("[ERROR]"):
                        break
                    time.sleep(0.06)
                summary = " ".join(results)
                # cua-driver has no key-down/hold primitive, so a hold_key
                # collapses to a single tap. Say so rather than imply the key
                # was held for the requested duration (key-repeat, held
                # modifiers) — the model can adapt (e.g. repeat the press).
                if action == "hold_key" and not summary.startswith("[ERROR]"):
                    summary += (
                        " (Note: press-and-hold is not supported on this "
                        "desktop; the key was tapped once and any hold "
                        "duration was ignored.)"
                    )
                return summary

            if action == "wait":
                requested = args.get("duration") if isinstance(args.get("duration"), (int, float)) else 1
                seconds = min(max(0, requested), MAX_WAIT_SECONDS)
                time.sleep(seconds)
                return f"Waited {seconds:g}s."

            return f"[ERROR] Unsupported computer-use action: {action}"
        except CuaCliError as e:
            return f"[ERROR] {action} failed: {str(e)[:300]}"
