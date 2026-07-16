"""Thin wrapper around the cua-driver CLI (https://github.com/trycua/cua).

cua-driver is an open-source background computer-use driver for macOS: it
snapshots app windows and posts synthesized input per-pid, so an agent can
drive a real app without stealing the user's focus. Every call here shells
``cua-driver call <tool> '<json>' --raw`` against the long-running daemon;
screenshots use ``--screenshot-out-file`` so image bytes never travel through
stdout.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

CALL_TIMEOUT_SECONDS = 30

# Windows whose smaller edge is under this (points) are helper surfaces —
# menu-bar strips, tooltips, overlays — not observation targets. An edge floor
# rather than an area floor: Calculator's untitled 2560x30 menu-bar strips
# have a large area but are only 30pt tall.
MIN_OBSERVABLE_EDGE = 100


class CuaCliError(RuntimeError):
    """cua-driver CLI invocation failed or returned an unusable payload."""


@dataclass
class CuaResult:
    text: str
    structured: dict | None
    is_error: bool


@dataclass
class CuaWindow:
    window_id: int
    title: str
    is_on_screen: bool
    on_current_space: bool
    width: float
    height: float
    z_index: int

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def min_edge(self) -> float:
        return min(self.width, self.height)


@dataclass
class CuaApp:
    pid: int
    name: str
    windows: list[CuaWindow]


def _run_cli(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["cua-driver", *args],
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise CuaCliError(
            "cua-driver is not installed or not on PATH. "
            "Install it from https://github.com/trycua/cua."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CuaCliError(f"cua-driver {' '.join(args[:2])} timed out") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise CuaCliError(detail[:500] or f"cua-driver exited {proc.returncode}")
    return proc.stdout


def cua_call(tool: str, args: dict | None = None) -> CuaResult:
    stdout = _run_cli(["call", tool, json.dumps(args or {}), "--raw"])
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise CuaCliError(f"cua-driver {tool} returned non-JSON: {stdout[:400]}") from e
    content = parsed.get("content") or []
    text = "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    structured = parsed.get("structuredContent")
    return CuaResult(
        text=text,
        structured=structured if isinstance(structured, dict) else None,
        is_error=parsed.get("isError") is True,
    )


def daemon_running() -> bool:
    try:
        return "daemon is running" in _run_cli(["status"])
    except CuaCliError:
        return False


def cua_screenshot(
    window_id: int, quality: int, max_long_side: int
) -> tuple[bytes, int, int]:
    """Capture one window as JPEG and return ``(bytes, width, height)``.

    When the native capture's long side exceeds ``max_long_side`` the image is
    downscaled (Pillow) so the bytes and dimensions always describe the same
    image — which is also cua-driver's click coordinate space (the driver caps
    its own get_window_state PNG at ``max_image_dimension``).
    """
    out_file = Path(tempfile.gettempdir()) / f"n2-cua-{uuid.uuid4().hex}.jpg"
    args = [
        "call",
        "screenshot",
        json.dumps({"window_id": window_id, "format": "jpeg", "quality": quality}),
        "--screenshot-out-file",
        str(out_file),
    ]
    try:
        try:
            _run_cli(args)
        except CuaCliError:
            # ScreenCaptureKit captures can fail transiently (stream races a
            # window-state change); one retry after a beat usually recovers.
            time.sleep(0.5)
            _run_cli(args)
        with Image.open(out_file) as image:
            image.load()
            width, height = image.size
            if max_long_side > 0 and max(width, height) > max_long_side:
                scale = max_long_side / max(width, height)
                width = max(1, round(width * scale))
                height = max(1, round(height * scale))
                image = image.resize((width, height), Image.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue(), width, height
    finally:
        out_file.unlink(missing_ok=True)


def _parse_windows(value: object) -> list[CuaWindow]:
    windows: list[CuaWindow] = []
    if not isinstance(value, list):
        return windows
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("window_id"), int):
            continue
        bounds = entry.get("bounds") if isinstance(entry.get("bounds"), dict) else {}
        windows.append(
            CuaWindow(
                window_id=entry["window_id"],
                title=entry.get("title") or "",
                is_on_screen=entry.get("is_on_screen") is True,
                on_current_space=entry.get("on_current_space") is True,
                width=float(bounds.get("width") or 0),
                height=float(bounds.get("height") or 0),
                z_index=int(entry.get("z_index") or 0),
            )
        )
    return windows


def launch_app(
    bundle_id: str | None = None,
    name: str | None = None,
    urls: list[str] | None = None,
) -> CuaApp:
    args: dict = {}
    if bundle_id:
        args["bundle_id"] = bundle_id
    elif name:
        args["name"] = name
    if urls:
        args["urls"] = urls
    result = cua_call("launch_app", args)
    if result.is_error or not result.structured:
        raise CuaCliError(f"launch_app failed: {result.text[:300]}")
    pid = result.structured.get("pid")
    if not isinstance(pid, int):
        raise CuaCliError(f"launch_app returned no pid: {result.text[:300]}")
    return CuaApp(
        pid=pid,
        name=result.structured.get("name") or "app",
        windows=_parse_windows(result.structured.get("windows")),
    )


def list_windows(pid: int) -> list[CuaWindow]:
    result = cua_call("list_windows", {"pid": pid})
    if result.is_error:
        raise CuaCliError(f"list_windows failed: {result.text[:300]}")
    return _parse_windows((result.structured or {}).get("windows"))


def pick_best_window(windows: list[CuaWindow]) -> CuaWindow | None:
    """Pick the window an agent should observe: the frontmost on-screen,
    current-Space window of meaningful size.

    Frontmost-first means a modal or dialog the app just opened becomes the
    observation target (a dialog is usually smaller than its parent, so a
    largest-area pick would miss it); the edge floor keeps helper strips out
    even when they outrank the real window in z-order.
    """
    if not windows:
        return None
    usable = [
        w
        for w in windows
        if w.is_on_screen and w.on_current_space and w.min_edge >= MIN_OBSERVABLE_EDGE
    ]
    if usable:
        return max(usable, key=lambda w: w.z_index)
    # Hidden-launched app (this tool's normal mode) or everything off-Space: no
    # window reports on-screen/on-current-space, so the primary filter is empty.
    # Still honor the edge floor here — otherwise menu-bar strips (Calculator's
    # 2560x30 helpers) can outrank the real window on area alone on large
    # displays — and keep the frontmost-first rule among the real windows.
    sized = [w for w in windows if w.min_edge >= MIN_OBSERVABLE_EDGE]
    if sized:
        return max(sized, key=lambda w: w.z_index)
    # Nothing clears the floor: fall back to the largest window anywhere so
    # there's still something to drive.
    return max(windows, key=lambda w: w.area)
