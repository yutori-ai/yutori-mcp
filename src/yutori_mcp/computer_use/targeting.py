"""Foreground target-PID guard for app-scoped computer-use sessions."""

from __future__ import annotations

from contextlib import suppress

from yutori.navigator.macos import FrontmostApp, MacOSComputer, MacOSFocusChangedError


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
    """Require an app-scoped desktop session's target PID before sending keyboard input.

    The SDK's focus guard prevents a change *after* a screenshot. It deliberately accepts
    whichever app that screenshot observed. Once this repository assigns ``target_pid`` for
    an app-scoped foreground run, accepting a different baseline would send text or shortcuts
    to the wrong application, so this stricter guard fails closed instead.
    """

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
