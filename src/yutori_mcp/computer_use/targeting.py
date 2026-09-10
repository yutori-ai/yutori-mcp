"""Foreground target-PID guard for app-scoped computer-use sessions."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from yutori.navigator.macos import FrontmostApp, MacOSComputer, MacOSFocusChangedError
from yutori.navigator.sandbox_tools import render_image_result


_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})


def _target_mismatch_message(
    tool: str,
    target_pid: int,
    current: FrontmostApp | None,
    *,
    target_name: str | None = None,
) -> str:
    target = (
        f"{target_name!r} (pid {target_pid})"
        if target_name
        else f"the requested app (pid {target_pid})"
    )
    if current is None:
        return f"{tool} was not sent: macOS could not verify that {target} is frontmost."
    return f"{tool} was not sent: {target} is the foreground target, but {current.describe()} is frontmost."


async def require_frontmost_target(
    computer: MacOSComputer,
    target_pid: int,
    *,
    tool: str,
    target_name: str | None = None,
) -> None:
    """Fail closed unless LaunchServices confirms the requested process is frontmost."""
    current = await computer._probe_frontmost()
    if current is not None and current.pid == target_pid:
        return
    raise MacOSFocusChangedError(
        _target_mismatch_message(tool, target_pid, current, target_name=target_name),
        computer.current_observation,
    )


class TargetGuardedMacOSComputer(MacOSComputer):
    """Apply the local harness's macOS-specific guards and file handling.

    The SDK's focus guard prevents a change *after* a screenshot. It deliberately accepts
    whichever app that screenshot observed. Once this repository assigns ``target_pid`` for
    an app-scoped foreground run, accepting a different baseline would send text or shortcuts
    to the wrong application, so this stricter guard fails closed instead.

    The SDK's generic n2 loop supports file handlers returning image content, but its macOS
    adapter in the pinned release still decodes every ``read`` target as UTF-8. Match the
    sandbox adapter's image behavior here so reading a local image returns a model-visible
    WebP instead of raising ``UnicodeDecodeError``.
    """

    async def read_file(
        self, file_path: str, offset: int = 1, limit: int = 2_000
    ) -> "str | dict[str, str]":
        self._require_local_shell()
        if offset < 1:
            raise ValueError("read.offset must be a positive 1-based line number")
        path = self._resolve_file_path(file_path)
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            return await super().read_file(file_path, offset=offset, limit=limit)

        data = await asyncio.to_thread(path.read_bytes)
        self._file_snapshots[path] = ""
        return render_image_result(file_path, data)

    async def _guard_frontmost(self, tool: str) -> None:
        if self.window_mode or not self.verify_focus or self.target_pid is None:
            await super()._guard_frontmost(tool)
            return

        current = await self._probe_frontmost()
        if current is not None and current.pid == self.target_pid:
            return

        self._focus_guard_trips += 1
        observation = None
        with suppress(Exception):
            observation = await self.screenshot()
        raise MacOSFocusChangedError(
            _target_mismatch_message(tool, self.target_pid, current),
            observation,
        )
