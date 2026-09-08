#!/usr/bin/env python3
"""Deterministic end-to-end input checks for Yutori's local macOS driver."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from yutori.navigator.macos import MacOSFocusChangedError, MacOSWindowTarget  # noqa: E402
from yutori.navigator.n2_actions import translate_n2_action  # noqa: E402

from yutori_mcp.computer_use.app import prepare_app  # noqa: E402
from yutori_mcp.computer_use.preflight import child_search_path  # noqa: E402
from yutori_mcp.computer_use.targeting import (  # noqa: E402
    TargetGuardedMacOSComputer as MacOSComputer,
    require_frontmost_target,
)

BUNDLE_ID = "ai.yutori.input-probe"
EXECUTABLE_NAME = "YutoriInputProbe"
DEFAULT_APP = REPOSITORY / "tools" / "YutoriInputProbe" / ".build" / "YutoriInputProbe.app"
INPUT_EVENT_CATEGORIES = {"nsevent", "text", "command", "control", "responder"}


@dataclass
class CaseResult:
    name: str
    passed: bool
    expected: str
    error: str | None
    received_events: list[dict[str, Any]]
    delivery: dict[str, Any] | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send exact input actions to Yutori Input Probe and correlate them with its AppKit log."
    )
    parser.add_argument("--mode", choices=("foreground", "background", "both"), default="both")
    parser.add_argument(
        "--allow-foreground-fallback",
        action="store_true",
        help="Allow background actions to front the probe briefly when the driver cannot deliver them.",
    )
    parser.add_argument("--app", type=Path, default=DEFAULT_APP, help="Path to YutoriInputProbe.app")
    parser.add_argument("--keep-open", action="store_true", help="Leave the probe running after the test.")
    return parser.parse_args()


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


async def wait_for_events(
    path: Path,
    predicate: Callable[[list[dict[str, Any]]], bool],
    *,
    timeout: float = 4,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    latest: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        latest = read_events(path)
        if predicate(latest):
            return latest
        await asyncio.sleep(0.05)
    return latest


def event_sequence(event: dict[str, Any]) -> int:
    value = event.get("sequence")
    return value if isinstance(value, int) else -1


def details(event: dict[str, Any]) -> dict[str, str]:
    value = event.get("details")
    return value if isinstance(value, dict) else {}


def delivery_dict(computer: MacOSComputer, starting_count: int) -> dict[str, Any] | None:
    outcomes = tuple(computer.action_outcomes)[starting_count:]
    if not outcomes:
        return None
    latest = asdict(outcomes[-1])
    latest["escalated_in_case"] = any(outcome.escalated for outcome in outcomes)
    return latest


def is_clean_refusal(
    error: str | None,
    events: list[dict[str, Any]],
    delivery: dict[str, Any] | None,
) -> bool:
    received_input = any(event.get("category") in INPUT_EVENT_CATEGORIES for event in events)
    return bool(
        error
        and not received_input
        and delivery
        and (delivery.get("recommended") == "foreground" or delivery.get("effect") == "refused")
    )


def target_center(events: list[dict[str, Any]], target: str, capture_size: tuple[int, int]) -> tuple[int, int]:
    frame = next(
        (
            event
            for event in reversed(events)
            if event.get("category") == "layout"
            and event.get("name") == "targetFrame"
            and details(event).get("target") == target
        ),
        None,
    )
    if frame is None:
        raise RuntimeError(f"The probe did not report geometry for {target!r}.")
    values = details(frame)
    window_width = float(values["windowWidth"])
    window_height = float(values["windowHeight"])
    scale_x = capture_size[0] / window_width
    scale_y = capture_size[1] / window_height
    return (
        round((float(values["windowX"]) + float(values["width"]) / 2) * scale_x),
        round((float(values["windowY"]) + float(values["height"]) / 2) * scale_y),
    )


async def run_case(
    computer: MacOSComputer,
    log_path: Path,
    name: str,
    expected: str,
    action: Callable[[], Any],
    matches: Callable[[list[dict[str, Any]]], bool],
    *,
    allow_explicit_refusal: bool = False,
) -> CaseResult:
    before = read_events(log_path)
    starting_sequence = max((event_sequence(event) for event in before), default=-1)
    starting_outcomes = len(computer.action_outcomes)
    error: str | None = None
    try:
        await action()
    except MacOSFocusChangedError:
        raise
    except Exception as exc:  # the report must preserve driver refusals verbatim
        error = f"{type(exc).__name__}: {exc}"
    events = await wait_for_events(
        log_path,
        lambda values: matches([event for event in values if event_sequence(event) > starting_sequence]),
        timeout=1.5,
    )
    received = [event for event in events if event_sequence(event) > starting_sequence]
    delivery = delivery_dict(computer, starting_outcomes)
    clean_refusal = allow_explicit_refusal and is_clean_refusal(error, received, delivery)
    return CaseResult(
        name=name,
        passed=(error is None and matches(received)) or clean_refusal,
        expected=expected,
        error=error,
        received_events=received,
        delivery=delivery,
    )


async def dispatch_n2(computer: MacOSComputer, action: str, arguments: dict[str, Any], size: tuple[int, int]) -> None:
    for translated in translate_n2_action(action, arguments, *size):
        translated = dict(translated)
        method_name = translated.pop("type")
        method = getattr(computer, method_name)
        await method(**translated)


def has_event(events: list[dict[str, Any]], category: str, name: str | None = None) -> bool:
    return any(
        event.get("category") == category and (name is None or event.get("name") == name)
        for event in events
    )


def has_text(events: list[dict[str, Any]], text: str) -> bool:
    return any(
        event.get("category") == "text" and text in details(event).get("value", "")
        for event in events
    )


def stayed_in_background(events: list[dict[str, Any]]) -> bool:
    meaningful = [event for event in events if event.get("category") in {"nsevent", "text", "command", "control"}]
    return bool(meaningful) and all(event.get("state", {}).get("appActive") is False for event in meaningful)


async def run_mode(mode: str, log_path: Path, allow_fallback: bool) -> tuple[dict[str, Any], int]:
    background = mode == "background"
    computer = MacOSComputer(
        presentation=False,
        scope="window" if background else "desktop",
        allow_foreground_fallback=allow_fallback if background else False,
    )
    cases: list[CaseResult] = []
    target_pid = 0
    requires_background_receipt = background and not allow_fallback

    async with computer:
        if not background:
            await computer.screenshot()
        target = await prepare_app(computer, BUNDLE_ID, None, front=not background)
        target_pid = int(target["pid"])
        computer.target_pid = target_pid
        if background:
            window_id = target.get("window_id")
            if not isinstance(window_id, int):
                raise RuntimeError("Yutori Input Probe has no targetable window.")
            await computer.set_window_target(
                MacOSWindowTarget(target_pid, window_id, app_name="Yutori Input Probe")
            )
        await computer.screenshot()
        if not background:
            await require_frontmost_target(
                computer,
                target_pid,
                tool="foreground probe matrix",
                target_name="Yutori Input Probe",
            )
        size = await computer.get_dimensions()

        marker = f"{mode}-Aa0-é-中-🙂"
        cases.append(
            await run_case(
                computer,
                log_path,
                "type ASCII and Unicode",
                f"The raw key sink receives {marker!r}, or the driver refuses without partial delivery.",
                lambda: dispatch_n2(computer, "type", {"text": marker}, size),
                lambda values: has_text(values, marker)
                and (not requires_background_receipt or stayed_in_background(values)),
                allow_explicit_refusal=background and not allow_fallback,
            )
        )

        command_cases = [
            ("meta alias", "meta+k", "cmd+k"),
            ("command + shift alias", "command+shift+k", "cmd+shift+k"),
            ("control alias", "control+k", "ctrl+k"),
            ("option alias", "option+k", "option+k"),
        ]
        for case_name, expression, expected_command in command_cases:
            cases.append(
                await run_case(
                    computer,
                    log_path,
                    case_name,
                    f"{expression} maps to {expected_command}, or the driver refuses without partial delivery.",
                    lambda expression=expression: dispatch_n2(
                        computer, "key_press", {"key": expression}, size
                    ),
                    lambda values, expected_command=expected_command: has_event(values, "command", expected_command)
                    and (not requires_background_receipt or stayed_in_background(values)),
                    allow_explicit_refusal=background and not allow_fallback,
                )
            )

        cases.append(
            await run_case(
                computer,
                log_path,
                "navigation sequence",
                "left right escape return produces raw key events in order.",
                lambda: dispatch_n2(
                    computer,
                    "key_press",
                    {"key": "left right escape return"},
                    size,
                ),
                lambda values: sum(event.get("category") == "nsevent" for event in values) >= 4
                and (not requires_background_receipt or stayed_in_background(values)),
                allow_explicit_refusal=background and not allow_fallback,
            )
        )

        if background:
            button_point = target_center(read_events(log_path), "target-1", size)
            cases.append(
                await run_case(
                    computer,
                    log_path,
                    "button activation",
                    "Target 1 increments while the app remains inactive.",
                    lambda: computer.click(*button_point),
                    lambda values: any(
                        event.get("category") == "control"
                        and details(event).get("target") == "target-1"
                        for event in values
                    )
                    and stayed_in_background(values),
                )
            )

            before = read_events(log_path)
            starting_sequence = max((event_sequence(event) for event in before), default=-1)
            starting_outcomes = len(computer.action_outcomes)
            modified_error: str | None = None
            try:
                await computer.click(*button_point, modifier=["cmd"])
            except Exception as exc:
                modified_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(0.2)
            modified_events = [
                event for event in read_events(log_path) if event_sequence(event) > starting_sequence
            ]
            modified_delivery = delivery_dict(computer, starting_outcomes)
            if allow_fallback:
                passed = modified_error is None and any(
                    event.get("category") == "control" and details(event).get("target") == "target-1"
                    for event in modified_events
                ) and bool(modified_delivery and modified_delivery.get("escalated_in_case"))
                expected = "The modified click is foreground-delivered and reported as escalated."
            else:
                passed = modified_error is not None and not any(
                    event.get("category") == "control" for event in modified_events
                )
                expected = "The modified click is refused instead of degrading to an unmodified background click."
            cases.append(
                CaseResult(
                    name="modified background click",
                    passed=passed,
                    expected=expected,
                    error=modified_error,
                    received_events=modified_events,
                    delivery=modified_delivery,
                )
            )

    return (
        {
            "mode": mode,
            "captureSize": {"width": size[0], "height": size[1]},
            "deliveryCounts": computer.delivery_counts,
            "cases": [asdict(case) for case in cases],
        },
        target_pid,
    )


def ensure_probe_is_not_running() -> None:
    result = subprocess.run(["pgrep", "-x", EXECUTABLE_NAME], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        raise RuntimeError(
            "Yutori Input Probe is already running. Quit it before starting a deterministic session so the "
            "driver cannot bind to the wrong instance."
        )


def launch_probe(app_path: Path, session_id: str, log_path: Path) -> None:
    if not app_path.is_dir():
        raise FileNotFoundError(f"App bundle not found at {app_path}. Run scripts/build-input-probe.sh first.")
    subprocess.run(
        [
            "open", "-g", "-n", str(app_path), "--args",
            "--session-id", session_id,
            "--log-path", str(log_path),
        ],
        check=True,
    )


async def async_main(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise RuntimeError("The input probe requires macOS.")
    ensure_probe_is_not_running()
    os.environ["PATH"] = child_search_path()

    session_id = "driver-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_directory = REPOSITORY / ".context" / "input-probe" / session_id
    output_directory.mkdir(parents=True, exist_ok=False)
    log_path = output_directory / "app-events.jsonl"
    launch_probe(args.app.resolve(), session_id, log_path)
    events = await wait_for_events(log_path, lambda values: has_event(values, "session", "ready"), timeout=8)
    if not has_event(events, "session", "ready"):
        raise RuntimeError(f"The probe did not become ready; expected an event at {log_path}.")
    ready = next(event for event in events if event.get("category") == "session" and event.get("name") == "ready")
    launched_pid = int(details(ready)["pid"])

    modes = ["background", "foreground"] if args.mode == "both" else [args.mode]
    reports: list[dict[str, Any]] = []
    target_pid = launched_pid
    try:
        for mode in modes:
            report, target_pid = await run_mode(mode, log_path, args.allow_foreground_fallback)
            reports.append(report)
    finally:
        if target_pid and not args.keep_open:
            try:
                os.kill(target_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    result = {
        "sessionID": session_id,
        "app": str(args.app.resolve()),
        "appEventLog": str(log_path),
        "allowForegroundFallback": args.allow_foreground_fallback,
        "modes": reports,
    }
    report_path = output_directory / "driver-report.json"
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    failures = 0
    for mode_report in reports:
        print(f"\n{mode_report['mode'].upper()}")
        for case in mode_report["cases"]:
            passed = bool(case["passed"])
            failures += not passed
            if passed and case["error"]:
                suffix = " (explicit refusal; no input leaked)"
            else:
                suffix = f" — {case['error']}" if case["error"] else ""
            print(f"  {'PASS' if passed else 'FAIL'}  {case['name']}{suffix}")
    print(f"\nApp events: {log_path}")
    print(f"Report:     {report_path}")
    return 1 if failures else 0


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except Exception as error:
        print(f"input probe failed: {error}", file=sys.stderr)
        print("Run `uv run yutori-mcp computer-use doctor` to verify the local driver and permissions.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
