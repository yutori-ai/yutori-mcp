from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock


def _load_probe_runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run-input-probe.py"
    spec = importlib.util.spec_from_file_location("run_input_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe_runner()


OTHER_APP = "com.example.frontmost"


def _event(
    sequence: int,
    category: str,
    *,
    active: bool = False,
    frontmost: str | None = OTHER_APP,
    name: str = "event",
    **details: str,
) -> dict:
    return {
        "sequence": sequence,
        "category": category,
        "name": name,
        "details": details,
        "state": {"appActive": active, "frontmostBundleID": frontmost},
    }


def test_capture_baseline_reads_latest_sequence_and_outcome_count(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(_event(sequence, "nsevent")) for sequence in (1, 3, 2)) + "\n",
        encoding="utf-8",
    )
    computer = type("FakeComputer", (), {"action_outcomes": ("a", "b")})()

    assert probe.capture_baseline(path, computer) == (3, 2)


def test_capture_baseline_defaults_to_no_prior_sequence(tmp_path):
    path = tmp_path / "events.jsonl"
    computer = type("FakeComputer", (), {"action_outcomes": ()})()

    assert probe.capture_baseline(path, computer) == (-1, 0)


def test_read_events_ignores_an_incomplete_trailing_line(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(_event(1, "session")) + "\n{", encoding="utf-8")

    assert probe.read_events(path) == [_event(1, "session")]


def test_target_center_scales_appkit_points_to_capture_pixels():
    frame = _event(
        1,
        "layout",
        target="target-1",
        windowX="10",
        windowY="20",
        width="30",
        height="40",
        windowWidth="100",
        windowHeight="200",
    )
    frame["name"] = "targetFrame"

    assert probe.target_center([frame], "target-1", (200, 400)) == (50, 80)


def test_background_assertion_checks_only_input_evidence():
    events = [
        _event(1, "layout", frontmost=probe.BUNDLE_ID),
        _event(2, "nsevent"),
        _event(3, "text"),
    ]

    assert probe.stayed_in_background(events) is True
    assert probe.stayed_in_background(events + [_event(4, "command", frontmost=probe.BUNDLE_ID)]) is False
    assert probe.stayed_in_background(events + [_event(4, "control", frontmost=None)]) is False
    assert probe.stayed_in_background([_event(1, "layout")]) is False


def test_background_assertion_tolerates_driver_activation_without_raise():
    """The driver marks the target active without fronting it; only the frontmost app matters."""
    events = [_event(1, "nsevent", active=True), _event(2, "text", active=True)]

    assert probe.stayed_in_background(events) is True


def test_background_assertion_counts_web_input_but_not_web_bookkeeping():
    bookkeeping = [_event(1, "web", name="ready"), _event(2, "web", name="focus")]
    assert probe.stayed_in_background(bookkeeping) is False

    landed = bookkeeping + [_event(3, "web", name="input", value="x")]
    assert probe.stayed_in_background(landed) is True
    assert probe.stayed_in_background(landed + [_event(4, "web", name="submit", frontmost=probe.BUNDLE_ID)]) is False


def test_surface_matchers_bind_to_their_target():
    events = [
        _event(1, "text", name="changed", target="submitField", value="native-background"),
        _event(2, "control", name="submitted", target="submitField", value="native-background"),
        _event(3, "web", name="input", value="web-background"),
        _event(4, "web", name="submit", value="web-background"),
        _event(5, "web", name="keydown", key="Enter", keyCode="13"),
    ]

    assert probe.has_text(events, "native", target="submitField") is True
    assert probe.has_text(events, "native", target="keySink") is False
    assert probe.has_submit(events, "submitField") is True
    assert probe.has_submit(events, "keySink") is False
    assert probe.has_web_value(events, "web-background") is True
    assert probe.has_web_value(events, "native") is False
    assert probe.has_web_event(events, "submit") is True
    assert probe.has_web_event(events, "keydown", key="Enter") is True
    assert probe.has_web_event(events, "keydown", key="Tab") is False


def test_enter_verdict_names_landed_refused_and_silent_misses():
    landed = {"passed": True, "error": None, "delivery": {"effect": "confirmed"}}
    refused = {
        "passed": True,
        "error": "MacOSBackgroundDeliveryError: refused",
        "delivery": {"effect": "refused", "refusal_code": "same_pid_keyboard_ambiguity"},
    }
    silent = {"passed": False, "error": None, "delivery": {"effect": "unverifiable", "route": "synthetic_events"}}
    errored = {"passed": False, "error": "CuaDriverToolError: incomplete", "delivery": {"effect": "partial"}}

    assert probe.enter_verdict(landed) == "landed"
    assert probe.enter_verdict(refused) == "refused (same_pid_keyboard_ambiguity)"
    assert probe.enter_verdict(silent) == "no effect (driver said unverifiable via synthetic_events)"
    assert probe.enter_verdict(errored) == "error"


def test_latest_window_inventory_prefers_background_snapshots():
    events = [
        _event(1, "windows", name="inventory", reason="ready", keyWindow="none"),
        _event(2, "windows", name="inventory", active=True, reason="active", keyWindow="7"),
    ]

    assert probe.latest_window_inventory(events, background_only=True)["reason"] == "ready"
    assert probe.latest_window_inventory(events, background_only=False)["reason"] == "active"
    assert probe.latest_window_inventory([_event(1, "layout")], background_only=True) is None


def test_clean_refusal_rejects_partial_or_misdirected_input():
    delivery = {"effect": "unverifiable", "recommended": "foreground"}

    assert probe.is_clean_refusal("delivery failed", [_event(1, "layout")], delivery) is True
    assert probe.is_clean_refusal("delivery failed", [_event(1, "text")], delivery) is False
    assert probe.is_clean_refusal(None, [], delivery) is False


def test_key_down_sequence_requires_exact_order_without_duplicates():
    events = []
    for sequence, key_code in enumerate(("123", "124", "53", "36"), start=1):
        event = _event(sequence, "nsevent", keyCode=key_code)
        event["name"] = "keyDown"
        events.append(event)

    assert probe.has_key_down_sequence(events, ["123", "124", "53", "36"]) is True
    assert probe.has_key_down_sequence(events, ["124", "123", "53", "36"]) is False
    assert probe.has_key_down_sequence(events + [events[-1]], ["123", "124", "53", "36"]) is False


def test_modified_click_requires_the_modifier_and_active_delivery():
    events = []
    for sequence, name in enumerate(("leftMouseDown", "leftMouseUp"), start=1):
        event = _event(
            sequence,
            "nsevent",
            active=True,
            modifierFlags="shift+cmd",
            clickCount="1",
        )
        event["name"] = name
        events.append(event)

    assert probe.has_modified_click(events, "cmd") is True
    assert probe.has_modified_click(events, "option") is False
    assert probe.has_modified_click(events[:1], "cmd") is False
    events[-1]["state"]["appActive"] = False
    assert probe.has_modified_click(events, "cmd") is False


async def test_dispatch_n2_paces_a_multi_key_sequence():
    computer = type(
        "FakeComputer",
        (),
        {
            "keypress": AsyncMock(),
            "wait": AsyncMock(),
        },
    )()

    await probe.dispatch_n2(
        computer,
        "key_press",
        {"key": "left right escape return"},
        (100, 100),
    )

    assert [call.kwargs["keys"] for call in computer.keypress.await_args_list] == [
        ["left"],
        ["right"],
        ["esc"],
        ["enter"],
    ]
    assert [call.args for call in computer.wait.await_args_list] == [
        (probe.KEY_SEQUENCE_SETTLE_MS,),
        (probe.KEY_SEQUENCE_SETTLE_MS,),
        (probe.KEY_SEQUENCE_SETTLE_MS,),
    ]
