from __future__ import annotations

import os
import shutil
import sys
from typing import Any, TextIO

from .constants import DELIVERY_MODE_BACKGROUND, DELIVERY_MODE_FOREGROUND

REDACTED = "[REDACTED]"


def redact(text: str, secret: str) -> str:
    """Scrub ``secret`` out of ``text`` before it can reach the protocol stream or logs.

    Shared by the runner (exception text) and the supervisor (stderr diagnostics and
    protocol stdout lines) so the child's API key never survives past this one point.
    """
    return text.replace(secret, REDACTED)


def _seconds(ms: Any) -> str:
    return f"{ms / 1000:.1f}s" if isinstance(ms, (int, float)) else "?"


def _phase(label: str, total_ms: Any, count: Any, unit: str) -> str:
    text = f"{label} {_seconds(total_ms)}"
    if isinstance(count, int) and count > 0:
        text += f" over {count} {unit}"
        if isinstance(total_ms, (int, float)):
            text += f" ({total_ms / count / 1000:.1f}s avg)"
    return text


def format_perf(result: dict[str, Any]) -> list[str]:
    """Render the phase breakdown when the runner supplies one."""
    elapsed_ms = result.get("elapsed_ms")
    steps = result.get("steps")
    if not isinstance(elapsed_ms, (int, float)) or not isinstance(steps, int):
        return []
    headline = f"Perf: total {_seconds(elapsed_ms)} over {steps} model turns"
    if steps > 0:
        headline += f" ({elapsed_ms / steps / 1000:.1f}s/turn)"
    lines = [headline]
    timings = result.get("timings")
    if isinstance(timings, dict):
        lines.append(
            "  "
            + " | ".join(
                [
                    _phase(
                        "model",
                        timings.get("model_ms"),
                        timings.get("model_calls"),
                        "calls",
                    ),
                    _phase(
                        "actions",
                        timings.get("action_ms"),
                        timings.get("tool_calls"),
                        "tool calls",
                    ),
                    _phase(
                        "screenshots",
                        timings.get("screenshot_ms"),
                        timings.get("screenshots"),
                        "captures",
                    ),
                    f"settle {_seconds(timings.get('settle_ms'))}",
                    f"polling {_seconds(timings.get('polling_ms'))}",
                    f"shell {_seconds(timings.get('shell_ms'))}",
                    f"other {_seconds(timings.get('other_ms'))}",
                ]
            )
        )
    return lines


def describe_delivery_surface(mode: str, app: str | None) -> str:
    """What a "ready" event is driving, phrased for the CLI and MCP progress renderers.

    Both callers announce this identically ("driving the desktop" vs. "driving the {app}
    window in the background") when a run starts, so the phrase lives here once rather than
    as two independently-typed string literals that could drift.
    """
    return f"the {app} window in the background" if mode == DELIVERY_MODE_BACKGROUND else "the desktop"


def format_action_line(event: dict[str, Any], *, index_default: Any = None) -> str:
    """The "action #N: tool -> status" prefix shared by the CLI and MCP progress renderers.

    Only the part that is byte-identical between the two callers lives here; each caller
    appends its own elapsed-time and command formatting, which intentionally differ (a
    multi-line indented command preview for a terminal vs. a single-line MCP log message).
    """
    index = event.get("index", index_default)
    line = f"action #{index}: {event.get('tool')} -> {event.get('status')}"
    if event.get("refusal_code"):
        line += f" ({event['refusal_code']})"
    if event.get("escalated"):
        line += " [fronted]"
    if event.get("duration_ms") is not None:
        line += f" took {event['duration_ms']} ms"
    return line


_ANSI_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
}
# Each glyph as (preferred, ASCII fallback), so a stream whose encoding cannot carry the
# preferred form degrades to plain text instead of raising UnicodeEncodeError at the very
# end of a run that already did its work.
_GLYPHS = {
    "check": ("\u2713", "v"),
    "cross": ("\u2717", "x"),
    "tilde": ("~", "~"),
    "dash": ("\u2014", "-"),
    "clock": ("\u23f1", "!"),
    "warn": ("\u26a0", "!"),
    "bullet": ("\u25cf", "*"),
    "separator": ("\u00b7", "|"),
    "rule": ("\u2500", "-"),
    "detail": ("\u21b3", ">"),
    "prompt": ("$", "$"),
}
# Every action status `runner._status_for` can emit, plus the "interrupted" one
# `ActionReporter.flush_interrupted` emits for a call cut short by a stop.
_ACTION_STYLES = {
    "executed": ("check", "green"),
    "refused": ("cross", "red"),
    "uncertain": ("tilde", "yellow"),
    "interrupted": ("dash", "yellow"),
}
# Every outcome `runner._cancelled_outcome`, `runner.run_request`, and `terminal_result` produce.
_OUTCOME_STYLES = {
    "completed": ("check", "green"),
    "limit": ("clock", "yellow"),
    "aborted": ("dash", "yellow"),
    "target_crashed": ("cross", "red"),
    "failed": ("cross", "red"),
}
FINAL_OUTPUT_HEADING = "FINAL OUTPUT"
_MAX_RULE_WIDTH = 88
_LABEL_WIDTH = 10
_DETAIL_INDENT = "     "


def supports_color(stream: TextIO | None = None) -> bool:
    """Whether ANSI styling is safe to write to ``stream``.

    NO_COLOR and FORCE_COLOR come first so a piped run (`| tee`, CI logs) stays plain
    text, and a deliberately colored one stays colored, whatever the TTY check says.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    stream = sys.stdout if stream is None else stream
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        # A closed or non-file-like stdout is not a terminal, and never a reason to fail a run.
        return False


def supports_glyphs(stream: TextIO | None = None) -> bool:
    """Whether ``stream``'s encoding can carry the non-ASCII glyphs in ``_GLYPHS``."""
    stream = sys.stdout if stream is None else stream
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(preferred for preferred, _ in _GLYPHS.values()).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


class Terminal:
    """How one output stream renders: ANSI styles on or off, rich glyphs or ASCII.

    Callable, so painting reads as ``paint(text, "green")`` at the call sites. Painted
    spans are never nested: the trailing reset would end the outer style early.
    """

    def __init__(self, *, color: bool = False, glyphs: bool = True) -> None:
        self.color = color
        self.glyphs = glyphs

    @classmethod
    def detect(cls, stream: TextIO | None = None) -> "Terminal":
        """The capabilities of ``stream`` (stdout by default)."""
        return cls(color=supports_color(stream), glyphs=supports_glyphs(stream))

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles or not text:
            return text
        codes = ";".join(_ANSI_CODES[style] for style in styles)
        return f"\033[{codes}m{text}\033[0m"

    def glyph(self, name: str) -> str:
        preferred, fallback = _GLYPHS[name]
        return preferred if self.glyphs else fallback

    def width(self) -> int:
        return min(_MAX_RULE_WIDTH, max(24, shutil.get_terminal_size((80, 24)).columns))

    def rule(self, label: str | None = None) -> str:
        """A full-width horizontal rule, optionally naming the block it opens."""
        bar = self.glyph("rule")
        if not label:
            return self(bar * self.width(), "dim")
        head = f"{bar * 2} {label} "
        return self(head, "bold") + self(bar * max(0, self.width() - len(head)), "dim")

    def row(self, label: str, value: str) -> str:
        """One ``label   value`` metadata row, aligned under the run's headline."""
        return "  " + self(label.ljust(_LABEL_WIDTH), "dim") + value


def _clock(ms: Any) -> str:
    """A duration scaled for reading: milliseconds under a second, minutes past sixty."""
    if not isinstance(ms, (int, float)):
        return "?"
    if ms < 1000:
        return f"{ms:.0f}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {seconds % 60:04.1f}s"


def format_terminal_action(event: dict[str, Any], paint: Terminal) -> list[str]:
    """One action as a status line plus its indented detail lines.

    The detail lines carry what a flat status line cannot: the members of a
    `computer_batch` and the command of a shell call. Deliberately separate from
    :func:`format_action_line`, which stays one plain line for MCP progress
    notifications, where a host renders each message on its own.
    """
    status = str(event.get("status") or "")
    glyph, color = _ACTION_STYLES.get(status, ("bullet", "yellow"))
    line = f"{paint(paint.glyph(glyph), color, 'bold')} {paint('#' + str(event.get('index', 0)), 'dim')} "
    line += paint(str(event.get("tool") or "unknown"), "cyan", "bold")
    if status not in {"", "executed"}:
        line += " " + paint(status, color)
    if event.get("refusal_code"):
        line += " " + paint(f"({event['refusal_code']})", color)
    if event.get("escalated"):
        line += " " + paint("[fronted]", "yellow")
    if event.get("run_in_background"):
        line += " " + paint("[background]", "yellow")
    timings = [_clock(event["duration_ms"])] if event.get("duration_ms") is not None else []
    if event.get("elapsed_ms") is not None:
        timings.append(f"at {_clock(event['elapsed_ms'])}")
    if timings:
        line += "  " + paint(f" {paint.glyph('separator')} ".join(timings), "dim")
    lines = [line]
    for detail in event.get("details") or []:
        lines.append(f"{_DETAIL_INDENT}{paint(paint.glyph('detail'), 'dim')} {paint(str(detail), 'dim')}")
    if event.get("command"):
        lines.append(f"{_DETAIL_INDENT}{paint(paint.glyph('prompt'), 'dim')} {event['command']}")
    return lines


def format_terminal_result(
    result: dict[str, Any],
    paint: Terminal,
    *,
    include_actions: bool = False,
) -> str:
    """A finished run for a terminal: the model's answer first, then the run's facts.

    The answer is what the operator ran the command for, so it leads, inside a labeled
    block that separates it from the progress lines above and the metadata below.
    ``include_actions`` is for callers that streamed nothing while the run was going
    (`smoke`); `run` leaves it off, since its actions already scrolled past live.
    """
    lines: list[str] = []
    if include_actions and (actions := result.get("actions")):
        for action in actions:
            lines.extend(format_terminal_action(action, paint))
        lines.append("")
    if result.get("final_text"):
        lines.extend(["", paint.rule(FINAL_OUTPUT_HEADING), str(result["final_text"]).strip(), paint.rule(), ""])
    outcome = str(result.get("outcome") or "failed")
    glyph, color = _OUTCOME_STYLES.get(outcome, ("bullet", "yellow"))
    facts = []
    if result.get("elapsed_ms") is not None:
        facts.append(_clock(result["elapsed_ms"]))
    if isinstance(result.get("steps"), int):
        facts.append(f"{result['steps']} model turns")
    facts.append(str(result.get("delivery_mode") or DELIVERY_MODE_FOREGROUND))
    separator = f"  {paint.glyph('separator')}  "
    lines.append(
        f"{paint(paint.glyph(glyph), color, 'bold')} "
        + paint(outcome, color, "bold")
        + paint(separator + separator.join(facts), "dim")
    )
    if result.get("run_url"):
        lines.append(paint.row("run", str(result["run_url"])))
    if (window_target := _window_target(result.get("window_target"))) is not None:
        lines.append(paint.row("window", window_target))
    if (state := _presentation_state(result)) is not None:
        surface = "menu bar" if result.get("delivery_mode") == DELIVERY_MODE_BACKGROUND else "overlay"
        lines.append(paint.row(surface, state))
    if (delivery := _delivery_counts(result)) is not None:
        lines.append(paint.row("delivery", delivery))
    if result.get("preview_frames"):
        lines.append(paint.row("activity", f"{result['preview_frames']} frame(s) streamed while the window was open"))
    perf = format_perf(result)
    if perf:
        lines.append(paint.row("perf", perf[0].removeprefix("Perf: ")))
        lines.extend(paint.row("", paint(line.strip(), "dim")) for line in perf[1:])
    return "\n".join(lines)


def _window_target(target: Any) -> str | None:
    """The background window a run was scoped to, or None when it drove the desktop."""
    if not isinstance(target, dict) or target.get("pid") is None:
        return None
    label = target.get("app_name") or target.get("title") or "window"
    return f"{label} (pid {target['pid']}, window {target.get('window_id')})"


def _presentation_state(result: dict[str, Any]) -> str | None:
    """Whether the requested presentation surface came up, or None if none was asked for."""
    if not result.get("reasoning_overlay_requested"):
        return None
    state = "active" if result.get("reasoning_overlay_effective") else "unavailable"
    return f"{state}; codec: {result.get('codec') or 'unknown'}"


def _delivery_counts(result: dict[str, Any]) -> str | None:
    """How often background delivery had to escalate or gave up; None when uninteresting.

    Always reported for a background run, even at zero -- that a run needed no
    escalation is the result the operator is looking for.
    """
    escalations = result.get("fallback_escalations") or 0
    skips = result.get("fallback_skips") or 0
    refusals = result.get("background_refusals") or 0
    if not (result.get("delivery_mode") == DELIVERY_MODE_BACKGROUND or escalations or skips or refusals):
        return None
    line = f"{escalations} foreground escalation(s), {refusals} background refusal(s)"
    if skips:
        # Foreground retries the SDK withheld because the window had already changed.
        line += f", {skips} retry(ies) skipped after the window changed"
    return line


def format_result(result: dict[str, Any]) -> str:
    lines = [
        f"Outcome: {result.get('outcome', 'failed')}",
        f"Delivery mode: {result.get('delivery_mode', DELIVERY_MODE_FOREGROUND)}",
    ]
    if result.get("run_url"):
        lines.append(f"Run: {result['run_url']}")
    if (window_target := _window_target(result.get("window_target"))) is not None:
        lines.append(f"Window target: {window_target}")
    if result.get("final_text"):
        lines.append(f"Final text: {result['final_text']}")
    if result.get("elapsed_ms") is not None:
        lines.append(f"Elapsed: {result['elapsed_ms']} ms")
    if (state := _presentation_state(result)) is not None:
        # Background runs show a menu bar item (latest frame + Stop) instead of the overlay.
        # Either way the run also offers the activity window, opened from that menu.
        surface = "Menu bar status" if result.get("delivery_mode") == DELIVERY_MODE_BACKGROUND else "Reasoning overlay"
        lines.append(f"{surface}: {state}")
    if (delivery := _delivery_counts(result)) is not None:
        lines.append(f"Delivery: {delivery}")
    if result.get("preview_frames"):
        lines.append(f"Activity window: {result['preview_frames']} frame(s) streamed while it was open")
    lines.extend(format_perf(result))
    actions = result.get("actions", [])
    if actions:
        lines.append("Actions:")
        for action in actions:
            line = (
                "- #{index} {tool}: {status} (raw: {raw_status}; mode: {delivery_mode}; "
                "route: {route}; refusal: {refusal_code})".format(**action)
            )
            if action.get("effect"):
                line += f"; effect: {action['effect']}"
            if action.get("escalated"):
                line += " [fronted]"
            if action.get("duration_ms") is not None:
                line += f" took {action['duration_ms']} ms"
            if action.get("command"):
                line += f" $ {action['command']}"
            lines.append(line)
    return "\n".join(lines)


def terminal_result(
    outcome: str,
    message: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    delivery_mode: str = DELIVERY_MODE_FOREGROUND,
) -> dict[str, Any]:
    """Build the supervisor-side terminal result dict every caller returns to the host.

    Single source of truth for this shape. The supervisor synthesizes one of these
    whenever it has to conclude a run itself rather than relay the runner's own
    terminal event — a failed protocol exchange (``failure`` below), an expired
    deadline (``limit``), or a cancelled task (``aborted``) — and all three carried
    hand-written copies of the same four keys, so a shape change had to be made in
    three places at once.
    """
    return {
        "outcome": outcome,
        "delivery_mode": delivery_mode,
        "final_text": message,
        "actions": actions or [],
    }


def failure(
    message: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    delivery_mode: str = DELIVERY_MODE_FOREGROUND,
) -> dict[str, Any]:
    """The terminal result for a run that could not be carried out."""
    return terminal_result("failed", message, actions=actions, delivery_mode=delivery_mode)
