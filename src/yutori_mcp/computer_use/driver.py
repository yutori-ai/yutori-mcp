"""cua-driver transport and the desktop handler the agent loop drives.

The transport is `cua-driver call <tool> <json> --raw`, one subprocess per
call, pinned to the verified driver 0.19.x contract.
The handler exposes the `AsyncComputerHandler` surface the pinned `cua-agent`
loop dispatches to, plus the optional shell capability of the hybrid tool
sets, executed headless on the host with captured output.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

CLI_TIMEOUT_SECONDS = 30.0
CAPTURE_ATTEMPTS = 3
CAPTURE_RETRY_DELAY_SECONDS = 0.25
# macOS reports an action before the surface repaints, so a capture taken
# immediately after would show the pre-action screen.
SETTLE_AFTER_ACTION_SECONDS = 1.0
# launch_app deliberately does not foreground; without this pause the model is
# told the app is open while the first observation still shows whatever was in
# front, and it burns its budget hunting other windows.
FRONTING_SETTLE_SECONDS = 0.8
SCROLL_LINES_PER_MODEL_UNIT = 3
DRIVER_SCROLL_MAX_AMOUNT = 50

# Shell result presentation shared with the n2 playground executor: the wire
# contract bounds one shell result at 8,000 characters, and the cua adapter
# enforces the same cap, so staying within it here keeps the exit-code marker
# from being cut off by that second, defense-in-depth truncation.
SHELL_RESULT_MAX_CHARS = 8_000
SHELL_RESULT_TRUNCATION_SUFFIX = "\n[result truncated]"
SHELL_EMPTY_SUCCESS_OUTPUT = "Command exited with code 0 and produced no output."

_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

# Marks the final $PWD a `bash` command left behind, so the next call can start
# there. Long and namespaced because it is matched against the command's own
# combined output, where a short marker could plausibly occur naturally.
_BASH_CWD_SENTINEL = "__YUTORI_MCP_BASH_CWD__"

_KEY_NAME_MAP = {
    "enter": "return",
    "esc": "escape",
    "backspace": "delete",
    "page_up": "pageup",
    "page_down": "pagedown",
    "control": "ctrl",
    "alt": "option",
    "meta": "cmd",
    "command": "cmd",
    "super": "cmd",
}
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

TYPE_CHUNK_MAX_CHARS = 500
_SHELL_PROXY_PATH = Path(__file__).with_name("shell_proxy.py")


class DriverError(RuntimeError):
    """The driver process failed or returned an unusable payload."""


class DriverRefusal(DriverError):
    """The driver refused the call.

    The message always contains the word "refused" so the runner's action
    classification can distinguish a policy refusal from a transport failure
    without a type import.
    """


def normalize_key(value: str) -> str:
    key = value.lower()
    return _PUNCTUATION_MAP.get(key, _KEY_NAME_MAP.get(key, key))


def chunk_type_text(text: str, max_chars: int = TYPE_CHUNK_MAX_CHARS) -> list[str]:
    """Split text for type_text so no single call exceeds the driver's appetite.

    Prefers splitting at the last newline or space past the window midpoint so
    words survive intact; hard-cuts otherwise. Chunks concatenate back exactly.
    """
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(window.rfind("\n"), window.rfind(" "))
        if cut <= max_chars // 2:
            cut = max_chars
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _payload_ok(tool: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DriverError(f"cua-driver {tool} returned a non-object payload")
    refusal = payload.get("refusal")
    if payload.get("status") == "refused" or payload.get("effect") == "refused" or isinstance(refusal, dict):
        detail = ""
        if isinstance(refusal, dict):
            detail = str(refusal.get("code") or refusal.get("reason") or "")
        raise DriverRefusal(f"cua-driver refused {tool}: {detail or payload.get('status', 'refused')}")
    # Driver >=0.16 stamps an informational `code` on some successful payloads,
    # so a bare string code is a failure only when nothing else vouches for the
    # call having taken effect.
    code = payload.get("code")
    if isinstance(code, str) and code:
        accepted = (
            payload.get("request_accepted") is True
            and payload.get("status") != "partial"
            and payload.get("activated") is not False
        )
        if payload.get("activated") is not True and not accepted:
            raise DriverError(f"cua-driver {tool} failed: {code}")
    return payload


class DriverCLI:
    """One `cua-driver call` subprocess per tool call, JSON in argv, JSON out."""

    def __init__(self, driver_path: str | Path, capture_dir: Path | None = None):
        self.driver_path = str(driver_path)
        # The driver rejects symlinked ancestors (macOS $TMPDIR traverses
        # /var -> /private/var), so the capture directory is fully resolved.
        base = capture_dir or Path.home() / ".cache" / "yutori-mcp" / "captures"
        base.mkdir(parents=True, exist_ok=True)
        self.capture_dir = base.resolve()

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            self.driver_path,
            "call",
            tool,
            json.dumps(args, separators=(",", ":"), ensure_ascii=False),
            "--raw",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), CLI_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.TimeoutError):
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise DriverError(f"cua-driver {tool} timed out after {CLI_TIMEOUT_SECONDS:g} seconds") from None
        if process.returncode != 0:
            diagnostic = (
                stderr.decode(errors="replace").strip()
                or stdout.decode(errors="replace").strip()
                or f"exit status {process.returncode}"
            )
            raise DriverError(f"cua-driver {tool} failed: {diagnostic[:500]}")
        try:
            payload = json.loads(stdout.decode(errors="replace"))
        except json.JSONDecodeError as error:
            raise DriverError(f"cua-driver {tool} returned invalid JSON") from error
        return _payload_ok(tool, payload)

    async def capture(self, tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        """A screenshot-bearing call: payload plus the image bytes.

        Retries when the driver answers without a pixel frame (mid-repaint it
        reports px_frame_mismatch by omitting screenshot_width/height); a real
        refusal surfaces immediately from the payload check inside call().
        """
        last_error: DriverError | None = None
        for attempt in range(CAPTURE_ATTEMPTS):
            if attempt:
                await asyncio.sleep(CAPTURE_RETRY_DELAY_SECONDS)
            out_file = self.capture_dir / f"{uuid.uuid4()}.png"
            try:
                payload = await self.call(tool, {**args, "screenshot_out_file": str(out_file)})
                if not isinstance(payload.get("screenshot_width"), int) or not isinstance(
                    payload.get("screenshot_height"), int
                ):
                    last_error = DriverError(f"cua-driver {tool} returned no usable pixel frame")
                    continue
                try:
                    image = out_file.read_bytes()
                except OSError as error:
                    raise DriverError(f"cua-driver {tool} wrote no screenshot file") from error
                if not image:
                    last_error = DriverError(f"cua-driver {tool} wrote an empty image")
                    continue
                return payload, image
            finally:
                with suppress(OSError):
                    out_file.unlink()
        assert last_error is not None
        raise last_error


def _parse_windows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    windows = []
    for window in payload.get("windows") or []:
        if not isinstance(window, dict):
            continue
        bounds = window.get("bounds")
        if window.get("window_id") is None or not isinstance(bounds, dict):
            continue
        if not isinstance(bounds.get("width"), (int, float)) or not isinstance(bounds.get("height"), (int, float)):
            continue
        windows.append(window)
    return windows


def pick_best_window(windows: list[dict[str, Any]], min_edge_points: float = 100) -> dict[str, Any] | None:
    """Prefer an on-screen, current-space window with usable edges.

    The edge heuristic excludes helper surfaces such as Calculator's wide,
    shallow menu-bar strips; falls back to the largest window by area.
    """
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
    return max(
        windows,
        key=lambda window: window["bounds"]["width"] * window["bounds"]["height"],
    )


async def prepare_app(cli: DriverCLI, app: str, start_url: str | None) -> dict[str, Any]:
    """Launch and front the requested app before the driver session starts.

    Runs before start_session on purpose: launch_app and bring_to_front carry
    no session id, because a session id on any action implicitly declares that
    session with an immutable capture scope, and the later desktop-scope
    start_session would then be refused with session_policy_conflict.

    Returns {"name", "pid"} for the system context and liveness polling.
    Fronting failures are never fatal — the first observation is ground truth,
    and treating an unverified fronting as fatal once aborted a run at zero
    steps.
    """
    launch_args: dict[str, Any] = {}
    if start_url:
        launch_args["urls"] = [start_url]
    if _BUNDLE_ID_PATTERN.match(app):
        try:
            payload = await cli.call("launch_app", {"bundle_id": app, **launch_args})
        except DriverError:
            payload = await cli.call("launch_app", {"name": app, **launch_args})
    else:
        payload = await cli.call("launch_app", {"name": app, **launch_args})
    pid = payload.get("pid")
    if not isinstance(pid, int):
        raise DriverError(f"launch_app returned no pid for {app!r}")
    name = str(payload.get("name") or app)
    window = pick_best_window(_parse_windows(payload))
    try:
        if window is not None:
            await cli.call("bring_to_front", {"pid": pid, "window_id": window["window_id"]})
        else:
            await cli.call("bring_to_front", {"pid": pid})
    except DriverError:
        with suppress(DriverError):
            await cli.call("bring_to_front", {"pid": pid})
    await asyncio.sleep(FRONTING_SETTLE_SECONDS)
    return {"name": name, "pid": pid}


def format_shell_result(output: str, exit_code: int) -> str:
    """Combined output with an ``[exit code N]`` marker on nonzero exit."""
    if exit_code == 0 and not output:
        return SHELL_EMPTY_SUCCESS_OUTPUT
    marker = f"[exit code {exit_code}]" if exit_code != 0 else ""
    budget = SHELL_RESULT_MAX_CHARS - (len(marker) + 1 if marker else 0)
    if len(output) > budget:
        output = output[: budget - len(SHELL_RESULT_TRUNCATION_SUFFIX)] + SHELL_RESULT_TRUNCATION_SUFFIX
    if not marker:
        return output
    if not output:
        return marker
    separator = "" if output.endswith("\n") else "\n"
    return f"{output}{separator}{marker}"


def bash_cwd_wrapper(command: str, sentinel: str) -> str:
    """Wrap a ``bash`` command so it reports the directory it finished in.

    The tool's contract is that the working directory persists across calls
    while env vars and functions do not. Every call is a fresh process, so the
    wrapper prints the final ``$PWD`` on a sentinel line and re-raises the
    command's own exit code as the shell's.
    """
    return f"{command}\n__yutori_rc=$?\nprintf '\\n{sentinel}%s' \"$PWD\"\nexit $__yutori_rc\n"


def split_bash_cwd(text: str, sentinel: str) -> tuple[str, str | None]:
    output, marker, reported = text.rpartition(f"\n{sentinel}")
    if not marker:
        return text, None
    return output, reported or None


async def _stop_shell_proxy(process: asyncio.subprocess.Process) -> None:
    """Ask the proxy to drain its private Bash session before it exits."""
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=3)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise RuntimeError("Could not verify that the Bash process session stopped.") from None


class DesktopTimings:
    """Accumulated capture and settle time, read by the runner's perf report.

    Kept on the handler because these two phases happen here: the capture RPC
    is this class's ``screenshot`` and the post-action settle is ``_settle``.
    The runner subtracts both from each tool-call span so its per-phase
    breakdown (model / actions / screenshots / settle) sums without double
    counting — the same decomposition the playground's StepTimings uses.
    """

    def __init__(self) -> None:
        self.capture_ms = 0
        self.captures = 0
        self.settle_ms = 0


class CuaDriverDesktop:
    """Full-display, native-pixel macOS handler backed by cua-driver.

    GUI actions go through the driver at desktop scope. The optional shell
    capabilities execute directly on the host through bash with captured
    output.
    """

    def __init__(self, cli: DriverCLI, *, session: str | None = None):
        self.cli = cli
        # The driver renders this name verbatim in the agent cursor's badge
        # (sanitized, 28-char cap) and derives the cursor's fill color from it,
        # so it is chosen for the person watching the desktop: a brand name
        # plus a short suffix that keeps concurrent or crashed-run sessions
        # distinguishable without reading like an opaque machine id.
        self.session = session or f"Yutori Navigator · {uuid.uuid4().hex[:4]}"
        self.timings = DesktopTimings()
        self._native_size: tuple[int, int] | None = None
        self._bash_cwd = os.path.expanduser("~")
        self._background_tasks: dict[str, asyncio.subprocess.Process] = {}

    async def __aenter__(self) -> CuaDriverDesktop:
        await self.cli.call("start_session", {"session": self.session, "capture_scope": "desktop"})
        return self

    async def __aexit__(self, exc_type, _exc, _traceback) -> None:
        cleanup_error: BaseException | None = None
        try:
            await self._terminate_background_tasks()
        except BaseException as error:
            cleanup_error = error
        try:
            await self.cli.call("end_session", {"session": self.session})
        except DriverError:
            # When a run error is already unwinding, secondary session-teardown
            # noise must not replace it; on a clean exit it still surfaces.
            if exc_type is None and cleanup_error is None:
                raise
        if cleanup_error is not None and exc_type is None:
            raise cleanup_error

    def _routed(self, **arguments: Any) -> dict[str, Any]:
        return {
            "delivery_mode": "foreground",
            "scope": "desktop",
            "session": self.session,
            **arguments,
        }

    async def _settle(self) -> None:
        await asyncio.sleep(SETTLE_AFTER_ACTION_SECONDS)
        self.timings.settle_ms += int(SETTLE_AFTER_ACTION_SECONDS * 1000)

    async def _act(self, tool: str, args: dict[str, Any]) -> None:
        await self.cli.call(tool, args)
        await self._settle()

    # --- AsyncComputerHandler surface -------------------------------------

    async def get_environment(self) -> Literal["mac"]:
        return "mac"

    async def get_dimensions(self) -> tuple[int, int]:
        if self._native_size is None:
            await self.screenshot()
        assert self._native_size is not None
        return self._native_size

    async def screenshot(self, text: str | None = None) -> str:
        del text
        start = time.monotonic()
        payload, image = await self.cli.capture("get_desktop_state", {"session": self.session})
        self._native_size = (payload["screenshot_width"], payload["screenshot_height"])
        encoded = base64.b64encode(image).decode()
        self.timings.capture_ms += int((time.monotonic() - start) * 1000)
        self.timings.captures += 1
        return encoded

    async def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        modifier: list[str] | None = None,
    ) -> None:
        arguments = self._routed(x=x, y=y, count=1, button=button)
        if modifier:
            arguments["modifier"] = [normalize_key(key) for key in modifier]
        await self._act("click", arguments)

    async def double_click(self, x: int, y: int, modifier: list[str] | None = None) -> None:
        arguments = self._routed(x=x, y=y, count=2, button="left")
        if modifier:
            arguments["modifier"] = [normalize_key(key) for key in modifier]
        await self._act("click", arguments)

    async def triple_click(self, x: int, y: int, modifier: list[str] | None = None) -> None:
        arguments = self._routed(x=x, y=y, count=3, button="left")
        if modifier:
            arguments["modifier"] = [normalize_key(key) for key in modifier]
        await self._act("click", arguments)

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        if scroll_x == 0 and scroll_y == 0:
            return
        width, height = await self.get_dimensions()
        horizontal = abs(scroll_x) > abs(scroll_y)
        delta = scroll_x if horizontal else scroll_y
        direction = ("right" if delta > 0 else "left") if horizontal else ("down" if delta > 0 else "up")
        dimension = width if horizontal else height
        # Recover the model's 1-50 amount from the loop's pixel conversion,
        # then triple it: the trained macOS executor sends amount * 3 wheel
        # notches. cua-driver caps each request at 50, so preserve the total
        # across bounded requests and settle once for the model action.
        amount = max(1, min(50, round(abs(delta) / max(1, dimension * 0.1))))
        remaining = amount * SCROLL_LINES_PER_MODEL_UNIT
        dispatched = False
        try:
            while remaining:
                chunk = min(remaining, DRIVER_SCROLL_MAX_AMOUNT)
                await self.cli.call(
                    "scroll",
                    self._routed(x=x, y=y, direction=direction, amount=chunk, by="line"),
                )
                dispatched = True
                remaining -= chunk
        finally:
            if dispatched:
                await self._settle()

    async def type(self, text: str) -> None:
        for chunk in chunk_type_text(text):
            # delay_ms 0 because the CGEvent fallback otherwise waits 30ms/char.
            await self.cli.call("type_text", self._routed(text=chunk, delay_ms=0))
        await self._settle()

    async def wait(self, ms: int = 1000) -> None:
        await asyncio.sleep(ms / 1000)

    async def move(self, x: int, y: int) -> None:
        await self._act(
            "move_cursor",
            {"scope": "desktop", "session": self.session, "x": x, "y": y},
        )

    async def keypress(self, keys: list[str] | str) -> None:
        sequence = [keys] if isinstance(keys, str) else list(keys)
        normalized = [normalize_key(key) for key in sequence]
        if len(normalized) == 1:
            await self._act("press_key", self._routed(key=normalized[0]))
        else:
            await self._act("hotkey", self._routed(keys=normalized))

    async def drag(self, path: list[dict[str, int]]) -> None:
        if len(path) < 2:
            raise ValueError("drag path must contain at least two points")
        start, end = path[0], path[-1]
        await self._act(
            "drag",
            self._routed(from_x=start["x"], from_y=start["y"], to_x=end["x"], to_y=end["y"]),
        )

    async def get_current_url(self) -> str:
        return ""

    async def left_mouse_down(self, x: int | None = None, y: int | None = None) -> None:
        # Present because the loop's runtime-checkable handler protocol
        # requires the member; n2 uses the atomic drag action instead.
        del x, y
        raise NotImplementedError("n2 uses the atomic drag action")

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        del x, y
        raise NotImplementedError("n2 uses the atomic drag action")

    # --- Optional shell capabilities ---------------------------------------

    async def run_shell_command(self, command: str, cwd: str | None = None, timeout_seconds: int = 10) -> str:
        """Execute one validated ``shell_command`` on the real host.

        The loop has already validated the arguments and clamped
        ``timeout_seconds`` to [1, 30]. On timeout its command process tree is
        killed and a TimeoutError is raised, which the loop converts into a
        recoverable ``[ERROR] shell_command failed: ...`` tool result.
        """
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(_SHELL_PROXY_PATH),
            command,
            "foreground",
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError) as error:
            await _stop_shell_proxy(process)
            raise TimeoutError(f"command was killed after exceeding its {timeout_seconds}-second timeout") from error
        except BaseException:
            await _stop_shell_proxy(process)
            raise
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return format_shell_result(output, int(process.returncode or 0))

    async def run_bash_command(self, command: str, timeout: float = 120.0, run_in_background: bool = False) -> str:
        """Execute one validated ``bash`` call on the real host.

        A different contract from ``shell_command``: the loop bounds ``timeout``
        to [0, 600], there is no per-call ``cwd`` because the working directory
        persists across calls, and ``run_in_background`` starts a task tracked
        until run teardown.
        """
        if run_in_background:
            return await self._run_bash_in_background(command)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(_SHELL_PROXY_PATH),
            bash_cwd_wrapper(command, _BASH_CWD_SENTINEL),
            "foreground",
            cwd=self._bash_cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            if timeout == 0:
                stdout, _ = await process.communicate()
            else:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as error:
            await _stop_shell_proxy(process)
            raise TimeoutError(f"command was killed after exceeding its {timeout:g}-second timeout") from error
        except BaseException:
            await _stop_shell_proxy(process)
            raise
        text = stdout.decode("utf-8", errors="replace") if stdout else ""
        output, reported_cwd = split_bash_cwd(text, _BASH_CWD_SENTINEL)
        # Absent on a killed command, in which case the previous cwd stands.
        self._bash_cwd = reported_cwd or self._bash_cwd
        return format_shell_result(output, int(process.returncode or 0))

    async def _run_bash_in_background(self, command: str) -> str:
        task_id = f"bash-{uuid.uuid4().hex[:8]}"
        output_path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"yutori-mcp-{task_id}.log")
        # Opened here rather than in the shell so a redirect failure surfaces
        # as a recoverable tool error instead of a silently empty log.
        log_file = open(output_path, "wb")
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(_SHELL_PROXY_PATH),
                command,
                "background",
                cwd=self._bash_cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
        except BaseException:
            if process is not None:
                await _stop_shell_proxy(process)
            raise
        finally:
            # The child holds its own dup of the descriptor.
            log_file.close()
        assert process is not None
        self._background_tasks[task_id] = process
        return (
            f"Started background task {task_id} (pid {process.pid}).\n"
            f"Output file: {output_path}\n"
            "The task will be stopped automatically when this computer-use run ends."
        )

    async def _terminate_background_tasks(self) -> None:
        """Kill every still-running background task at teardown.

        A background command that survives the run would keep acting on the
        user's real machine after the tool reported done — the same failure
        the foreground timeout path already guards against.
        """
        errors: list[BaseException] = []
        try:
            for process in reversed(self._background_tasks.values()):
                try:
                    await _stop_shell_proxy(process)
                except BaseException as error:
                    errors.append(error)
        finally:
            self._background_tasks.clear()
        if errors:
            raise errors[0]
