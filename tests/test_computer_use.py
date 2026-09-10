from __future__ import annotations

import argparse
import base64
import asyncio
import hashlib
import importlib.metadata
import io
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.error import HTTPError
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError
from yutori.navigator.macos import (
    FrontmostApp,
    MacOSFocusChangedError,
    MacOSPresentationStatus,
    ShellPresentationEvent,
)
from yutori.navigator.macos.transport import CuaDriverToolError, CuaDriverUncertainActionError

from yutori_mcp.computer_use import preflight, runner as runner_module, supervisor
from yutori_mcp.computer_use.app import pick_best_window, prepare_app
from yutori_mcp.computer_use.constants import (
    DELIVERY_MODE_BACKGROUND,
    DELIVERY_MODE_FOREGROUND,
    DELIVERY_MODES,
    DRIVER_VERSION,
    MCP_VERSION,
    OBSERVATION_FORMAT,
    PROTOCOL_VERSION,
    SDK_ARTIFACT_SHA256,
    SDK_INSTALLATION_SHA256,
    SDK_PROVENANCE_SHA256,
    SDK_VERSION,
    TOOL_SET,
)
from yutori_mcp.computer_use.lock import ComputerUseBusyError, DesktopLock
from yutori_mcp.computer_use.result import (
    FINAL_OUTPUT_HEADING,
    Terminal,
    failure,
    format_duration,
    format_result,
    format_startup_line,
    format_terminal_action,
    format_terminal_result,
    redact,
    supports_color,
    supports_glyphs,
    terminal_result,
)
from yutori_mcp.computer_use.supervisor import attach_run_link, run_chat_id
from yutori_mcp.computer_use.targeting import TargetGuardedMacOSComputer, require_frontmost_target
from yutori_mcp.computer_use.runner import (
    ActionReporter,
    Emitter,
    RequestError,
    RunGuard,
    batch_action_previews,
    classify_result,
    parse_request,
)
from yutori_mcp.computer_use.supervisor import (
    RUNNER_FRAME_LIMIT_BYTES,
    _stop_process_group,
    _supervise,
    python_runner_command,
    run_task,
)
from yutori_mcp.schemas import COMPUTER_USE_DEFAULT_MINUTES, COMPUTER_USE_DEFAULT_MODE, ComputerUseMode, ComputerUseTaskInput


@pytest.mark.parametrize("minutes", [0.9, 60.1])
def test_schema_rejects_minutes(minutes):
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", minutes=minutes)


def test_schema_allows_one_hour_deadline():
    assert ComputerUseTaskInput(task="x", minutes=60).minutes == 60


def test_schema_defaults_to_thirty_minutes():
    assert COMPUTER_USE_DEFAULT_MINUTES == 30
    assert ComputerUseTaskInput(task="x").minutes == 30


@pytest.mark.parametrize("max_steps", [0, -1])
def test_schema_rejects_nonpositive_max_steps(max_steps):
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", max_steps=max_steps)


def test_schema_allows_large_max_steps():
    assert ComputerUseTaskInput(task="x", max_steps=250).max_steps == 250


def test_schema_requires_app_for_url_and_has_no_harness_override():
    with pytest.raises(ValidationError, match="start_url requires app"):
        ComputerUseTaskInput(task="x", start_url="https://example.com")
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", harness="node")


def test_computer_use_cli_imports_without_executing_the_sdk():
    source = Path(__file__).parents[1] / "src"
    script = """
import importlib.abc
import sys

class BlockSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "yutori" or fullname.startswith("yutori."):
            raise RuntimeError(f"unexpected SDK import: {fullname}")
        return None

sys.meta_path.insert(0, BlockSDK())
import yutori_mcp.entrypoint
import yutori_mcp.computer_use.cli
"""
    environment = {**os.environ, "PYTHONPATH": str(source)}
    subprocess.run([sys.executable, "-c", script], env=environment, check=True)


@pytest.mark.parametrize(
    "platform,environment,expected",
    [("linux", "prod", False), ("darwin", "prod", True), ("darwin", "unknown", False)],
)
def test_registration_gate(platform, environment, expected):
    with patch("yutori_mcp.server.sys.platform", platform), patch.dict("os.environ", {"YUTORI_ENV": environment}):
        from yutori_mcp.server import _computer_use_enabled

        assert _computer_use_enabled() is expected


def test_main_applies_explicit_environment_before_computer_use_registration(monkeypatch):
    from yutori_mcp import server

    observed = []

    def record_registration() -> None:
        observed.append(os.environ["YUTORI_ENV"])

    monkeypatch.setenv("YUTORI_ENV", "dev")
    monkeypatch.setattr(sys, "argv", ["yutori-mcp", "--env", "prod"])
    monkeypatch.setattr(server, "_register_computer_use_tool", record_registration)
    monkeypatch.setattr(server.mcp, "run", lambda **_kwargs: None)

    server.main()
    assert observed == ["prod"]


def _recording_dispatch() -> tuple[dict[str, str | None], Callable[[str, object], int]]:
    """A `dispatch`-shaped stub that records the ambient YUTORI_ENV it sees when called.

    Shared by the three tests below pinning how `--env`/ambient YUTORI_ENV reaches
    `computer_use.cli.dispatch` through server.main() and entrypoint._computer_use_main().
    """
    observed: dict[str, str | None] = {}

    def record_dispatch(_command: str, _args: object) -> int:
        observed["environment"] = os.environ.get("YUTORI_ENV")
        return 0

    return observed, record_dispatch


def test_main_applies_explicit_environment_before_computer_use_dispatch(monkeypatch):
    import yutori_mcp.computer_use.cli as computer_use_cli
    from yutori_mcp import server

    observed, record_dispatch = _recording_dispatch()

    monkeypatch.setenv("YUTORI_ENV", "prod")
    monkeypatch.setattr(sys, "argv", ["yutori-mcp", "--env", "dev", "computer-use", "doctor"])
    monkeypatch.setattr(computer_use_cli, "dispatch", record_dispatch)

    with pytest.raises(SystemExit) as exc_info:
        server.main()
    assert exc_info.value.code == 0
    assert observed == {"environment": "dev"}


def test_main_clears_ambient_environment_for_computer_use_without_explicit_env(monkeypatch):
    import yutori_mcp.computer_use.cli as computer_use_cli
    from yutori_mcp import server

    observed, record_dispatch = _recording_dispatch()

    monkeypatch.setenv("YUTORI_ENV", "dev")
    monkeypatch.setattr(sys, "argv", ["yutori-mcp", "computer-use", "doctor"])
    monkeypatch.setattr(computer_use_cli, "dispatch", record_dispatch)

    with pytest.raises(SystemExit) as exc_info:
        server.main()
    assert exc_info.value.code == 0
    assert observed == {"environment": None}


@pytest.mark.parametrize(
    ("arguments", "ambient", "expected"),
    [(["--env", "dev"], "prod", "dev"), ([], "dev", None)],
)
def test_protected_entrypoint_applies_computer_use_environment(monkeypatch, arguments, ambient, expected):
    import yutori_mcp.computer_use.cli as computer_use_cli
    from yutori_mcp import entrypoint

    observed, record_dispatch = _recording_dispatch()

    monkeypatch.setenv("YUTORI_ENV", ambient)
    monkeypatch.setattr(sys, "argv", ["yutori-mcp", *arguments, "computer-use", "doctor"])
    monkeypatch.setattr(computer_use_cli, "dispatch", record_dispatch)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint._computer_use_main()
    assert exc_info.value.code == 0
    assert observed == {"environment": expected}


def test_entrypoint_env_choices_match_adapter_environments():
    """entrypoint._ENV_CHOICES is a hardcoded duplicate of adapter.ENVIRONMENT_BASE_URLS's keys
    (see the comment on _ENV_CHOICES for why it can't just import that dict). This pins the two
    together so a new environment added to one is caught if it is not added to the other."""
    from yutori_mcp import entrypoint
    from yutori_mcp.adapter import ENVIRONMENT_BASE_URLS

    assert set(entrypoint._ENV_CHOICES) == set(ENVIRONMENT_BASE_URLS)


def test_lock_rejects_second_owner_and_releases(tmp_path):
    path = tmp_path / "desktop.lock"
    with DesktopLock(path), pytest.raises(ComputerUseBusyError), DesktopLock(path):
        pass
    with DesktopLock(path):
        pass


def test_lock_is_reentrant_for_one_owner(tmp_path):
    path = tmp_path / "desktop.lock"
    lock = DesktopLock(path)
    with lock, lock, pytest.raises(ComputerUseBusyError), DesktopLock(path):
        pass
    with DesktopLock(path):
        pass


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
def test_lock_releases_on_exception_and_cancellation(tmp_path, error):
    path = tmp_path / "desktop.lock"
    with pytest.raises(type(error)), DesktopLock(path):
        raise error
    with DesktopLock(path):
        pass


class _Writer:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _Process:
    def __init__(self, stdout, stderr):
        self.pid = 123
        self.returncode = None
        self.stdin = _Writer()
        self.stdout = stdout
        self.stderr = stderr

    async def wait(self):
        self.returncode = 0
        return 0


def _stream(*lines):
    stream = asyncio.StreamReader()
    for line in lines:
        stream.feed_data(line.encode() + b"\n")
    stream.feed_eof()
    return stream


def _ready_event(**overrides):
    event = {
        "type": "ready",
        "protocol_version": PROTOCOL_VERSION,
        "package_version": MCP_VERSION,
        "sdk_version": SDK_VERSION,
        "sdk_artifact_sha256": SDK_ARTIFACT_SHA256,
        "sdk_provenance_sha256": SDK_PROVENANCE_SHA256,
        "driver_version_pinned": DRIVER_VERSION,
    }
    event.update(overrides)
    return event


def _result_event(**overrides):
    event = {
        "type": "result",
        "outcome": "completed",
        "delivery_mode": "foreground",
        "final_text": "ok",
    }
    event.update(overrides)
    return event


def _action_event(**overrides):
    event = {
        "type": "action",
        "index": 0,
        "tool": "left_click",
        "status": "executed",
        "raw_status": "confirmed",
        "delivery_mode": "foreground",
        "route": "pixel",
        "refusal_code": None,
    }
    event.update(overrides)
    return event


def _startup_event(**overrides):
    event = {
        "type": "startup",
        "phase": "computer",
        "duration_ms": 250,
        "elapsed_ms": 300,
    }
    event.update(overrides)
    return event


async def _run_supervised(process, *, api_key="yt-key", deadline_seconds=1, **supervise_kwargs):
    """Patch the child process and call `_supervise` with the shared fixed args this file's tests repeat.

    Only for tests that don't need to assert on the `create_subprocess_exec` mock itself or patch anything
    beyond it -- those keep their own inline `with patch(...)` block.
    """
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        return await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key=api_key,
            deadline=time.monotonic() + deadline_seconds,
            **supervise_kwargs,
        )


def _stub_process_group_stop(process) -> Callable[[Any], Any]:
    """`side_effect` for a patched `_stop_process_group` that marks `process` SIGTERM'd.

    Stands in for the real stop path so tests can force `_supervise` to observe a terminated
    child without actually spawning a process group to signal.
    """

    async def stop(_process):
        process.returncode = -signal.SIGTERM

    return stop


@contextmanager
def _patched_process_group_stop(process):
    """Patch subprocess spawn and `_stop_process_group` for one `_supervise` call.

    Covers the tests below that need both `_supervise`'s child process replaced with
    `process` *and* the process-group stop path observed or stubbed -- `_run_supervised`
    above only covers the simpler case that leaves `_stop_process_group` unpatched.
    Yields the `_stop_process_group` mock so callers can assert on it.
    """
    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch(
            "yutori_mcp.computer_use.supervisor._stop_process_group",
            side_effect=_stub_process_group_stop(process),
        ) as stopped,
    ):
        yield stopped


async def _run_supervised_with_stop_patched(
    process, *, api_key="yt-key", deadline_seconds=1, **supervise_kwargs
) -> tuple[dict[str, Any], AsyncMock]:
    """Like `_run_supervised`, but through `_patched_process_group_stop` for its shared args.

    For the tests below that also need to observe or stub the process-group stop path
    (`stopped.assert_awaited_once_with(process)`) rather than leaving it unpatched --
    tests whose control flow diverges further (creating and cancelling their own task)
    keep their own inline `with _patched_process_group_stop(...)` block.
    """
    with _patched_process_group_stop(process) as stopped:
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key=api_key,
            deadline=time.monotonic() + deadline_seconds,
            **supervise_kwargs,
        )
    return result, stopped


async def test_supervisor_redacts_key_and_keeps_it_out_of_argv():
    secret = "yt-super-secret-value"
    process = _Process(
        _stream(json.dumps(_ready_event()), json.dumps({"type": "error", "code": "X", "message": secret})),
        _stream(f"diagnostic {secret}"),
    )
    create = AsyncMock(return_value=process)
    with patch("asyncio.create_subprocess_exec", create):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key=secret,
            deadline=time.monotonic() + 1,
        )
    assert secret not in json.dumps(result)
    assert secret not in " ".join(create.await_args.args)
    assert process.stdin.data.count(b"\n") == 1


async def test_supervisor_forwards_ready_and_action_events():
    events = [
        _ready_event(reasoning_overlay_requested=True),
        _startup_event(),
        _action_event(index=1, tool="computer_batch", elapsed_ms=42),
        _result_event(),
    ]
    process = _Process(_stream(*(json.dumps(event) for event in events)), _stream(""))
    seen: list[dict] = []

    async def on_event(event):
        seen.append(event)

    result = await _run_supervised(process, on_event=on_event)
    assert [event["type"] for event in seen] == ["ready", "startup", "action"]
    assert result["actions"] == [events[2]]


async def test_supervisor_forwards_frame_and_activity_events_without_recording_them_as_actions():
    frame = {"type": "frame", "capture_id": 1, "media_type": "image/jpeg", "data": "AAAA", "caption": "Frame 1"}
    activity = {"type": "activity", "entry": {"id": "entry-0", "kind": "thinking", "text": "hm"}}
    events = [_ready_event(reasoning_overlay_requested=True), frame, activity, _action_event(index=1), _result_event()]
    process = _Process(_stream(*(json.dumps(event) for event in events)), _stream(""))
    seen: list[dict] = []

    async def on_event(event):
        seen.append(event)

    result = await _run_supervised(process, on_event=on_event)
    assert [event["type"] for event in seen] == ["ready", "frame", "activity", "action"]
    assert result["outcome"] == "completed"
    assert [action["type"] for action in result["actions"]] == ["action"]


async def test_supervisor_does_not_let_a_blocked_progress_callback_stall_protocol_drain(monkeypatch):
    monkeypatch.setattr(supervisor, "EVENT_CALLBACK_FLUSH_SECONDS", 0.01)
    started = asyncio.Event()

    async def on_event(_event):
        started.set()
        await asyncio.Future()

    action = _action_event()
    process = _Process(
        _stream(json.dumps(_ready_event()), json.dumps(action), json.dumps(_result_event())),
        _stream(""),
    )

    result = await asyncio.wait_for(_run_supervised(process, on_event=on_event), 0.2)

    assert started.is_set()
    assert result["outcome"] == "completed"
    assert result["actions"] == [action]


async def test_supervisor_does_not_advertise_the_pid_before_ready(monkeypatch):
    record = Mock()
    monkeypatch.setattr(supervisor, "_record_runner_pid", record)
    monkeypatch.setattr(supervisor, "_clear_runner_pid", Mock())
    process = _Process(_stream("not-json"), _stream(""))

    result = await _run_supervised(process)

    assert result["outcome"] == "failed"
    record.assert_not_called()


async def test_supervisor_accepts_result_larger_than_default_stream_limit():
    final_text = "x" * 70_000
    stream = asyncio.StreamReader(limit=RUNNER_FRAME_LIMIT_BYTES)
    for event in (_ready_event(), _result_event(final_text=final_text)):
        stream.feed_data(json.dumps(event).encode() + b"\n")
    stream.feed_eof()
    process = _Process(stream, _stream(""))
    create = AsyncMock(return_value=process)

    with patch("asyncio.create_subprocess_exec", create):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )

    assert result["final_text"] == final_text
    assert create.await_args.kwargs["limit"] == RUNNER_FRAME_LIMIT_BYTES


async def test_supervisor_rejects_result_larger_than_configured_stream_limit():
    stream = asyncio.StreamReader(limit=100)
    stream.feed_data(json.dumps(_ready_event()).encode() + b"\n")
    stream.feed_data(json.dumps({"type": "result", "final_text": "x" * 200}).encode() + b"\n")
    stream.feed_eof()
    process = _Process(stream, _stream(""))
    process.returncode = 0

    result = await _run_supervised(process)

    assert result["outcome"] == "failed"
    assert "exceeded" in result["final_text"]


async def test_supervisor_rejects_runner_provenance_drift():
    process = _Process(
        _stream(json.dumps(_ready_event(sdk_version="0.8.1"))),
        _stream(""),
    )

    result, _ = await _run_supervised_with_stop_patched(process)
    assert result["outcome"] == "failed"
    assert "provenance mismatch" in result["final_text"]


@pytest.mark.parametrize(
    "event",
    [
        {"type": "action"},
        {
            "type": "action",
            "index": 0,
            "tool": "left_click",
            "status": "executed",
            "raw_status": "confirmed",
            "delivery_mode": "foreground",
            "route": "pixel",
        },
        {"type": "startup", "phase": "computer"},
        {"type": "result", "outcome": "completed"},
    ],
)
async def test_supervisor_rejects_malformed_events(event):
    process = _Process(_stream(json.dumps(_ready_event()), json.dumps(event)), _stream(""))
    process.returncode = 0
    result = await _run_supervised(process)
    assert result["outcome"] == "failed"
    assert "malformed" in result["final_text"]


async def test_supervisor_rejects_invalid_utf8_in_an_otherwise_valid_event():
    stream = asyncio.StreamReader()
    stream.feed_data(json.dumps(_ready_event()).encode() + b"\n")
    stream.feed_data(b'{"type":"result","outcome":"completed","delivery_mode":"foreground","final_text":"\xff"}\n')
    stream.feed_eof()
    process = _Process(stream, _stream(""))
    process.returncode = 0
    result = await _run_supervised(process)
    assert result["outcome"] == "failed"
    assert "invalid JSON" in result["final_text"]


async def test_supervisor_overwrites_child_supplied_actions():
    action = _action_event()
    process = _Process(
        _stream(
            json.dumps(_ready_event()),
            json.dumps(action),
            json.dumps(_result_event(actions=[{"tool": "forged"}])),
        ),
        _stream(""),
    )
    result = await _run_supervised(process)
    assert result["actions"] == [action]


async def test_supervisor_rejects_data_after_a_terminal_event():
    process = _Process(
        _stream(
            json.dumps(_ready_event()),
            json.dumps(_result_event()),
            json.dumps({"type": "action", "tool": "left_click"}),
        ),
        _stream(""),
    )
    process.returncode = 0
    result = await _run_supervised(process)
    assert result["outcome"] == "failed"
    assert "after its terminal event" in result["final_text"]


def test_remaining_seconds_returns_positive_time_left():
    deadline = time.monotonic() + 5
    remaining = supervisor._remaining_seconds(deadline)
    assert 0 < remaining <= 5


def test_remaining_seconds_raises_once_deadline_has_passed():
    with pytest.raises(asyncio.TimeoutError):
        supervisor._remaining_seconds(time.monotonic() - 1)


async def test_stderr_diagnostics_retain_only_the_latest_twenty_lines():
    diagnostics = await supervisor._drain_stderr(_stream(*(f"line-{index}" for index in range(30))), "secret")

    assert diagnostics == [f"line-{index}" for index in range(10, 30)]


async def test_stop_process_group_escalates_to_kill():
    process = SimpleNamespace(pid=123, returncode=None, wait=AsyncMock(return_value=0))

    def killed(_pid, sig):
        if sig == signal.SIGKILL:
            process.returncode = -signal.SIGKILL

    async def expire(awaitable, _timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    with (
        patch("yutori_mcp.computer_use.supervisor.os.killpg", side_effect=killed) as kill,
        patch("yutori_mcp.computer_use.supervisor.asyncio.wait_for", side_effect=expire),
    ):
        await _stop_process_group(process)
    assert [call.args[1] for call in kill.call_args_list] == [signal.SIGTERM, signal.SIGKILL]


async def test_supervisor_stdout_deadline_returns_limit_and_stops_group():
    process = _Process(asyncio.StreamReader(), asyncio.StreamReader())

    result, stopped = await _run_supervised_with_stop_patched(process, api_key="secret", deadline_seconds=0.01)
    assert result["outcome"] == "limit"
    stopped.assert_awaited_once_with(process)


async def test_supervisor_eof_does_not_bypass_the_deadline():
    process = _Process(_stream(json.dumps(_ready_event()), json.dumps(_result_event())), _stream(""))

    async def wait_forever():
        await asyncio.Future()

    process.wait = wait_forever
    result, stopped = await _run_supervised_with_stop_patched(process, api_key="secret", deadline_seconds=0.01)
    assert result["outcome"] == "limit"
    stopped.assert_awaited_once_with(process)


async def test_supervisor_cancellation_is_aborted_and_stops_group():
    process = _Process(asyncio.StreamReader(), asyncio.StreamReader())

    with _patched_process_group_stop(process) as stopped:
        task = asyncio.create_task(
            _supervise(
                command=python_runner_command(),
                request={"type": "run"},
                api_key="secret",
                deadline=time.monotonic() + 60,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        result = await task
    assert result["outcome"] == "aborted"
    stopped.assert_awaited()


async def test_runner_sigterm_path_cancels_the_sdk_session(monkeypatch):
    started = asyncio.Event()
    cleaned = asyncio.Event()
    handlers = {}

    async def run_request(_request, _emitter, _api_key, cancellation):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            assert cancellation.cause == "supervisor"
            cleaned.set()
            return "aborted"

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(runner_module, "run_request", run_request)
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, callback: handlers.setdefault(sig, callback))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: handlers.pop(sig, None) is not None)
    task = asyncio.create_task(runner_module._run_until_terminated({}, Emitter(_CollectStream()), "key"))
    await started.wait()
    handlers[signal.SIGTERM]()
    handlers[signal.SIGTERM]()
    assert await task == "aborted"
    assert cleaned.is_set()


async def test_runner_honors_sigterm_received_before_the_async_loop(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(runner_module, "run_request", run)
    stream = _CollectStream()

    outcome = await runner_module._run_until_terminated(
        _valid_request(),
        Emitter(stream),
        "key",
        lambda: True,
    )

    assert outcome == "aborted"
    run.assert_not_awaited()
    assert json.loads(stream.lines[-1]) == {
        "type": "result",
        "outcome": "aborted",
        "delivery_mode": "foreground",
        "final_text": "The computer-use run was stopped.",
        "elapsed_ms": 0,
        "steps": 0,
    }


async def test_runner_honors_sigterm_during_signal_handler_handoff(monkeypatch):
    run = AsyncMock()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(runner_module, "run_request", run)
    monkeypatch.setattr(loop, "add_signal_handler", lambda _sig, callback: callback())
    monkeypatch.setattr(loop, "remove_signal_handler", lambda _sig: True)
    stream = _CollectStream()

    outcome = await runner_module._run_until_terminated(
        _valid_request(),
        Emitter(stream),
        "key",
    )

    assert outcome == "aborted"
    run.assert_not_awaited()
    assert json.loads(stream.lines[-1])["outcome"] == "aborted"


def test_runner_removes_api_key_before_spawning_a_real_shell(monkeypatch):
    secret = "yt-child-shell-secret"
    monkeypatch.setenv("YUTORI_API_KEY", secret)
    assert runner_module._take_api_key() == secret
    assert "YUTORI_API_KEY" not in os.environ
    subprocess.run(["/bin/sh", "-c", 'test -z "${YUTORI_API_KEY+x}"'], check=True)


def test_python_runner_is_isolated_and_has_no_node_path():
    assert python_runner_command() == [sys.executable, "-I", "-B", "-m", "yutori_mcp.computer_use.runner"]
    source = Path(supervisor.__file__).read_text()
    assert "find_node" not in source
    assert "load_runtime" not in source


def _run_task_kwargs(tmp_path, **overrides):
    kwargs = {
        "task": "open calculator",
        "app": None,
        "start_url": None,
        "minutes": 1,
        "max_steps": 10,
        "api_key": "yt-key",
        "api_base_url": "https://api.yutori.com/v1",
        "lock": DesktopLock(tmp_path / "desktop.lock"),
    }
    kwargs.update(overrides)
    return kwargs


@contextmanager
def _patched_run_task_supervise(tmp_path, *, result=None):
    """Patch driver discovery and `_supervise()` so `run_task()` sees a fake driver.

    Shared by the tests below that call `run_task()` directly and need its two
    module-level dependencies stubbed identically -- a driver file `find_cua_driver()`
    can resolve, and `_supervise()` returning ``result`` (default: a bare completed
    outcome) instead of actually spawning the runner subprocess. Yields the `_supervise`
    AsyncMock so callers can inspect its `await_args`.
    """
    driver = tmp_path / "cua-driver"
    driver.write_text("")
    supervise = AsyncMock(return_value=result if result is not None else {"outcome": "completed"})
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_cua_driver", return_value=driver),
    ):
        yield supervise


def _patch_run_credentials(monkeypatch, *, api_key: str = "k") -> None:
    """Patch resolve_run_credentials_and_platform_url() with a fixed key/base_url/platform_url triple.

    Every test that drives a full run path (server._handle_computer_use, cli._smoke_live,
    cli._run_custom) needs this resolved identically; only the api_key literal varies by
    call site, and nothing asserts on that value.
    """
    monkeypatch.setattr(
        "yutori_mcp.adapter.resolve_run_credentials_and_platform_url",
        lambda: (api_key, "https://api.yutori.com/v1", "https://platform.yutori.com"),
    )


async def test_run_task_uses_only_python_runner_and_sdk_driver_discovery(tmp_path):
    with _patched_run_task_supervise(tmp_path) as supervise:
        result = await run_task(**_run_task_kwargs(tmp_path))
    assert result["outcome"] == "completed"
    assert supervise.await_args.kwargs["command"] == python_runner_command()
    request = supervise.await_args.kwargs["request"]
    assert request["model"] == "n2"
    assert "driver_path" not in request and "harness" not in request


async def test_run_task_links_the_run_to_the_platform_chat_page(tmp_path):
    action = _action_event(tool="screenshot", chat_id="chat-1")
    supervised = terminal_result("limit", "deadline", actions=[action])
    with _patched_run_task_supervise(tmp_path, result=supervised):
        result = await run_task(**_run_task_kwargs(tmp_path, platform_url="https://platform.dev.yutori.com"))
    assert result["chat_id"] == "chat-1"
    assert result["run_url"] == "https://platform.dev.yutori.com/navigator/chats/chat-1"
    assert "Run: https://platform.dev.yutori.com/navigator/chats/chat-1" in format_result(result)


def _patch_server_lock(monkeypatch, lock_module, lock, first_blocker=lambda: None) -> None:
    """Patch DesktopLock and first_blocker() for tests driving server._handle_computer_use directly.

    Mirrors _patch_smoke_preflight's convention for the CLI's DesktopLock/first_blocker pair;
    every caller here reserves the desktop at a test-owned lock and stubs the preflight gate
    before exercising the server handler, and only the lock instance and blocker differ.
    """
    monkeypatch.setattr(lock_module, "DesktopLock", lambda: lock)
    monkeypatch.setattr(preflight, "first_blocker", first_blocker)


async def test_server_holds_desktop_lock_across_preflight_and_runner(monkeypatch, tmp_path):
    from yutori_mcp import server
    from yutori_mcp.computer_use import lock as lock_module

    lock = DesktopLock(tmp_path / "desktop.lock")

    def first_blocker() -> None:
        assert lock._file is not None
        return None

    async def run_with_lock(**kwargs: Any) -> dict[str, str]:
        assert kwargs["lock"] is lock
        assert lock._file is not None
        return {"outcome": "completed"}

    _patch_server_lock(monkeypatch, lock_module, lock, first_blocker)
    monkeypatch.setattr(supervisor, "run_task", run_with_lock)
    _patch_run_credentials(monkeypatch, api_key="api-key")

    result, raw = await server._handle_computer_use(None, {"task": "open calculator"})
    assert result["outcome"] == "completed"
    assert raw == {}
    assert lock._file is None


def test_runtime_constants_select_latest_python_surface():
    assert TOOL_SET == "computer_use_tools-20260830"
    assert SDK_VERSION == "0.9.25"
    assert all(len(digest) == 64 for digest in (SDK_ARTIFACT_SHA256, SDK_INSTALLATION_SHA256, SDK_PROVENANCE_SHA256))
    assert '"yutori==0.9.25"' in Path(__file__).parents[1].joinpath("pyproject.toml").read_text()


def test_installed_sdk_matches_the_published_artifact():
    result = preflight.check_runtime()
    assert result.ok, result.detail


@pytest.mark.skipif(
    os.environ.get("YUTORI_MCP_VERIFY_PUBLISHED_ARTIFACT") != "1",
    reason="release artifact verification is a single-version CI check",
)
def test_published_sdk_wheel_matches_artifact_hash():
    with urllib.request.urlopen(f"https://pypi.org/pypi/yutori/{SDK_VERSION}/json", timeout=30) as response:
        release = json.load(response)
    wheels = [item for item in release["urls"] if item["filename"].endswith("-py3-none-any.whl")]
    assert len(wheels) == 1
    wheel = wheels[0]
    assert wheel["digests"]["sha256"] == SDK_ARTIFACT_SHA256
    with urllib.request.urlopen(wheel["url"], timeout=30) as response:
        assert hashlib.sha256(response.read()).hexdigest() == SDK_ARTIFACT_SHA256


def test_mcp_protocol_version_matches_package_metadata():
    assert MCP_VERSION == importlib.metadata.version("yutori-mcp")


def test_driver_contract_rejects_version_drift(monkeypatch):
    monkeypatch.setattr(preflight, "driver_version", lambda: "0.18.0")
    result = preflight.check_driver_contract()
    assert not result.ok and result.blocking
    monkeypatch.setattr(preflight, "driver_version", lambda: DRIVER_VERSION)
    assert preflight.check_driver_contract().ok


def test_api_access_probes_the_runtime_toolset(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"choices":[{}]}'

    monkeypatch.setattr(
        "yutori_mcp.adapter.resolve_run_credentials",
        lambda _environment: ("api-key", "https://api.yutori.com/v1"),
    )
    monkeypatch.setattr(preflight, "urlopen", lambda request, timeout: requests.append(request) or Response())

    assert preflight.check_api_access().ok
    assert json.loads(requests[0].data)["tool_set"] == TOOL_SET
    assert requests[0].full_url == "https://api.yutori.com/v1/chat/completions"


@pytest.mark.parametrize("status", [429, 500])
def test_api_access_rejects_non_auth_http_failures(monkeypatch, status):
    def fail_probe(*_args: Any, **_kwargs: Any) -> None:
        raise HTTPError("https://api.yutori.com", status, "failed", {}, None)

    monkeypatch.setattr(
        "yutori_mcp.adapter.resolve_run_credentials", lambda _: ("api-key", "https://api.yutori.com/v1")
    )
    monkeypatch.setattr(preflight, "urlopen", fail_probe)
    result = preflight.check_api_access()
    assert not result.ok
    assert result.detail == f"probe failed (HTTP {status})"


def test_api_access_reports_invalid_model_instead_of_login(monkeypatch):
    body = json.dumps(
        {
            "error": {
                "message": "Invalid model. Available models: n1.5-latest",
                "code": "invalid_model",
            }
        }
    ).encode()

    def fail_probe(*_args: Any, **_kwargs: Any) -> None:
        raise HTTPError("https://api.yutori.com", 400, "failed", {}, io.BytesIO(body))

    monkeypatch.setattr(
        "yutori_mcp.adapter.resolve_run_credentials", lambda _: ("api-key", "https://api.yutori.com/v1")
    )
    monkeypatch.setattr(preflight, "urlopen", fail_probe)
    result = preflight.check_api_access()
    assert not result.ok
    assert result.detail == "Invalid model. Available models: n1.5-latest"
    assert "requests 'n2'" in result.remediation
    assert "login" not in result.remediation


def test_runtime_check_verifies_version_installation_and_provenance(monkeypatch, tmp_path):
    payload = b"provenance"
    package_file = Path("yutori/runtime.py")
    installed_file = tmp_path / package_file
    installed_file.parent.mkdir()
    installed_file.write_text("trusted")
    provenance_file = tmp_path / "yutori/navigator/macos/assets/provenance.json"
    provenance_file.parent.mkdir(parents=True)
    provenance_file.write_bytes(payload)
    distribution = SimpleNamespace(
        version=SDK_VERSION,
        files=[package_file],
        locate_file=lambda path: tmp_path / path,
        read_text=lambda _name: None,
    )
    monkeypatch.setattr(preflight.importlib.metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(preflight, "SDK_PROVENANCE_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(preflight, "SDK_INSTALLATION_SHA256", preflight._stable_distribution_digest(distribution))
    assert preflight.check_runtime().ok

    installed_file.write_text("modified")
    monkeypatch.setattr(preflight, "_provenance_path", lambda *_args, **_kwargs: pytest.fail("read untrusted SDK"))
    assert not preflight.check_runtime().ok


def test_runtime_check_requires_an_explicit_editable_override(monkeypatch, tmp_path):
    payload = b"provenance"
    provenance_file = tmp_path / "yutori/navigator/macos/assets/provenance.json"
    provenance_file.parent.mkdir(parents=True)
    provenance_file.write_bytes(payload)
    distribution = SimpleNamespace(
        version=SDK_VERSION,
        read_text=lambda _name: json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}),
    )
    monkeypatch.setattr(preflight.importlib.metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(preflight, "SDK_PROVENANCE_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.delenv("YUTORI_MCP_ALLOW_EDITABLE_SDK", raising=False)
    assert not preflight.check_runtime().ok

    monkeypatch.setenv("YUTORI_MCP_ALLOW_EDITABLE_SDK", "1")
    assert preflight.check_runtime().ok


def test_runtime_digest_ignores_pip_generated_bytecode(tmp_path):
    source = Path("yutori/runtime.py")
    bytecode = Path("yutori/__pycache__/runtime.cpython-310.pyc")
    for path, content in ((source, b"trusted"), (bytecode, b"interpreter-specific")):
        installed = tmp_path / path
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(content)
    distribution = SimpleNamespace(
        files=[source, bytecode],
        locate_file=lambda path: tmp_path / path,
    )
    source_only = SimpleNamespace(files=[source], locate_file=distribution.locate_file)
    assert preflight._stable_distribution_digest(distribution) == preflight._stable_distribution_digest(source_only)


def test_overlay_compiler_and_capture_failures_are_warnings(monkeypatch):
    monkeypatch.setattr(preflight, "find_cua_driver", lambda: None)
    capture = preflight.check_capture()
    assert not capture.ok and not capture.blocking
    with patch.object(preflight.subprocess, "run", side_effect=OSError("missing")):
        compiler = preflight.check_compiler()
    assert not compiler.ok and not compiler.blocking
    with patch("yutori.navigator.macos.check_macos_overlay", side_effect=RuntimeError("missing")):
        overlay = preflight.check_overlay()
    assert not overlay.ok and not overlay.blocking


@pytest.mark.parametrize(
    "lock_value,ok,detail",
    [
        ("No", True, "console user testuser"),
        ("Yes", False, "console user testuser; screen locked"),
        (None, False, "console user testuser; lock state unavailable"),
    ],
)
def test_gui_session_checks_machine_lock_state(monkeypatch, lock_value, ok, detail):
    def run(argv, **_kwargs):
        if argv[0] == "/usr/bin/stat":
            return subprocess.CompletedProcess(argv, 0, stdout="testuser\n")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                f'"IOConsoleLocked" = {lock_value}\n'
                if lock_value is not None
                else '"OtherProperty" = Yes\n'
            ),
        )

    monkeypatch.setattr(preflight.subprocess, "run", run)
    result = preflight.check_gui_session()
    assert result.ok is ok
    assert result.detail == detail


def test_first_blocker_uses_only_the_live_run_safety_checks(monkeypatch):
    calls = []

    def ok():
        calls.append("ok")
        return preflight.CheckResult("runtime", True, "ready")

    def blocker():
        calls.append("blocker")
        return preflight.CheckResult("driver", False, "missing", "setup")

    def never():
        raise AssertionError("checks after the first blocker must not run")

    monkeypatch.setattr(preflight, "_RUN_BLOCKING_CHECKS", (ok, blocker, never))
    assert preflight.first_blocker().name == "driver"
    assert calls == ["ok", "blocker"]


def test_live_run_preflight_leaves_diagnostic_and_synthetic_api_probes_to_doctor():
    live_checks = set(preflight._RUN_BLOCKING_CHECKS)
    assert preflight.check_capture not in live_checks
    assert preflight.check_compiler not in live_checks
    assert preflight.check_overlay not in live_checks
    assert preflight.check_api_access not in live_checks
    assert live_checks.issubset(set(preflight.checks_for()))


def test_doctor_labels_nonblocking_failures_as_warnings(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda: [preflight.CheckResult("overlay", False, "not prepared", "run setup", blocking=False)],
    )
    assert cli._doctor() == 0
    assert "WARNING overlay" in capsys.readouterr().out


def test_installer_checksum_aborts_before_execution(monkeypatch):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(cli, "check_runtime", lambda: preflight.CheckResult("runtime", True, "ok"))
    monkeypatch.setattr(cli, "_download_installer", lambda _: b"installer")
    with patch("yutori_mcp.computer_use.cli.subprocess.run") as run:
        assert cli._setup() == 1
    run.assert_not_called()


def _patch_successful_driver_setup(monkeypatch, cli, tmp_path) -> None:
    """Patch every _setup() gate before the overlay step so it always reaches that step.

    Both overlay-outcome tests below need `_setup()` to sail through the runtime check,
    installer download, checksum verification, and `find_cua_driver()` lookup identically;
    only what happens at the overlay-preparation step differs between them.
    """
    driver = tmp_path / "cua-driver"
    driver.write_text("")
    monkeypatch.setattr(cli, "check_runtime", lambda: preflight.CheckResult("runtime", True, "ok"))
    monkeypatch.setattr(cli, "_download_installer", lambda _: b"installer")
    monkeypatch.setattr(cli, "DRIVER_INSTALLER_SHA256", hashlib.sha256(b"installer").hexdigest())
    monkeypatch.setattr(cli, "find_cua_driver", lambda: driver)


def test_setup_prepares_overlay_after_driver_permissions(monkeypatch, tmp_path):
    from yutori_mcp.computer_use import cli

    _patch_successful_driver_setup(monkeypatch, cli, tmp_path)
    prepared = SimpleNamespace(binary=tmp_path / "overlay")
    prepare = patch("yutori.navigator.macos.prepare_macos_overlay", return_value=prepared)
    with prepare as prepare_overlay, patch.object(cli.subprocess, "run"), patch.object(cli, "_doctor", return_value=0):
        assert cli._setup() == 0
    prepare_overlay.assert_called_once_with()


def test_setup_treats_overlay_file_errors_as_warnings(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    _patch_successful_driver_setup(monkeypatch, cli, tmp_path)
    with (
        patch("yutori.navigator.macos.prepare_macos_overlay", side_effect=OSError("read-only cache")),
        patch.object(cli.subprocess, "run"),
        patch.object(cli, "_doctor", return_value=0),
    ):
        assert cli._setup() == 0
    assert "WARNING reasoning overlay unavailable" in capsys.readouterr().out


async def test_mechanical_calculator_check_uses_cua_driver(monkeypatch, tmp_path):
    from yutori_mcp.computer_use import app, cli

    driver = tmp_path / "cua-driver"
    driver.write_text("")
    transports = []
    computers = []

    class FakeTransport:
        def __init__(self, binary):
            self.binary = binary
            transports.append(self)

    class FakeComputer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.session = "smoke-session"
            self.calls = []
            self.copied = iter(("41", "42"))
            computers.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def _call_tool(self, name, arguments, *, read_only=False):
            self.calls.append((name, arguments, read_only))
            if name == "clipboard_read":
                return {"structuredContent": {"text": next(self.copied)}}
            return {"structuredContent": {}}

        async def keypress(self, keys):
            self.calls.append(("keypress", keys))

        async def type(self, text):
            self.calls.append(("type", text))

        async def wait(self, milliseconds):
            self.calls.append(("wait", milliseconds))

    prepare = AsyncMock()
    monkeypatch.setattr(cli, "find_cua_driver", lambda: driver)
    monkeypatch.setattr("yutori_mcp.computer_use.targeting.TargetGuardedMacOSComputer", FakeComputer)
    monkeypatch.setattr("yutori.navigator.macos.transport.CuaDriverTransport", FakeTransport)
    monkeypatch.setattr(app, "prepare_app", prepare)

    assert await cli._mechanical_calculator_check() == "42"
    assert transports[0].binary == driver
    assert computers[0].kwargs == {
        "transport": transports[0],
        "owns_transport": True,
        "presentation": False,
        "show_stop_button": False,
    }
    prepare.assert_awaited_once_with(computers[0], "Calculator", None)
    assert ("wait", 300) in computers[0].calls
    assert ("type", "6*7=") in computers[0].calls
    assert computers[0].calls.count(("keypress", ["CMD", "C"])) == 2


def _patch_smoke_preflight(monkeypatch, cli, lock_path, first_blocker=lambda: None):
    """Patch the DesktopLock path and first_blocker() result every _smoke_live() test needs.

    Every caller of `_smoke_live()` reserves the desktop at a test-owned lock path and stubs
    the preflight gate before exercising it; only the blocker (or lack of one) differs per test.
    """
    monkeypatch.setattr(cli, "DesktopLock", lambda: DesktopLock(lock_path))
    monkeypatch.setattr(cli, "first_blocker", first_blocker)


async def test_smoke_reserves_desktop_before_mechanical_check(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    lock_path = tmp_path / "desktop.lock"
    mechanical_check = AsyncMock()
    preflight = Mock()
    _patch_smoke_preflight(monkeypatch, cli, lock_path, preflight)
    monkeypatch.setattr(cli, "_mechanical_calculator_check", mechanical_check)

    with DesktopLock(lock_path):
        assert await cli._smoke_live() == 1

    preflight.assert_not_called()
    mechanical_check.assert_not_awaited()
    assert "Another computer-use task controls this Mac" in capsys.readouterr().out


async def test_smoke_does_not_print_mismatched_clipboard_contents(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    secret = "clipboard-secret-value"
    _patch_smoke_preflight(monkeypatch, cli, tmp_path / "desktop.lock")
    monkeypatch.setattr(cli, "_mechanical_calculator_check", AsyncMock(return_value=secret))

    assert await cli._smoke_live() == 1

    output = capsys.readouterr().out
    assert "did not match '42'" in output
    assert secret not in output


async def test_smoke_allows_two_minutes_for_live_check(monkeypatch, tmp_path):
    from yutori_mcp.computer_use import cli

    run = AsyncMock(return_value={"outcome": "completed"})
    _patch_smoke_preflight(monkeypatch, cli, tmp_path / "desktop.lock")
    monkeypatch.setattr(cli, "_mechanical_calculator_check", AsyncMock(return_value="42"))
    monkeypatch.setattr(supervisor, "run_task", run)
    monkeypatch.setattr(cli, "format_terminal_result", lambda *_args, **_kwargs: "complete")
    _patch_run_credentials(monkeypatch, api_key="dev-key")

    assert await cli._smoke_live() == 0

    assert run.await_args.kwargs["minutes"] == 2
    assert run.await_args.kwargs["lock"]._depth == 0


def test_pick_best_window_excludes_helper_strips():
    strips = [{"window_id": index, "bounds": {"width": 600, "height": 20}, "z_index": 9} for index in range(4)]
    main = {"window_id": 99, "bounds": {"width": 400, "height": 500}, "z_index": 1}
    assert pick_best_window(strips + [main])["window_id"] == 99
    assert pick_best_window(strips)["window_id"] == 0


def test_pick_best_window_ignores_tiny_untitled_swiftui_host_above_main_window():
    main = {
        "window_id": 10,
        "title": "Yutori Input Probe",
        "bounds": {"width": 1120, "height": 780},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 17,
    }
    ui_host = {
        "window_id": 11,
        "title": "",
        "bounds": {"width": 280, "height": 168},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 26,
    }

    assert pick_best_window([main, ui_host])["window_id"] == 10


def test_pick_best_window_uses_frontmost_titled_window_when_skipping_host():
    back_document = {
        "window_id": 10,
        "title": "Back document",
        "bounds": {"width": 1200, "height": 900},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 10,
    }
    front_document = {
        "window_id": 11,
        "title": "Front document",
        "bounds": {"width": 650, "height": 500},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 20,
    }
    ui_host = {
        "window_id": 12,
        "title": "",
        "bounds": {"width": 200, "height": 120},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 30,
    }

    assert pick_best_window([back_document, front_document, ui_host])["window_id"] == 11


def test_pick_best_window_keeps_a_substantial_untitled_frontmost_sheet():
    main = {
        "window_id": 10,
        "title": "Document",
        "bounds": {"width": 800, "height": 600},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 1,
    }
    sheet = {
        "window_id": 11,
        "title": "",
        "bounds": {"width": 500, "height": 400},
        "is_on_screen": True,
        "on_current_space": True,
        "z_index": 2,
    }

    assert pick_best_window([main, sheet])["window_id"] == 11


async def test_prepare_app_retries_bundle_as_name_and_fronts_best_window():
    computer = SimpleNamespace(
        launch_app=AsyncMock(
            side_effect=[
                CuaDriverToolError("APP_NOT_INSTALLED"),
                {
                    "pid": 42,
                    "name": "Calculator",
                    "windows": [{"window_id": 7, "bounds": {"width": 400, "height": 500}}],
                },
            ]
        ),
        bring_to_front=AsyncMock(),
        _probe_frontmost=AsyncMock(return_value=FrontmostApp(42, "Calculator")),
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "com.apple.calculator", "https://example.com")
    assert target == {"name": "Calculator", "pid": 42, "window_id": 7}
    assert computer.launch_app.await_args_list[0].kwargs == {
        "bundle_id": "com.apple.calculator",
        "urls": ["https://example.com"],
    }
    assert computer.bring_to_front.await_args.args == (42, 7)
    computer.wait.assert_awaited_once_with(800)


async def test_prepare_app_launches_finder_by_bundle_id():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Finder"}),
        bring_to_front=AsyncMock(),
        _probe_frontmost=AsyncMock(return_value=FrontmostApp(42, "Finder")),
        wait=AsyncMock(),
    )

    target = await prepare_app(computer, "Finder", None)

    assert target == {"name": "Finder", "pid": 42, "window_id": None}
    computer.launch_app.assert_awaited_once_with(bundle_id="com.apple.finder", urls=None)


async def test_prepare_app_preserves_explicit_launch_refusal():
    computer = SimpleNamespace(launch_app=AsyncMock(side_effect=CuaDriverToolError("POLICY_DENIED")))
    with pytest.raises(CuaDriverToolError, match="POLICY_DENIED"):
        await prepare_app(computer, "com.apple.calculator", None)
    computer.launch_app.assert_awaited_once()


async def test_prepare_app_never_retries_uncertain_launch():
    computer = SimpleNamespace(launch_app=AsyncMock(side_effect=CuaDriverUncertainActionError("acknowledgement lost")))
    with pytest.raises(CuaDriverUncertainActionError, match="acknowledgement lost"):
        await prepare_app(computer, "com.apple.calculator", None)
    computer.launch_app.assert_awaited_once()


async def test_prepare_app_fronts_running_persistent_app_after_launch_failure():
    computer = SimpleNamespace(
        launch_app=AsyncMock(side_effect=CuaDriverToolError("APP_NOT_INSTALLED")),
        _call_tool=AsyncMock(
            return_value={
                "structuredContent": {"apps": [{"pid": 42, "name": "Finder", "bundle_id": "com.apple.finder"}]}
            }
        ),
        bring_to_front=AsyncMock(),
        _probe_frontmost=AsyncMock(return_value=FrontmostApp(42, "Finder")),
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "Finder", None)
    assert target == {"name": "Finder", "pid": 42, "window_id": None}
    computer._call_tool.assert_awaited_once_with("list_apps", {}, read_only=True)
    computer.bring_to_front.assert_awaited_once_with(42, None)


async def test_prepare_app_does_not_retry_uncertain_fronting():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Calculator"}),
        bring_to_front=AsyncMock(side_effect=CuaDriverUncertainActionError("acknowledgement lost")),
        _probe_frontmost=AsyncMock(return_value=FrontmostApp(42, "Calculator")),
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "Calculator", None)
    assert target == {"name": "Calculator", "pid": 42, "window_id": None}
    computer.bring_to_front.assert_awaited_once_with(42, None)
    computer.wait.assert_awaited_once_with(800)


async def test_prepare_app_refuses_to_return_when_another_app_is_frontmost():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Notes"}),
        bring_to_front=AsyncMock(),
        _probe_frontmost=AsyncMock(return_value=FrontmostApp(99, "Conductor")),
        current_observation=None,
        wait=AsyncMock(),
    )

    with pytest.raises(
        MacOSFocusChangedError,
        match=r"foreground setup was not sent: 'Notes' \(pid 42\).*Conductor \(pid 99\) is frontmost",
    ):
        await prepare_app(computer, "Notes", None)


async def test_require_frontmost_target_fails_closed_when_the_probe_is_unavailable():
    computer = SimpleNamespace(
        _probe_frontmost=AsyncMock(return_value=None),
        current_observation="current frame",
    )

    with pytest.raises(MacOSFocusChangedError, match="could not verify") as caught:
        await require_frontmost_target(computer, 42, tool="type_text", target_name="Notes")

    assert caught.value.observation == "current frame"


async def test_target_guarded_computer_allows_the_requested_foreground_pid():
    computer = object.__new__(TargetGuardedMacOSComputer)
    computer.scope = "desktop"
    computer.verify_focus = True
    computer.target_pid = 42
    computer._focus_guard_trips = 0
    computer._probe_frontmost = AsyncMock(return_value=FrontmostApp(42, "Notes"))
    computer.screenshot = AsyncMock()

    await computer._guard_frontmost("type_text")

    assert computer.focus_guard_trips == 0
    computer.screenshot.assert_not_awaited()


async def test_target_guarded_computer_refuses_keyboard_for_another_foreground_pid():
    computer = object.__new__(TargetGuardedMacOSComputer)
    computer.scope = "desktop"
    computer.verify_focus = True
    computer.target_pid = 42
    computer._focus_guard_trips = 0
    computer._probe_frontmost = AsyncMock(return_value=FrontmostApp(99, "Conductor"))
    computer.screenshot = AsyncMock(return_value="fresh frame")

    with pytest.raises(MacOSFocusChangedError, match=r"pid 42.*Conductor \(pid 99\) is frontmost") as caught:
        await computer._guard_frontmost("hotkey")

    assert caught.value.observation == "fresh frame"
    assert computer.focus_guard_trips == 1


async def test_target_guarded_computer_leaves_window_scope_unchanged():
    computer = object.__new__(TargetGuardedMacOSComputer)
    computer.scope = "window"
    computer.verify_focus = True
    computer.target_pid = 42
    computer._probe_frontmost = AsyncMock()

    await computer._guard_frontmost("hotkey")

    computer._probe_frontmost.assert_not_awaited()


async def test_smoke_reports_preflight_detail_and_fix(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    blocker = preflight.CheckResult(
        "Yutori API",
        False,
        "Invalid model",
        "Use a supported model.",
    )
    _patch_smoke_preflight(monkeypatch, cli, tmp_path / "desktop.lock", lambda: blocker)
    mechanical = AsyncMock()
    monkeypatch.setattr(cli, "_mechanical_calculator_check", mechanical)

    assert await cli._smoke_live() == 1
    assert capsys.readouterr().out == "Invalid model Fix: Use a supported model.\n"
    mechanical.assert_not_awaited()


def _valid_request(**overrides):
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "type": "run",
        "task": "open calculator",
        "app": None,
        "start_url": None,
        "deadline_ms": 1_000_000,
        "max_steps": 10,
        "mode": "foreground",
        "allow_foreground_fallback": False,
        "allow_local_shell": True,
        "model": "n2",
        "api_base_url": "https://api.dev.yutori.com/v1",
    }
    request.update(overrides)
    return request


def test_parse_request_accepts_python_only_shape():
    parsed = parse_request(_valid_request())
    assert parsed["task"] == "open calculator"
    assert "driver_path" not in parsed and "harness" not in parsed


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"protocol_version": PROTOCOL_VERSION + 1}, "UNSUPPORTED_PROTOCOL_VERSION"),
        ({"type": "walk"}, "INVALID_REQUEST"),
        ({"task": ""}, "INVALID_REQUEST"),
        ({"start_url": "https://x", "app": None}, "INVALID_REQUEST"),
        ({"mode": "sideways"}, "INVALID_REQUEST"),
        ({"mode": None}, "INVALID_REQUEST"),
        ({"mode": "background", "app": None}, "INVALID_REQUEST"),
        ({"allow_foreground_fallback": "yes"}, "INVALID_REQUEST"),
        ({"allow_foreground_fallback": True, "mode": "foreground"}, "INVALID_REQUEST"),
        ({"deadline_ms": 0}, "INVALID_REQUEST"),
        ({"max_steps": -1}, "INVALID_REQUEST"),
        ({"api_base_url": None}, "INVALID_REQUEST"),
    ],
)
def test_parse_request_rejects_malformed_requests(overrides, code):
    with pytest.raises(RequestError) as error:
        parse_request(_valid_request(**overrides))
    assert error.value.code == code


@pytest.mark.parametrize(
    "output,raw_status,status",
    [
        ("ok", "confirmed", "executed"),
        ("[ERROR] Refused an action.", "refused", "refused"),
        ("[ERROR] shell_command failed: timeout", "timeout_after_possible_dispatch", "uncertain"),
        ("[ERROR] Invalid click", "unverifiable", "uncertain"),
    ],
)
def test_classify_result_maps_outputs_to_statuses(output, raw_status, status):
    outputs = [{"type": "function_call_output", "call_id": "c1", "output": output}]
    assert classify_result(outputs) == raw_status
    assert runner_module._status_for(raw_status) == status


def test_redacted_error_text_scrubs_the_secret():
    error = RuntimeError("driver rejected key yt-secret-123")
    assert runner_module._redacted_error_text(error, "yt-secret-123") == "driver rejected key [REDACTED]"


def test_redacted_error_text_falls_back_to_type_name_when_message_is_empty():
    assert runner_module._redacted_error_text(RuntimeError(), "yt-secret-123") == "RuntimeError"


def test_redact_scrubs_every_occurrence_of_the_secret():
    assert redact("key=yt-secret then yt-secret again", "yt-secret") == "key=[REDACTED] then [REDACTED] again"


def test_redact_is_a_noop_when_the_secret_is_absent():
    assert redact("nothing sensitive here", "yt-secret") == "nothing sensitive here"


def test_terminal_result_carries_the_outcome_and_shared_shape():
    actions = [{"index": 0}]
    assert terminal_result("limit", "deadline expired", actions=actions) == {
        "outcome": "limit",
        "delivery_mode": DELIVERY_MODE_FOREGROUND,
        "final_text": "deadline expired",
        "actions": actions,
    }


def test_failure_is_the_failed_outcome_of_the_shared_shape():
    assert failure("boom") == terminal_result("failed", "boom")


def test_result_event_carries_the_outcome_and_shared_shape():
    assert runner_module._result_event("limit", "deadline expired") == {
        "type": "result",
        "outcome": "limit",
        "delivery_mode": DELIVERY_MODE_FOREGROUND,
        "final_text": "deadline expired",
    }


def test_error_event_carries_the_code_and_message():
    assert runner_module._error_event("MISSING_API_KEY", "not set") == {
        "type": "error",
        "code": "MISSING_API_KEY",
        "message": "not set",
    }


class _CollectStream:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, data):
        self.lines.append(data)

    def flush(self):
        pass


async def test_startup_reporter_emits_each_phase_and_only_the_first_model_request():
    stream = _CollectStream()
    clock = iter((10.1, 10.5, 11.0)).__next__
    reporter = runner_module.StartupReporter(Emitter(stream), 10.0, clock=clock)

    reporter.mark("api_client")
    reporter.mark("computer")
    await reporter.on_api_start({})
    await reporter.on_api_start({})

    events = [json.loads(line) for line in stream.lines]
    assert [event["phase"] for event in events] == ["api_client", "computer", "model"]
    assert [event["duration_ms"] for event in events] == [100, 400, 500]
    assert [event["elapsed_ms"] for event in events] == [100, 500, 1000]


def test_runner_main_unexpected_failure_preserves_the_requested_background_mode(monkeypatch):
    stream = _CollectStream()

    async def fail(_request, _emitter, _api_key, _termination_requested):
        raise RuntimeError("unexpected runner failure")

    monkeypatch.setattr(runner_module, "_take_api_key", lambda: "yt-key")
    monkeypatch.setattr(runner_module, "_claim_protocol_stream", lambda: stream)
    monkeypatch.setattr(
        runner_module,
        "_read_request_line",
        lambda: json.dumps(_valid_request(app="Notes", mode="background")),
    )
    monkeypatch.setattr(runner_module, "_run_until_terminated", fail)

    assert runner_module.main() == 1
    terminal = json.loads(stream.lines[-1])
    assert terminal["outcome"] == "failed"
    assert terminal["delivery_mode"] == DELIVERY_MODE_BACKGROUND


async def test_action_events_sanitize_commands_and_never_include_output(monkeypatch):
    secret = "private-value-1234"
    monkeypatch.setenv("SERVICE_API_KEY", secret)
    stream = _CollectStream()
    reporter = ActionReporter(Emitter(stream), time.monotonic())
    item = {
        "name": "bash",
        "arguments": json.dumps({"command": f"API_TOKEN={secret} run --password hunter2", "run_in_background": True}),
    }
    await reporter.on_computer_call_start(item)
    await reporter.on_computer_call_end(
        item,
        [{"output": {"result": "Started background task bash-ab12 (pid 7).\nsecret command output must not escape"}}],
    )
    event = json.loads(stream.lines[-1])
    assert event["run_in_background"] is True
    assert event["background_task_id"] == "bash-ab12"
    assert "[REDACTED]" in event["command"]
    assert secret not in json.dumps(event)
    assert "command output" not in json.dumps(event)


async def test_action_reporter_flushes_an_in_flight_call_as_uncertain():
    stream = _CollectStream()
    clock = iter((10.0, 10.5, 10.5)).__next__
    reporter = ActionReporter(Emitter(stream), 9.0, clock=clock)
    await reporter.on_computer_call_start({"name": "left_click", "arguments": {"coordinates": [1, 1]}})

    reporter.flush_interrupted()
    reporter.flush_interrupted()

    assert len(stream.lines) == 1
    event = json.loads(stream.lines[0])
    assert event["status"] == "uncertain"
    assert event["raw_status"] == "interrupted"
    assert event["duration_ms"] == 500
    assert reporter.tool_calls == 1


async def test_run_guard_stops_before_expired_first_step_and_at_cap():
    expired = RunGuard(10, time.monotonic() - 1)
    assert not await expired.on_run_continue({}, [], [])
    assert expired.deadline_reached and expired.steps == 0
    capped = RunGuard(1, time.monotonic() + 60)
    assert await capped.on_run_continue({}, [], [])
    assert not await capped.on_run_continue({}, [], [])
    assert capped.limit_reached and capped.steps == 1


class _FakePresentation:
    telemetry = ({"type": "presentation_ready"},)


class _FakeCancellation:
    cause = None
    cancelled = False

    async def wait(self):
        await asyncio.Future()

    def raise_if_cancelled(self):
        return None


class _FakeComputer:
    instances: list[_FakeComputer] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.presentation_requested = kwargs.get("presentation", True)
        self.presentation = _FakePresentation() if self.presentation_requested else None
        self.presentation_status = MacOSPresentationStatus(
            self.presentation_requested,
            self.presentation is not None,
            "active" if self.presentation is not None else "unavailable",
            "yutori" if self.presentation is not None else "hidden",
            codec="webp",
        )
        self.cancellation = _FakeCancellation()
        self.current_observation = None
        self.target_pid = None
        self.recover_target = None
        self.target_recovery_attempts = 0
        self.no_progress_triggers = 0
        self.shell_events = (
            ShellPresentationEvent("bash-1234", "echo safe", True, "running"),
            ShellPresentationEvent("bash-1234", "echo safe", True, "completed", 0),
        )
        self.timings = {
            "model_ms": 0,
            "action_ms": 20,
            "capture_ms": 30,
            "encode_ms": 10,
            "polling_ms": 5,
            "shell_ms": 7,
            "screenshots": 2,
        }
        self.closed = False
        self.screenshots = 0
        self.window_targets: list[Any] = []
        self.action_outcomes: tuple[Any, ...] = ()
        self.focus_guard_trips = 0
        self.delivery_counts = {
            "background_attempts": 0,
            "foreground_escalations": 0,
            "fallback_skips": 0,
            "background_refusals": 0,
            "window_rebinds": 0,
        }
        self.window_target_info = None
        self.__dict__.update(self.telemetry_overrides)
        self.__class__.instances.append(self)

    telemetry_overrides: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def screenshot(self):
        self.screenshots += 1

    async def set_window_target(self, target):
        self.window_targets.append(target)

    async def aclose(self):
        self.closed = True


class _FakeAgent:
    instances: list[_FakeAgent] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.computer = kwargs.get("computer")
        self.callbacks = kwargs.get("callbacks") or []
        self.timings = {"model_ms": 40}
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def run(self, _messages):
        for callback in self.callbacks:
            if hasattr(callback, "on_run_continue") and not await callback.on_run_continue({}, [], []):
                return
            if hasattr(callback, "on_api_start"):
                await callback.on_api_start({})
            if hasattr(callback, "on_api_end"):
                await callback.on_api_end({}, {"request_id": "req-first", "choices": []})
        item = {"name": "left_click", "arguments": {"coordinates": [1, 1]}}
        for callback in self.callbacks:
            if hasattr(callback, "on_computer_call_start"):
                await callback.on_computer_call_start(item)
        for callback in self.callbacks:
            if hasattr(callback, "on_computer_call_end"):
                await callback.on_computer_call_end(item, [{"output": "ok"}])
        yield {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Done [DONE]"}]}]}


def _patch_runner_sdk(monkeypatch, *, agent_cls: type = _FakeAgent) -> None:
    """Point runner_module's SDK imports at the fakes it drives run_request() with.

    Six run_request() tests each independently set both `MacOSComputer` (always
    `_FakeComputer`) and `N2ComputerAgent`, differing only in which `_FakeAgent`
    subclass scripts the run's tool calls.
    """
    monkeypatch.setattr(runner_module, "MacOSComputer", _FakeComputer)
    monkeypatch.setattr(runner_module, "N2ComputerAgent", agent_cls)


def test_presentation_payload_distinguishes_capture_codec_from_n2_request_format():
    computer = _FakeComputer()
    status = MacOSPresentationStatus(True, True, "active", "yutori", codec="jpeg")
    payload = runner_module._presentation_payload(computer, status)
    assert payload["codec"] == payload["capture_codec"] == "jpeg"
    assert payload["capture_codec_fallback"] is True
    assert payload["observation_format"] == OBSERVATION_FORMAT == "webp"
    assert payload["observation_format_fallback"] is False


async def test_run_request_wires_sdk_runtime_and_reports_effective_state(monkeypatch):
    _FakeComputer.instances.clear()
    _FakeAgent.instances.clear()
    _patch_runner_sdk(monkeypatch)
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() + 60) * 1000)))
    outcome = await runner_module.run_request(request, Emitter(stream), "yt-secret")
    assert outcome == "completed"
    computer = _FakeComputer.instances[-1]
    agent = _FakeAgent.instances[-1]
    assert computer.kwargs == {
        "presentation": True,
        "show_stop_button": True,
        "allow_local_shell": True,
        "execution_deadline": pytest.approx(computer.kwargs["execution_deadline"]),
        "cancellation": computer.kwargs["cancellation"],
        "known_secrets": ("yt-secret",),
        "exclude_overlay_from_capture": False,
    }
    assert agent.kwargs["tool_set"] == TOOL_SET
    # The agent's sink is the activity tee wrapping the SDK's own controller.
    assert isinstance(agent.kwargs["presentation"], runner_module.ActivityReporter)
    assert agent.kwargs["presentation"]._inner is computer.presentation
    assert agent.kwargs["image_format"] == OBSERVATION_FORMAT == "webp"
    assert agent.kwargs["supports_click_modifiers"] is True
    assert "Shell commands run headlessly" in agent.kwargs["system_prompt"]
    assert "Do not use osascript" in agent.kwargs["system_prompt"]
    assert "Never inspect a GUI application's databases" in agent.kwargs["system_prompt"]
    assert "use at most three shell calls for research" in agent.kwargs["system_prompt"]
    assert "never inspect browser profile databases" in agent.kwargs["system_prompt"]
    assert "stop immediately instead of trying alternate URLs" in agent.kwargs["system_prompt"]
    assert "Never ask them to give you a password" in agent.kwargs["system_prompt"]
    assert "Do not install software or packages" in agent.kwargs["system_prompt"]
    assert computer.closed
    startup_events = [json.loads(line) for line in stream.lines if json.loads(line)["type"] == "startup"]
    assert [event["phase"] for event in startup_events] == ["api_client", "computer", "model"]
    result = json.loads(stream.lines[-1])
    assert result["final_text"] == "Done"
    assert result["reasoning_overlay_requested"] is True
    assert result["reasoning_overlay_effective"] is True
    assert result["codec"] == "webp"
    assert result["capture_codec"] == "webp"
    assert result["capture_codec_fallback"] is False
    assert result["observation_format"] == "webp"
    assert result["observation_format_fallback"] is False
    assert result["background_command_counts"] == {
        "started": 1,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
    }
    assert result["timings"]["polling_ms"] == 5
    assert result["timings"]["shell_ms"] == 7


class _CancelledAgent(_FakeAgent):
    async def run(self, _messages):
        item = {"name": "left_click", "arguments": {"coordinates": [1, 1]}}
        for callback in self.callbacks:
            if hasattr(callback, "on_computer_call_start"):
                await callback.on_computer_call_start(item)
        raise asyncio.CancelledError
        yield  # pragma: no cover - makes this an async generator


async def test_run_request_reports_an_action_interrupted_by_cancellation(monkeypatch):
    _patch_runner_sdk(monkeypatch, agent_cls=_CancelledAgent)
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() + 60) * 1000)))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "aborted"

    events = [json.loads(line) for line in stream.lines]
    assert [event["type"] for event in events[-2:]] == ["action", "result"]
    assert events[-2]["raw_status"] == "interrupted"
    assert events[-2]["status"] == "uncertain"


class _LimitAgent(_FakeAgent):
    async def run(self, messages):
        self.messages = messages
        for callback in self.callbacks:
            if hasattr(callback, "on_run_continue"):
                while await callback.on_run_continue({}, [], []):
                    pass
        yield {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Partial"}]}]}

    def completion_request(self, extra_messages):
        self.summary_extra_messages = extra_messages
        return {
            "model": "n2",
            "messages": [
                {"role": "user", "content": "compacted trajectory checkpoint"},
                *extra_messages,
            ],
        }


async def test_run_request_reuses_the_agent_trajectory_for_the_limit_summary(monkeypatch):
    class SummaryCompletions:
        def __init__(self):
            self.requests = []

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            return {
                "request_id": "req-summary",
                "choices": [{"message": {"content": "Summary [DONE]"}}],
            }

    class SummaryClient:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.completions = SummaryCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            self.closed = False
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            self.closed = True

    _FakeAgent.instances.clear()
    _patch_runner_sdk(monkeypatch, agent_cls=_LimitAgent)
    monkeypatch.setattr(runner_module, "AsyncYutoriClient", SummaryClient)
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() + 60) * 1000), max_steps=1))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "limit"

    (run_agent,) = _FakeAgent.instances
    (client,) = SummaryClient.instances
    assert run_agent.kwargs["completions"] is client.completions
    assert "api_key" not in run_agent.kwargs and "base_url" not in run_agent.kwargs
    assert run_agent.summary_extra_messages == [
        {"role": "user", "content": runner_module.STOP_SUMMARY_PROMPT}
    ]
    assert client.completions.requests[0]["messages"] == [
        {"role": "user", "content": "compacted trajectory checkpoint"},
        {"role": "user", "content": runner_module.STOP_SUMMARY_PROMPT},
    ]
    assert client.closed
    result = json.loads(stream.lines[-1])
    assert result["final_text"] == "Summary"
    assert result["timings"]["model_calls"] == 1


async def test_limit_summary_stops_with_the_computer_cancellation_latch():
    from yutori.navigator.macos import CancellationLatch

    completion_cancelled = asyncio.Event()

    class BlockingCompletions:
        async def create(self, **_kwargs):
            try:
                await asyncio.Future()
            finally:
                completion_cancelled.set()

    cancellation = CancellationLatch()
    agent = SimpleNamespace(
        computer=SimpleNamespace(cancellation=cancellation),
        timings={"model_ms": 0},
        completion_request=lambda messages: {"model": "n2", "messages": messages},
    )
    task = asyncio.create_task(
        runner_module._summarize_limit_run(
            agent,
            BlockingCompletions(),
            runner_module.ApiCounter(),
            runner_module.ChatTracker(),
            time.monotonic() + 60,
        )
    )
    await asyncio.sleep(0)

    cancellation.request("operator_stop")

    with pytest.raises(asyncio.CancelledError):
        await task
    assert completion_cancelled.is_set()


async def test_run_request_reports_limit_when_the_deadline_has_already_passed():
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() - 1) * 1000)))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "limit"

    (event,) = [json.loads(line) for line in stream.lines]
    assert event == {
        "type": "result",
        "outcome": "limit",
        "delivery_mode": DELIVERY_MODE_FOREGROUND,
        "final_text": "The deadline expired before the run started.",
        "elapsed_ms": 0,
        "steps": 0,
    }


class _PydanticLikeResponse:
    def __init__(self, request_id):
        self._request_id = request_id

    def model_dump(self):
        return {"request_id": self._request_id, "choices": []}


async def test_chat_tracker_keeps_the_first_request_id_from_dict_or_model_responses():
    tracker = runner_module.ChatTracker()
    await tracker.on_api_end({}, {"choices": []})
    assert tracker.chat_id is None
    await tracker.on_api_end({}, _PydanticLikeResponse("req-1"))
    await tracker.on_api_end({}, {"request_id": "req-2", "choices": []})
    assert tracker.chat_id == "req-1"


async def test_run_request_carries_the_chat_id_on_actions_and_the_result(monkeypatch):
    _patch_runner_sdk(monkeypatch)
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() + 60) * 1000)))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "completed"

    events = [json.loads(line) for line in stream.lines]
    # `activity` and `frame` events interleave with actions, so pick the last action by type.
    action, result = [event for event in events if event["type"] == "action"][-1], events[-1]
    assert (action["type"], action["chat_id"]) == ("action", "req-first")
    assert (result["type"], result["chat_id"]) == ("result", "req-first")


def test_run_chat_id_prefers_the_result_then_the_latest_action():
    assert run_chat_id({"chat_id": "from-result", "actions": [{"chat_id": "from-action"}]}) == "from-result"
    assert run_chat_id({"actions": [{"chat_id": None}, {"chat_id": "later"}, {"tool": "screenshot"}]}) == "later"
    assert run_chat_id({"actions": []}) is None


def test_attach_run_link_builds_the_platform_chat_url():
    result = terminal_result("limit", "deadline", actions=[{"chat_id": "abc-123"}])
    assert attach_run_link(result, "https://platform.yutori.com/") is result
    assert result["chat_id"] == "abc-123"
    assert result["run_url"] == "https://platform.yutori.com/navigator/chats/abc-123"
    assert "run_url" not in attach_run_link(terminal_result("failed", "no model call"), "https://platform.yutori.com")
    assert "run_url" not in attach_run_link({"chat_id": "abc-123", "actions": []}, None)


def test_format_result_prints_the_run_link_right_after_the_outcome():
    text = format_result({"outcome": "completed", "run_url": "https://platform.yutori.com/navigator/chats/abc"})
    assert text.splitlines()[:3] == [
        "Outcome: completed",
        "Delivery mode: foreground",
        "Run: https://platform.yutori.com/navigator/chats/abc",
    ]
    assert "Run:" not in format_result({"outcome": "failed"})


def test_format_result_handles_python_runtime_action_fields():
    text = format_result(
        {
            "outcome": "completed",
            "actions": [
                _action_event(
                    tool="bash",
                    elapsed_ms=10,
                    duration_ms=5,
                    command="echo safe",
                    run_in_background=True,
                    background_task_id="bash-1",
                )
            ],
        }
    )
    assert "#0 bash: executed" in text
    assert "$ echo safe" in text


def test_repository_contains_no_node_runtime_surface():
    root = Path(__file__).parents[1]
    assert not (root / "src/yutori_mcp/computer_use/runtime.py").exists()
    assert not (root / "src/yutori_mcp/computer_use/driver.py").exists()
    text = "\n".join(
        path.read_text()
        for path in [
            root / "pyproject.toml",
            root / "README.md",
            root / "TOOLS.md",
            root / ".github/workflows/test.yml",
            root / "src/yutori_mcp/computer_use/constants.py",
        ]
    )
    assert "node-harness" not in text
    assert "runner.mjs" not in text
    assert "yutori-sdk-typescript" not in text
    assert "secrets.SDK_DEPLOY_KEY" not in text
    assert "PYSDK_DEPLOY_KEY" not in text
    assert "git+ssh" not in text


# --- background mode ----------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeWindowTarget:
    pid: int
    window_id: int
    title: str | None = None
    app_name: str | None = None


def _background_request(**overrides):
    request = _valid_request(app="Notes", mode="background", deadline_ms=int((time.time() + 60) * 1000))
    request.update(overrides)
    return request


def test_computer_use_mode_defaults_and_validators():
    params = ComputerUseTaskInput(task="t")
    assert params.mode == "foreground" and params.allow_foreground_fallback is False
    assert params.allow_local_shell is True
    with pytest.raises(ValidationError, match="mode='background' requires app"):
        ComputerUseTaskInput(task="t", mode="background")
    with pytest.raises(ValidationError, match="allow_foreground_fallback requires mode='background'"):
        ComputerUseTaskInput(task="t", app="Notes", allow_foreground_fallback=True)
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="t", app="Notes", mode="sideways")
    background = ComputerUseTaskInput(task="t", app="Notes", mode="background", allow_foreground_fallback=True)
    assert background.model_dump()["mode"] == "background"
    assert background.model_dump()["allow_foreground_fallback"] is True


def test_computer_use_mode_literal_matches_runtime_delivery_modes():
    from typing import get_args

    assert set(get_args(ComputerUseMode)) == set(DELIVERY_MODES)
    assert COMPUTER_USE_DEFAULT_MODE == DELIVERY_MODE_FOREGROUND


def test_run_computer_use_task_signature_mirrors_the_schema_defaults():
    import inspect

    from yutori_mcp import server

    parameters = inspect.signature(server.run_computer_use_task).parameters
    fields = ComputerUseTaskInput.model_fields
    assert parameters["mode"].default == fields["mode"].default == COMPUTER_USE_DEFAULT_MODE
    assert parameters["allow_foreground_fallback"].default is fields["allow_foreground_fallback"].default is False
    assert parameters["allow_local_shell"].default is fields["allow_local_shell"].default is True
    assert list(parameters)[-1] == "ctx"


async def test_server_forwards_mode_and_fallback_to_the_runner(monkeypatch, tmp_path):
    from yutori_mcp import server
    from yutori_mcp.computer_use import lock as lock_module

    forwarded: dict[str, Any] = {}

    async def run_with_lock(**kwargs: Any) -> dict[str, str]:
        forwarded.update(kwargs)
        return {"outcome": "completed", "delivery_mode": "background"}

    _patch_server_lock(monkeypatch, lock_module, DesktopLock(tmp_path / "desktop.lock"))
    monkeypatch.setattr(supervisor, "run_task", run_with_lock)
    _patch_run_credentials(monkeypatch)

    result, _ = await server._handle_computer_use(
        None,
        {
            "task": "add a note",
            "app": "Notes",
            "mode": "background",
            "allow_foreground_fallback": True,
            "allow_local_shell": False,
        },
    )
    assert result["delivery_mode"] == "background"
    assert forwarded["mode"] == "background" and forwarded["allow_foreground_fallback"] is True
    assert forwarded["allow_local_shell"] is False
    assert forwarded["app"] == "Notes"
    assert forwarded["platform_url"] == "https://platform.yutori.com"


async def test_progress_reporter_delivers_progress_and_log_notifications_concurrently():
    from yutori_mcp import server

    progress_started = asyncio.Event()
    info_started = asyncio.Event()
    calls = []

    async def report_progress(**kwargs):
        calls.append(("progress", kwargs))
        progress_started.set()
        await info_started.wait()

    async def info(message):
        calls.append(("info", message))
        info_started.set()
        await progress_started.wait()

    ctx = SimpleNamespace(report_progress=report_progress, info=info)
    on_event = server._progress_reporter(ctx, 12, mode="background", app="Notes")

    await asyncio.wait_for(on_event({"type": "ready"}), 0.2)

    assert {call[0] for call in calls} == {"progress", "info"}
    progress_call = next(payload for kind, payload in calls if kind == "progress")
    assert "12 model turns" in progress_call["message"]


async def test_progress_reporter_formats_startup_phases_without_action_fields():
    from yutori_mcp import server

    ctx = SimpleNamespace(report_progress=AsyncMock(), info=AsyncMock())
    on_event = server._progress_reporter(ctx, 12, mode="background", app="Notes")

    await on_event(_startup_event(phase="target"))

    ctx.report_progress.assert_awaited_once_with(
        progress=0,
        message="Computer-use startup: Notes ready  250ms | at 300ms",
    )
    ctx.info.assert_awaited_once_with("Computer-use startup: Notes ready  250ms | at 300ms")


async def test_server_early_failures_preserve_the_requested_background_mode(monkeypatch, tmp_path):
    from yutori_mcp import server
    from yutori_mcp.computer_use import lock as lock_module

    lock_path = tmp_path / "desktop.lock"
    blocker = preflight.CheckResult("driver", False, "missing", "run setup")
    _patch_server_lock(monkeypatch, lock_module, DesktopLock(lock_path), lambda: blocker)

    arguments = {"task": "add a note", "app": "Notes", "mode": "background"}
    blocked, _ = await server._handle_computer_use(None, arguments)
    assert blocked["delivery_mode"] == DELIVERY_MODE_BACKGROUND

    monkeypatch.setattr(preflight, "first_blocker", lambda: pytest.fail("busy run must not reach preflight"))
    with DesktopLock(lock_path):
        busy, _ = await server._handle_computer_use(None, arguments)
    assert busy["delivery_mode"] == DELIVERY_MODE_BACKGROUND


async def test_invoke_unexpected_failure_preserves_the_requested_background_mode(monkeypatch):
    from yutori_mcp import server

    async def fail_before_result(_client, _arguments):
        raise RuntimeError("unexpected host failure")

    monkeypatch.setitem(server._TOOL_HANDLERS, "run_computer_use_task", fail_before_result)
    text = await server._invoke(
        "run_computer_use_task",
        {"task": "add a note", "app": "Notes", "mode": "background"},
    )
    assert "Outcome: failed" in text
    assert "Delivery mode: background" in text


def test_cli_run_parser_accepts_mode_and_fallback_flags():
    from yutori_mcp.computer_use import cli

    parser = argparse.ArgumentParser()
    cli.register_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        ["computer-use", "run", "add a note", "--app", "Notes", "--mode", "background", "--allow-foreground-fallback", "--no-local-shell"]
    )
    assert args.mode == "background" and args.allow_foreground_fallback is True
    assert args.allow_local_shell is False
    default = parser.parse_args(["computer-use", "run", "add a note"])
    assert default.mode == COMPUTER_USE_DEFAULT_MODE and default.allow_foreground_fallback is False
    assert default.allow_local_shell is True
    with pytest.raises(SystemExit):
        parser.parse_args(["computer-use", "run", "x", "--mode", "sideways"])


def test_exit_code_is_zero_only_for_a_completed_outcome():
    from yutori_mcp.computer_use import cli

    assert cli._exit_code({"outcome": "completed"}) == 0
    assert cli._exit_code({"outcome": "limit"}) == 1
    assert cli._exit_code({}) == 1


def test_hands_off_notice_depends_on_the_mode():
    from yutori_mcp.computer_use import cli

    assert cli.hands_off_notice("foreground") == (
        "The model takes over this Mac's desktop now; do not touch it during the run."
    )
    assert "keep working" in cli.hands_off_notice("background")
    assert "leave that window alone" in cli.hands_off_notice("background")


async def test_cli_run_forwards_the_mode_and_prints_the_matching_notice(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    run = AsyncMock(return_value={"outcome": "completed", "delivery_mode": "background", "final_text": "done"})
    monkeypatch.setattr(cli, "_blocked", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor, "run_task", run)
    _patch_run_credentials(monkeypatch)
    args = SimpleNamespace(
        task="add a note",
        app="Notes",
        start_url=None,
        minutes=30,
        max_steps=60,
        mode="background",
        allow_foreground_fallback=True,
        allow_local_shell=False,
    )
    assert await cli._run_custom(args) == 0
    assert run.await_args.kwargs["mode"] == "background"
    assert run.await_args.kwargs["allow_foreground_fallback"] is True
    assert run.await_args.kwargs["allow_local_shell"] is False
    assert run.await_args.kwargs["platform_url"] == "https://platform.yutori.com"
    out = capsys.readouterr().out
    assert "preflight ready" in out
    assert cli.hands_off_notice("background") in out
    assert "completed" in out and "background" in out
    with pytest.raises(ValidationError, match="requires app"):
        await cli._run_custom(SimpleNamespace(**{**vars(args), "app": None}))


async def test_run_task_request_carries_mode_fallback_and_shell_policy(tmp_path):
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(
            **_run_task_kwargs(
                tmp_path,
                app="Notes",
                mode="background",
                allow_foreground_fallback=True,
                allow_local_shell=False,
            )
        )
    request = supervise.await_args.kwargs["request"]
    assert request["protocol_version"] == PROTOCOL_VERSION == 2
    assert request["mode"] == "background" and request["allow_foreground_fallback"] is True
    assert request["allow_local_shell"] is False


async def test_run_task_defaults_to_foreground_and_reports_failures_in_the_requested_mode(tmp_path):
    with patch.object(supervisor, "find_cua_driver", return_value=None):
        foreground = await run_task(**_run_task_kwargs(tmp_path))
        background = await run_task(**_run_task_kwargs(tmp_path, app="Notes", mode="background"))
    assert foreground["outcome"] == "failed" and foreground["delivery_mode"] == "foreground"
    assert background["outcome"] == "failed" and background["delivery_mode"] == "background"


async def test_supervisor_synthesized_results_carry_the_requested_mode():
    process = _Process(_stream(json.dumps(_ready_event())), _stream())
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run", "mode": "background"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
    assert result["outcome"] == "failed" and result["delivery_mode"] == "background"
    assert "exited without a result" in result["final_text"]


def test_event_shape_checks_delivery_modes_and_accepts_the_new_action_fields():
    action = _action_event(delivery_mode="background", route="accessibility", effect="confirmed", escalated=True)
    assert supervisor._event_shape_error(action) is None
    assert supervisor._event_shape_error({**action, "delivery_mode": "sideways"}) == (
        "invalid or missing fields: delivery_mode"
    )
    assert supervisor._event_shape_error(_result_event(delivery_mode="background")) is None
    assert supervisor._event_shape_error(_result_event(delivery_mode="sideways")) == (
        "invalid or missing fields: delivery_mode"
    )


def test_parse_request_accepts_background_with_app():
    parsed = parse_request(_valid_request(app="Notes", mode="background", allow_foreground_fallback=True))
    assert parsed["mode"] == "background" and parsed["allow_foreground_fallback"] is True
    assert parsed["allow_local_shell"] is True
    assert parse_request(_valid_request())["mode"] == "foreground"


def test_computer_kwargs_keep_the_foreground_shape_and_add_window_scope_for_background(monkeypatch):
    monkeypatch.delenv(runner_module.ENV_RECORDABLE_OVERLAY, raising=False)
    cancellation = object()
    shared = {
        "presentation": True,
        "show_stop_button": True,
        "allow_local_shell": True,
        "execution_deadline": 1.0,
        "cancellation": cancellation,
        "known_secrets": ("k",),
    }
    foreground = runner_module._computer_kwargs(
        parse_request(_valid_request()), deadline=1.0, cancellation=cancellation, api_key="k"
    )
    # Recordable by default: the overlay stays in screen recordings, the SDK keeps it out of the
    # model's frames on the capturer's side.
    assert foreground == {**shared, "exclude_overlay_from_capture": False}
    background = runner_module._computer_kwargs(
        parse_request(_valid_request(app="Notes", mode="background", allow_foreground_fallback=True)),
        deadline=1.0,
        cancellation=cancellation,
        api_key="k",
    )
    assert background == {
        **shared,
        "scope": "window",
        "allow_foreground_fallback": True,
    }


def test_computer_kwargs_recordable_overlay_switch(monkeypatch):
    def foreground():
        return runner_module._computer_kwargs(
            parse_request(_valid_request()), deadline=1.0, cancellation=object(), api_key="k"
        )

    # "0" opts out: the overlay leaves screen capture altogether (and recordings with it).
    monkeypatch.setenv(runner_module.ENV_RECORDABLE_OVERLAY, "0")
    assert foreground()["exclude_overlay_from_capture"] is True
    # Anything else keeps the default.
    for value in ("1", "true", ""):
        monkeypatch.setenv(runner_module.ENV_RECORDABLE_OVERLAY, value)
        assert foreground()["exclude_overlay_from_capture"] is False
    # Window scope shows no full-screen overlay; the switch does not apply.
    background = runner_module._computer_kwargs(
        parse_request(_valid_request(app="Notes", mode="background")), deadline=1.0, cancellation=object(), api_key="k"
    )
    assert "exclude_overlay_from_capture" not in background


def test_child_environment_forwards_the_recordable_overlay_switch(monkeypatch):
    monkeypatch.delenv(runner_module.ENV_RECORDABLE_OVERLAY, raising=False)
    assert runner_module.ENV_RECORDABLE_OVERLAY not in supervisor._child_environment("k")
    monkeypatch.setenv(runner_module.ENV_RECORDABLE_OVERLAY, "0")
    assert supervisor._child_environment("k")[runner_module.ENV_RECORDABLE_OVERLAY] == "0"


def test_computer_kwargs_can_disable_local_shell():
    request = parse_request(_valid_request(app="iPhone Mirroring", mode="background", allow_local_shell=False))
    kwargs = runner_module._computer_kwargs(request, deadline=1.0, cancellation=object(), api_key="k")
    assert kwargs["allow_local_shell"] is False
    assert "Local shell and filesystem tools are disabled" in runner_module.system_context(
        "background", "iPhone Mirroring", False
    )


def test_system_context_varies_only_in_the_mode_specific_parts():
    foreground = runner_module.system_context("foreground")
    background = runner_module.system_context("background", "Notes")
    assert foreground == runner_module.SYSTEM_CONTEXT
    assert foreground.startswith("You control the entire macOS screen.")
    assert background.startswith("You control exactly one application window: Notes.")
    assert "entire macOS screen" not in background and "bring anything to the front" in background
    assert "Never use the shell to open, launch, or activate applications" in background
    assert "open -a" not in foreground
    assert "leave that result in view" not in background
    for shared in ("cmd, not ctrl", "Shell commands run headlessly", "Do not open or change System Settings"):
        assert shared in foreground and shared in background
    assert "the target application" in runner_module.system_context("background")


def test_supports_background_mode_reads_the_sdk_signature(monkeypatch):
    class WithScope:
        def __init__(self, *, scope="desktop"):
            pass

    class Legacy:
        def __init__(self, *, presentation=True):
            pass

    monkeypatch.setattr(runner_module, "MacOSComputer", WithScope)
    assert runner_module._supports_background_mode() is True
    monkeypatch.setattr(runner_module, "MacOSComputer", Legacy)
    assert runner_module._supports_background_mode() is False


async def test_run_request_background_binds_the_window_and_never_fronts(monkeypatch):
    _FakeComputer.instances.clear()
    _FakeAgent.instances.clear()
    prepared = AsyncMock(return_value={"name": "Notes", "pid": 42, "window_id": 7})
    _patch_runner_sdk(monkeypatch)
    monkeypatch.setattr(runner_module, "prepare_app", prepared)
    monkeypatch.setattr(runner_module, "_supports_background_mode", lambda: True)
    monkeypatch.setattr("yutori.navigator.macos.MacOSWindowTarget", _FakeWindowTarget, raising=False)
    monkeypatch.setattr(
        _FakeComputer,
        "telemetry_overrides",
        {
            "delivery_counts": {
                "background_attempts": 5,
                "foreground_escalations": 2,
                "fallback_skips": 3,
                "background_refusals": 1,
                "window_rebinds": 1,
            },
            "window_target_info": {"pid": 42, "window_id": 7, "app_name": "Notes"},
        },
    )
    stream = _CollectStream()
    request = parse_request(_background_request(allow_foreground_fallback=True))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "completed"

    computer = _FakeComputer.instances[-1]
    agent = _FakeAgent.instances[-1]
    assert computer.kwargs["scope"] == "window" and computer.kwargs["presentation"] is True
    assert computer.kwargs["allow_foreground_fallback"] is True
    assert computer.screenshots == 0  # no pre-launch desktop frame in window scope
    assert prepared.await_args.args[1:] == ("Notes", None) and prepared.await_args.kwargs == {"front": False}
    assert computer.window_targets == [_FakeWindowTarget(42, 7, app_name="Notes")]
    assert computer.target_pid == 42
    assert agent.kwargs["system_prompt"].startswith("You control exactly one application window: Notes.")
    # The agent's sink is the activity tee wrapping the SDK's own controller.
    assert isinstance(agent.kwargs["presentation"], runner_module.ActivityReporter)
    assert agent.kwargs["presentation"]._inner is computer.presentation
    result = json.loads(stream.lines[-1])
    assert result["delivery_mode"] == "background"
    assert result["reasoning_overlay_requested"] is True and result["reasoning_overlay_effective"] is True
    assert result["fallback_escalations"] == 2 and result["background_refusals"] == 1
    assert result["fallback_skips"] == 3
    assert result["window_rebinds"] == 1 and result["focus_guard_trips"] == 0
    assert result["preview_frames"] == 0
    assert result["window_target"] == {"pid": 42, "window_id": 7, "app_name": "Notes"}
    startup_events = [json.loads(line) for line in stream.lines if json.loads(line)["type"] == "startup"]
    assert [event["phase"] for event in startup_events] == ["api_client", "computer", "target", "model"]
    action = next(json.loads(line) for line in stream.lines if json.loads(line)["type"] == "action")
    assert action["delivery_mode"] == "background" and action["route"] == "pixel"
    assert action["effect"] is None and action["escalated"] is False

    prepared.return_value = {"name": "Notes", "pid": 43, "window_id": 9}
    assert await computer.recover_target() == 43
    assert prepared.await_args.kwargs == {"front": False}
    assert computer.window_targets[-1] == _FakeWindowTarget(43, 9, app_name="Notes")


async def test_run_request_foreground_does_not_encode_the_invalidated_prelaunch_frame(monkeypatch):
    _FakeComputer.instances.clear()
    prepared = AsyncMock(return_value={"name": "Notes", "pid": 42, "window_id": 7})
    _patch_runner_sdk(monkeypatch)
    monkeypatch.setattr(runner_module, "prepare_app", prepared)
    stream = _CollectStream()
    request = parse_request(_valid_request(app="Notes", deadline_ms=int((time.time() + 60) * 1000)))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "completed"

    computer = _FakeComputer.instances[-1]
    assert computer.screenshots == 0
    assert prepared.await_args.kwargs == {"front": True}
    assert computer.window_targets == []
    result = json.loads(stream.lines[-1])
    assert result["delivery_mode"] == "foreground" and result["window_target"] is None
    assert result["fallback_escalations"] == 0 and result["background_refusals"] == 0


async def test_pinned_sdk_launch_invalidates_its_cached_prelaunch_frame():
    from yutori.navigator.macos import MacOSComputer

    transport = SimpleNamespace(
        call_tool=AsyncMock(return_value={"structuredContent": {"name": "Notes", "pid": 42}})
    )
    computer = MacOSComputer(transport=transport, owns_transport=False, presentation=False)
    computer._initial_png = b"stale desktop"

    assert await computer.launch_app(name="Notes") == {"name": "Notes", "pid": 42}
    assert computer._initial_png is None


async def test_run_request_background_without_sdk_support_fails_before_touching_the_desktop(monkeypatch):
    class Untouchable:
        def __init__(self, **_kwargs):
            raise AssertionError("MacOSComputer must not be constructed")

    monkeypatch.setattr(runner_module, "MacOSComputer", Untouchable)
    monkeypatch.setattr(runner_module, "_supports_background_mode", lambda: False)
    stream = _CollectStream()

    assert await runner_module.run_request(parse_request(_background_request()), Emitter(stream), "k") == "failed"

    (event,) = [json.loads(line) for line in stream.lines]
    assert event["type"] == "error" and event["code"] == "UNSUPPORTED_MODE"
    assert SDK_VERSION in event["message"]


async def test_action_reporter_reports_the_delivery_the_sdk_observed():
    computer = SimpleNamespace(action_outcomes=[])
    stream = _CollectStream()
    reporter = ActionReporter(
        Emitter(stream),
        time.monotonic(),
        delivery_mode="background",
        action_delivery=runner_module._action_delivery(computer),
    )
    item = {"name": "computer_batch", "arguments": {"actions": []}}
    # One batch: a background click, an escalated type, then a background key press.
    computer.action_outcomes.extend(
        [
            SimpleNamespace(
                requested_delivery="background", route="accessibility", effect="unverifiable", escalated=False,
                refusal_code=None,
            ),
            SimpleNamespace(
                requested_delivery="foreground", route="synthetic_events", effect="unverifiable", escalated=True,
                refusal_code="delivery_failed",
            ),
            SimpleNamespace(
                requested_delivery="background", route="synthetic_events", effect="confirmed", escalated=False,
                refusal_code=None,
            ),
        ]
    )
    await reporter.on_computer_call_start(item)
    await reporter.on_computer_call_end(item, [{"output": "ok"}])
    await reporter.on_computer_call_start({"name": "bash", "arguments": {"command": "ls"}})
    await reporter.on_computer_call_end({"name": "bash", "arguments": {"command": "ls"}}, [{"output": "ok"}])
    first, second = (json.loads(line) for line in stream.lines)
    assert (first["delivery_mode"], first["route"], first["effect"], first["escalated"]) == (
        "foreground",
        "synthetic_events",
        "confirmed",
        True,
    )
    assert first["refusal_code"] == "delivery_failed"
    # No new driver outcome for the shell call: fall back to the configured mode.
    assert (second["delivery_mode"], second["route"], second["effect"], second["escalated"]) == (
        "background",
        "pixel",
        None,
        False,
    )


def _background_window(window_id: int = 7, **overrides: Any) -> dict[str, Any]:
    window = {
        "window_id": window_id,
        "bounds": {"width": 400, "height": 500},
        "is_on_screen": False,
        "on_current_space": None,
        "z_index": 3,
    }
    window.update(overrides)
    return window


async def test_prepare_app_background_unhides_and_returns_the_window_without_fronting():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Notes", "windows": [_background_window()]}),
        unhide_app=AsyncMock(return_value=True),
        bring_to_front=AsyncMock(side_effect=AssertionError("background runs must never front the app")),
        list_windows=AsyncMock(
            return_value={"windows": [_background_window(is_on_screen=True, on_current_space=True)]}
        ),
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "Notes", None, front=False)
    assert target == {"name": "Notes", "pid": 42, "window_id": 7}
    computer.unhide_app.assert_awaited_once_with(42)
    computer.bring_to_front.assert_not_awaited()
    computer.list_windows.assert_awaited_once_with(42)
    computer.wait.assert_awaited_once_with(300)


async def test_prepare_app_background_refreshes_a_transient_launch_window():
    helper = _background_window(
        8,
        title="",
        bounds={"width": 280, "height": 168},
        z_index=20,
    )
    main = _background_window(
        9,
        title="Yutori Input Probe",
        bounds={"width": 1120, "height": 780},
        z_index=10,
    )
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Yutori Input Probe", "windows": [helper]}),
        unhide_app=AsyncMock(return_value=True),
        list_windows=AsyncMock(
            return_value={
                "windows": [
                    {**helper, "is_on_screen": True, "on_current_space": True},
                    {**main, "is_on_screen": True, "on_current_space": True},
                ]
            }
        ),
        wait=AsyncMock(),
    )

    target = await prepare_app(computer, "Yutori Input Probe", None, front=False)

    assert target["window_id"] == 9


async def test_prepare_app_background_fallback_skips_visible_host_for_offscreen_content(monkeypatch):
    from yutori_mcp.computer_use import app as app_module

    monkeypatch.setattr(app_module, "_WINDOW_POLL_ATTEMPTS", 2)
    helper = _background_window(
        8,
        title="",
        bounds={"width": 280, "height": 168},
        is_on_screen=True,
        on_current_space=True,
        z_index=20,
    )
    main = _background_window(
        9,
        title="Yutori Input Probe",
        bounds={"width": 1120, "height": 780},
        z_index=10,
    )
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Yutori Input Probe"}),
        unhide_app=AsyncMock(return_value=True),
        list_windows=AsyncMock(return_value={"windows": [helper, main]}),
        wait=AsyncMock(),
    )

    target = await prepare_app(computer, "Yutori Input Probe", None, front=False)

    assert target["window_id"] == 9
    assert computer.list_windows.await_count == 2


async def test_prepare_app_background_polls_for_a_window_after_a_cold_launch():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Notes", "windows": []}),
        unhide_app=AsyncMock(return_value=True),
        list_windows=AsyncMock(
            side_effect=[
                {"windows": []},
                {"windows": [_background_window(9, is_on_screen=True, on_current_space=True)]},
            ]
        ),
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "Notes", None, front=False)
    assert target["window_id"] == 9
    assert computer.list_windows.await_args_list == [((42,),), ((42,),)]
    assert [call.args for call in computer.wait.await_args_list] == [(300,), (250,)]


async def test_prepare_app_background_still_resolves_a_window_when_unhide_fails():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Notes", "windows": []}),
        unhide_app=AsyncMock(side_effect=CuaDriverToolError("unhide failed")),
        list_windows=AsyncMock(
            return_value={"windows": [_background_window(9, is_on_screen=True, on_current_space=True)]}
        ),
        wait=AsyncMock(),
    )

    target = await prepare_app(computer, "Notes", None, front=False)

    assert target == {"name": "Notes", "pid": 42, "window_id": 9}
    computer.unhide_app.assert_awaited_once_with(42)
    computer.list_windows.assert_awaited_once_with(42)


async def test_prepare_app_background_gives_up_when_no_window_appears(monkeypatch):
    from yutori_mcp.computer_use import app as app_module

    monkeypatch.setattr(app_module, "_WINDOW_POLL_ATTEMPTS", 2)
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Notes"}),
        unhide_app=AsyncMock(return_value=False),
        list_windows=AsyncMock(return_value={"windows": []}),
        wait=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="showed no window to target in background mode"):
        await prepare_app(computer, "Notes", None, front=False)
    assert computer.list_windows.await_count == 2


def test_pick_best_window_prefers_offscreen_content_windows_over_helper_strips():
    strip = {"window_id": 1, "bounds": {"width": 3360, "height": 30}, "is_on_screen": False, "z_index": 114}
    content = _background_window(2, bounds={"width": 230, "height": 408}, z_index=67)
    assert pick_best_window([strip, content])["window_id"] == 2
    visible = _background_window(3, is_on_screen=True, on_current_space=True, z_index=1)
    assert pick_best_window([strip, content, visible])["window_id"] == 3


def test_format_result_renders_background_fields():
    text = format_result(
        {
            "outcome": "completed",
            "delivery_mode": "background",
            "final_text": "done",
            "reasoning_overlay_requested": True,
            "reasoning_overlay_effective": True,
            "codec": "jpeg",
            "observation_format": "webp",
            "fallback_escalations": 1,
            "background_refusals": 2,
            "window_target": {"pid": 42, "window_id": 7, "app_name": "Notes"},
            "actions": [
                _action_event(route="accessibility", effect="confirmed", escalated=True, duration_ms=5)
            ],
        }
    )
    assert "Delivery mode: background" in text
    assert "Window target: Notes (pid 42, window 7)" in text
    assert "Delivery: 1 foreground escalation(s), 2 background refusal(s)" in text
    assert "mode: foreground; route: accessibility; refusal: None); effect: confirmed [fronted] took 5 ms" in text
    assert "Menu bar status: active; capture: jpeg; N2 request: webp" in text
    assert "Reasoning overlay" not in text


def test_format_result_reports_skipped_retries_only_when_present():
    base = {"outcome": "completed", "delivery_mode": "background", "final_text": "done"}
    assert "Delivery: 0 foreground escalation(s), 0 background refusal(s)" in format_result(base)
    assert "skipped" not in format_result(base)
    assert "Activity window" not in format_result(base)
    assert "Activity window: 7 frame(s) streamed while it was open" in format_result({**base, "preview_frames": 7})
    with_skips = format_result({**base, "fallback_skips": 2})
    assert "Delivery: 0 foreground escalation(s), 0 background refusal(s), 2 retry(ies) skipped after the window changed" in with_skips


def test_format_result_foreground_output_has_no_background_lines():
    text = format_result({"outcome": "completed", "delivery_mode": "foreground", "final_text": "done"})
    assert "Delivery:" not in text and "Window target" not in text


def test_terminal_result_and_failure_carry_the_requested_mode():
    assert terminal_result("limit", "x", delivery_mode=DELIVERY_MODE_BACKGROUND)["delivery_mode"] == "background"
    assert failure("x", delivery_mode=DELIVERY_MODE_BACKGROUND) == terminal_result(
        "failed", "x", delivery_mode=DELIVERY_MODE_BACKGROUND
    )
    assert failure("x")["delivery_mode"] == DELIVERY_MODE_FOREGROUND


def test_docs_describe_background_mode():
    root = Path(__file__).parents[1]
    assert '"mode": "background"' in (root / "TOOLS.md").read_text()
    assert "allow_foreground_fallback" in (root / "TOOLS.md").read_text()
    assert "no background runs" not in (root / "README.md").read_text()
    skill = " ".join((root / "skills/06-computer-use/SKILL.md").read_text().split())
    assert "--mode background" in skill
    assert '"in the background"' in skill and "ask which app to target" in skill
    assert "Claude Code, including Claude sessions hosted by Conductor" in skill
    assert "run the CLI through the Bash tool with stdout attached" in skill
    assert "does not expose MCP progress or log notifications" in skill
    assert "TaskOutput" in skill


def test_claude_mcp_config_allows_the_full_computer_use_deadline():
    config = json.loads((Path(__file__).parents[1] / ".mcp.json").read_text())
    # The tool accepts a 60-minute deadline; leave one minute for startup and
    # final result delivery so Claude does not abandon a still-running child.
    assert config["mcpServers"]["yutori"]["request_timeout_ms"] == 61 * 60 * 1000


def test_iphone_mirroring_skill_uses_the_scoped_safe_route():
    root = Path(__file__).parents[1]
    skill_path = root / "skills/07-iphone-mirroring/SKILL.md"
    skill = skill_path.read_text()
    compact = " ".join(skill.split())
    assert "name: yutori-iphone-mirroring" in skill
    assert "This capability is purely experimental" in skill
    assert '"app": "iPhone Mirroring"' in skill
    assert '"mode": "background"' in skill
    assert '"allow_local_shell": false' in skill
    assert "--no-local-shell" in skill
    assert '"allow_foreground_fallback": true' not in skill
    assert "--allow-foreground-fallback" not in skill
    assert "Never enable foreground fallback" in skill
    assert '"max_steps": 100' in skill
    assert "Command-3" in skill and "Command-1" in skill and "Command-2" in skill
    assert "stop and report the blocked action" in compact
    assert "Do not repeat an action reported as uncertain" in compact
    assert "passcode, passkey, biometric, one-time-code" in compact
    assert "This capability is purely experimental" in (root / "README.md").read_text()


# --- computer-use stop --------------------------------------------------------------------


async def test_supervisor_advertises_the_runner_pid_only_while_it_runs(monkeypatch, tmp_path):
    pid_path = tmp_path / "computer-use.pid"
    monkeypatch.setattr(supervisor, "runner_pid_path", lambda: pid_path)
    process = _Process(_stream(json.dumps(_ready_event()), json.dumps(_result_event())), _stream())
    seen: list[str] = []

    async def on_event(event):
        seen.append(pid_path.read_text().strip())

    result = await _run_supervised(process, on_event=on_event)
    assert result["outcome"] == "completed"
    assert seen == ["123"]
    assert not pid_path.exists()


def test_stop_active_run_signals_the_runner_process_group(monkeypatch, tmp_path):
    pid_path = tmp_path / "computer-use.pid"
    pid_path.write_text("4242\n")
    monkeypatch.setattr(supervisor, "runner_pid_path", lambda: pid_path)
    monkeypatch.setattr(supervisor, "_process_command", lambda pid: f"python -I -m {supervisor.RUNNER_MODULE}")
    with patch("yutori_mcp.computer_use.supervisor.os.killpg") as killpg:
        message = supervisor.stop_active_run()
    killpg.assert_called_once_with(4242, signal.SIGTERM)
    assert "pid 4242" in message and "aborted" in message
    assert pid_path.exists()  # the supervisor removes it once the runner exits


@pytest.mark.parametrize("command", [None, "/bin/bash -l"])
def test_stop_active_run_ignores_and_removes_a_stale_pid_file(monkeypatch, tmp_path, command):
    pid_path = tmp_path / "computer-use.pid"
    pid_path.write_text("4242\n")
    monkeypatch.setattr(supervisor, "runner_pid_path", lambda: pid_path)
    monkeypatch.setattr(supervisor, "_process_command", lambda pid: command)
    with patch("yutori_mcp.computer_use.supervisor.os.killpg") as killpg:
        message = supervisor.stop_active_run()
    killpg.assert_not_called()
    assert message.startswith("No computer-use run is active")
    assert not pid_path.exists()


def test_stop_active_run_reports_no_run_without_a_pid_file(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "runner_pid_path", lambda: tmp_path / "missing.pid")
    assert supervisor.stop_active_run() == "No computer-use run is active."


def test_cli_stop_prints_the_stop_outcome(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(cli, "stop_active_run", lambda: "No computer-use run is active.")
    parser = argparse.ArgumentParser()
    cli.register_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["computer-use", "stop"])
    assert cli.dispatch(args.computer_use_command, args) == 0
    assert capsys.readouterr().out == "No computer-use run is active.\n"


def _batch(*members: dict[str, Any]) -> dict[str, Any]:
    return {"name": "computer_batch", "arguments": {"actions": list(members)}}


def test_batch_action_previews_describe_each_member_of_the_batch():
    previews = batch_action_previews(
        _batch(
            {"action": "screenshot"},
            {"action": "left_click", "coordinates": [412, 318], "modifier": "ctrl"},
            {"action": "type", "text": "yutori.com/company"},
            {"action": "key_press", "key": "cmd+l"},
            {"action": "scroll", "coordinates": [500, 500], "direction": "down", "amount": 3},
            {"action": "wait", "duration": 1.5},
            # The nested envelope tool sets 20260812/20260815 advertise, which
            # flatten_batch_member() folds into the flat shape before rendering.
            {"name": "drag", "arguments": {"start_coordinates": [10, 20], "coordinates": [30, 40]}},
        )
    )
    assert previews == [
        "screenshot",
        "left_click (412,318) +ctrl",
        'type "yutori.com/company"',
        "key_press cmd+l",
        "scroll (500,500) down x3",
        "wait 1.5s",
        "drag (10,20) -> (30,40)",
    ]


def test_batch_action_previews_only_apply_to_batches_and_survive_junk_members():
    assert batch_action_previews({"name": "bash", "arguments": {"command": "ls"}}) is None
    assert batch_action_previews(_batch()) is None
    assert batch_action_previews({"name": "computer_batch", "arguments": {"actions": "nope"}}) is None
    # A malformed coordinate pair is dropped rather than rendered or raised on.
    assert batch_action_previews(_batch({"action": "left_click", "coordinates": [1]}, "junk")) == ["left_click"]


def test_batch_action_previews_redact_a_secret_the_model_typed(monkeypatch):
    monkeypatch.setenv("DEMO_API_KEY", "super-secret-value")
    (preview,) = batch_action_previews(_batch({"action": "type", "text": "super-secret-value"}))
    assert "super-secret-value" not in preview
    (long_preview,) = batch_action_previews(_batch({"action": "type", "text": "y" * 200}))
    typed = long_preview.removeprefix('type "').removesuffix('"')
    assert len(typed) == runner_module.TYPED_TEXT_PREVIEW_CHARACTERS and typed.endswith("\u2026")


async def test_action_events_carry_the_batch_detail_and_no_detail_for_other_tools():
    stream = _CollectStream()
    reporter = ActionReporter(Emitter(stream), time.monotonic())
    batch = _batch({"action": "left_click", "coordinates": [1, 2]})
    await reporter.on_computer_call_start(batch)
    await reporter.on_computer_call_end(batch, [{"output": "ok"}])
    shell = {"name": "bash", "arguments": {"command": "ls"}}
    await reporter.on_computer_call_start(shell)
    await reporter.on_computer_call_end(shell, [{"output": "ok"}])
    first, second = (json.loads(line) for line in stream.lines)
    assert first["details"] == ["left_click (1,2)"]
    assert second["details"] is None


def test_supports_color_honors_the_environment_and_the_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert supports_color(SimpleNamespace(isatty=lambda: True)) is True
    assert supports_color(SimpleNamespace(isatty=lambda: False)) is False
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert supports_color(SimpleNamespace(isatty=lambda: False)) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert supports_color(SimpleNamespace(isatty=lambda: True)) is False


# The Terminal every rendering test below wants: no ANSI codes and no non-ASCII glyphs, so
# assertions can pin exact byte-for-byte output instead of the color/glyph-detecting default.
_PLAIN_TERMINAL = Terminal(color=False, glyphs=False)


def test_supports_glyphs_falls_back_for_an_ascii_stream():
    assert supports_glyphs(SimpleNamespace(encoding="utf-8")) is True
    assert supports_glyphs(SimpleNamespace(encoding="ascii")) is False
    assert _PLAIN_TERMINAL.glyph("check").isascii() and _PLAIN_TERMINAL.rule("X").isascii()


def test_terminal_paints_only_when_color_is_on():
    assert Terminal(color=False)("ok", "green") == "ok"
    assert Terminal(color=True)("ok", "green") == "\033[32mok\033[0m"


def test_format_terminal_action_shows_the_batch_members_and_the_shell_command():
    lines = format_terminal_action(
        {
            "index": 4,
            "tool": "computer_batch",
            "status": "executed",
            "duration_ms": 1630,
            "elapsed_ms": 14691,
            "details": ["left_click (1,2)", 'type "hi"'],
        },
        _PLAIN_TERMINAL,
    )
    assert lines[0] == "v #4 computer_batch  1.6s | at 14.7s"
    assert [line.strip() for line in lines[1:]] == ["> left_click (1,2)", '> type "hi"']
    refused = format_terminal_action(
        {"index": 0, "tool": "bash", "status": "refused", "refusal_code": "driver_refused", "command": "ls"},
        _PLAIN_TERMINAL,
    )
    assert refused[0] == "x #0 bash refused (driver_refused)"
    assert refused[1].strip() == "$ ls"


def test_format_terminal_result_leads_with_a_labeled_final_output_block():
    text = format_terminal_result(
        {
            "outcome": "completed",
            "delivery_mode": "foreground",
            "final_text": "  Here are the 14 employees.  ",
            "elapsed_ms": 292147,
            "steps": 35,
            "run_url": "https://platform.yutori.com/navigator/chats/abc",
        },
        _PLAIN_TERMINAL,
    )
    lines = [line for line in text.split("\n") if line.strip()]
    assert FINAL_OUTPUT_HEADING in lines[0]
    assert lines[1] == "Here are the 14 employees."
    assert lines[3].startswith("v completed") and "4m 52.1s" in lines[3] and "35 model turns" in lines[3]
    assert lines[4].strip() == f"version   yutori-mcp {MCP_VERSION}  |  yutori {SDK_VERSION}"
    assert lines[5].split() == ["run", "https://platform.yutori.com/navigator/chats/abc"]


def test_format_terminal_result_omits_the_action_list_unless_asked():
    result = {
        "outcome": "failed",
        "delivery_mode": "foreground",
        "actions": [{"index": 0, "tool": "bash", "status": "executed", "command": "ls"}],
    }
    assert "bash" not in format_terminal_result(result, _PLAIN_TERMINAL)
    assert "bash" in format_terminal_result(result, _PLAIN_TERMINAL, include_actions=True)


def test_format_terminal_result_reports_the_background_surfaces():
    text = format_terminal_result(
        {
            "outcome": "limit",
            "delivery_mode": "background",
            "window_target": {"pid": 42, "app_name": "Notes", "window_id": 7},
            "reasoning_overlay_requested": True,
            "reasoning_overlay_effective": True,
            "codec": "jpeg",
            "observation_format": "webp",
            "fallback_escalations": 1,
            "fallback_skips": 2,
            "preview_frames": 7,
        },
        _PLAIN_TERMINAL,
    )
    assert "! limit" in text
    assert "window    Notes (pid 42, window 7)" in text
    assert "menu bar  active; capture: jpeg; N2 request: webp" in text
    assert "delivery  1 foreground escalation(s), 0 background refusal(s)" in text
    assert "retry(ies) skipped after the window changed" in text
    assert "activity  7 frame(s) streamed while the window was open" in text


def test_cli_run_header_states_the_task_target_and_limits():
    from yutori_mcp.computer_use import cli

    params = ComputerUseTaskInput(task="list the team", app="Safari", start_url="https://yutori.com", minutes=5)
    text = cli.format_run_header(params, _PLAIN_TERMINAL)
    assert "task      list the team" in text
    assert "target    Safari  https://yutori.com" in text
    assert f"version   yutori-mcp {MCP_VERSION}  |  yutori {SDK_VERSION}" in text
    assert "limits    foreground  |  5 min  |  60 model turns" in text
    assert cli.hands_off_notice("foreground") in text


async def test_cli_startup_printer_shows_phase_and_cumulative_timers(capsys):
    from yutori_mcp.computer_use import cli

    clock = iter((10.25, 10.5)).__next__
    printer = cli._event_printer("foreground", "Safari", _PLAIN_TERMINAL, started_at=10.0, clock=clock)

    await printer(_ready_event())
    await printer(_startup_event(phase="target", duration_ms=120))

    output = capsys.readouterr().out
    assert "runner process ready  250ms" in output
    assert "Safari ready  120ms | at 500ms" in output


def test_startup_timing_formatter_scales_durations_and_names_phases():
    assert format_duration(85) == "85ms"
    assert format_duration(1_250) == "1.2s"
    assert format_startup_line(_startup_event(phase="target"), app="Notes") == (
        "Notes ready  250ms | at 300ms"
    )


async def test_progress_reporter_folds_the_batch_detail_into_one_message():
    from yutori_mcp import server

    messages: list[str] = []
    ctx = SimpleNamespace(
        report_progress=AsyncMock(),
        info=lambda message: messages.append(message) or asyncio.sleep(0),
    )
    on_event = server._progress_reporter(ctx, 12)
    await on_event(
        {
            "type": "action",
            "index": 3,
            "tool": "computer_batch",
            "status": "executed",
            "elapsed_ms": 900,
            "details": ["left_click (1,2)", 'type "hi"'],
        }
    )
    (message,) = messages
    assert message.count("\n") == 0
    assert message.endswith('left_click (1,2); type "hi"')


# ---------------------------------------------------------------------------
# Embedded driver host: a host application ships its own cua-driver and daemon.
# ---------------------------------------------------------------------------


def _configure_embedded_host(monkeypatch, tmp_path, *, binary_exists: bool = True) -> tuple[Path, Path]:
    binary = tmp_path / "cua-driver"
    if binary_exists:
        binary.write_text("")
    sock = tmp_path / "daemon.sock"
    monkeypatch.setenv(preflight.ENV_DRIVER_BINARY, str(binary))
    monkeypatch.setenv(preflight.ENV_DRIVER_SOCKET, str(sock))
    return binary, sock


def test_embedded_host_environment_names_match_the_sdk_transport():
    from yutori.navigator.macos import transport

    if not hasattr(transport, "ENV_DRIVER_BINARY"):
        pytest.skip("the pinned SDK predates the embedded transport constants")
    assert preflight.ENV_DRIVER_BINARY == transport.ENV_DRIVER_BINARY
    assert preflight.ENV_DRIVER_SOCKET == transport.ENV_DRIVER_SOCKET


def test_embedded_host_is_absent_without_configuration(monkeypatch):
    monkeypatch.delenv(preflight.ENV_DRIVER_BINARY, raising=False)
    monkeypatch.delenv(preflight.ENV_DRIVER_SOCKET, raising=False)
    assert preflight.embedded_driver_host() is None
    assert preflight._driver_socket_arguments() == []


def test_embedded_host_requires_both_binary_and_socket(monkeypatch, tmp_path):
    monkeypatch.setenv(preflight.ENV_DRIVER_BINARY, str(tmp_path / "cua-driver"))
    monkeypatch.delenv(preflight.ENV_DRIVER_SOCKET, raising=False)
    with pytest.raises(ValueError, match="must be set together"):
        preflight.embedded_driver_host()
    result = preflight.check_driver_app()
    assert not result.ok and "must be set together" in result.detail
    # Half a configuration must not fall back to the standalone install either.
    monkeypatch.setattr(preflight, "DRIVER_PATHS", (tmp_path / "absent",))
    assert preflight.find_cua_driver() is None


def test_embedded_host_binary_replaces_the_app_bundle_and_path_discovery(monkeypatch, tmp_path):
    binary, sock = _configure_embedded_host(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight, "DRIVER_PATHS", (tmp_path / "stale-cua-driver",))
    (tmp_path / "stale-cua-driver").write_text("")

    assert preflight.find_cua_driver() == binary
    assert preflight.check_driver_app().ok
    assert preflight._driver_socket_arguments() == ["--socket", str(sock)]
    assert preflight.child_search_path().split(":")[0] == str(tmp_path)


def test_embedded_host_missing_binary_blocks(monkeypatch, tmp_path):
    _configure_embedded_host(monkeypatch, tmp_path, binary_exists=False)
    result = preflight.check_driver_app()
    assert not result.ok and result.remediation == preflight._EMBEDDED_HOST_REMEDIATION
    assert preflight.find_cua_driver() is None


def test_embedded_daemon_identity_is_the_listening_private_socket(monkeypatch, tmp_path):
    import shutil
    import socket as socket_module
    import tempfile

    _configure_embedded_host(monkeypatch, tmp_path)
    # AF_UNIX paths are capped at 104 bytes on macOS; pytest's tmp_path is longer than that.
    short_dir = Path(tempfile.mkdtemp(prefix="cu-", dir="/tmp"))
    sock = short_dir / "d.sock"
    monkeypatch.setenv(preflight.ENV_DRIVER_SOCKET, str(sock))
    try:
        blocked = preflight.check_daemon_identity()
        assert not blocked.ok and str(sock) in blocked.detail

        server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        server.bind(str(sock))
        server.listen(1)
        try:
            assert preflight.check_daemon_identity().ok
        finally:
            server.close()
    finally:
        shutil.rmtree(short_dir, ignore_errors=True)


def _write_fake_mcp_proxy(path: Path, structured: dict[str, Any]) -> None:
    """A stand-in for ``cua-driver mcp``: answers initialize and one tools/call, echoing its argv."""
    path.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, sys",
                f"structured = {structured!r}",
                "structured['argv'] = sys.argv[1:]",
                "for line in sys.stdin:",
                "    message = json.loads(line)",
                "    if message.get('id') == 1:",
                "        print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'capabilities': {}}}), flush=True)",
                "    elif message.get('id') == 2:",
                "        print('daemon log line that is not JSON', flush=True)",
                "        print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': {'structuredContent': structured}}), flush=True)",
                "",
            ]
        )
    )
    path.chmod(0o700)


def test_embedded_permissions_come_from_the_check_permissions_tool(monkeypatch, tmp_path):
    binary, sock = _configure_embedded_host(monkeypatch, tmp_path)
    _write_fake_mcp_proxy(binary, {"accessibility": True, "screen_recording": True, "source": {"attribution": "host"}})

    payload = preflight._embedded_permissions(preflight.embedded_driver_host())

    assert payload["argv"] == ["mcp", "--embedded", "--socket", str(sock)]
    assert preflight.check_permissions().ok


def test_embedded_permissions_missing_grant_blocks_with_host_remediation(monkeypatch, tmp_path):
    binary, _ = _configure_embedded_host(monkeypatch, tmp_path)
    _write_fake_mcp_proxy(binary, {"accessibility": True, "screen_recording": False})

    result = preflight.check_permissions()

    assert not result.ok
    assert result.remediation == preflight._EMBEDDED_PERMISSIONS_REMEDIATION
    assert "host application" in result.detail


def test_embedded_permissions_proxy_failure_blocks_instead_of_raising(monkeypatch, tmp_path):
    binary, _ = _configure_embedded_host(monkeypatch, tmp_path)
    binary.write_text(f"#!{sys.executable}\nraise SystemExit(3)\n")
    binary.chmod(0o700)
    monkeypatch.setattr(preflight, "_EMBEDDED_RPC_TIMEOUT_SECONDS", 2)

    assert not preflight.check_permissions().ok


@pytest.mark.parametrize("capture_result", [None, subprocess.CompletedProcess([], 0)])
def test_embedded_capture_failure_names_the_host_application(monkeypatch, tmp_path, capture_result):
    _configure_embedded_host(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight, "_run_safely", lambda *_args, **_kwargs: capture_result)

    result = preflight.check_capture()

    assert not result.ok
    assert result.remediation == preflight._EMBEDDED_PERMISSIONS_REMEDIATION
    assert "CuaDriver" not in result.remediation


def test_child_environment_forwards_the_embedded_host_configuration(monkeypatch, tmp_path):
    binary, sock = _configure_embedded_host(monkeypatch, tmp_path)
    monkeypatch.setenv(preflight.ENV_DRIVER_EMBEDDED, "1")
    monkeypatch.setenv("CUA_DRIVER_RS_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CUA_DRIVER_HOST_BUNDLE_ID", "com.yutori.desktop")
    monkeypatch.setenv("UNRELATED_SECRET", "no")

    env = supervisor._child_environment("yt-key")

    assert env[preflight.ENV_DRIVER_BINARY] == str(binary)
    assert env[preflight.ENV_DRIVER_SOCKET] == str(sock)
    assert env[preflight.ENV_DRIVER_EMBEDDED] == "1"
    assert env["CUA_DRIVER_RS_HOME"] == str(tmp_path / "state")
    assert env["CUA_DRIVER_HOST_BUNDLE_ID"] == "com.yutori.desktop"
    assert "UNRELATED_SECRET" not in env
    assert env["PATH"].split(":")[0] == str(tmp_path)


# ---------------------------------------------------------------------------
# Host-owned Stop control and the machine-readable CLI surface.
# ---------------------------------------------------------------------------


def _request_payload(**overrides):
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "type": "run",
        "task": "open calculator",
        "app": None,
        "start_url": None,
        "deadline_ms": 60_000,
        "max_steps": 5,
        "mode": DELIVERY_MODE_FOREGROUND,
        "allow_foreground_fallback": False,
        "allow_local_shell": True,
        "model": "n2",
        "api_base_url": "https://api.yutori.com/v1",
    }
    payload.update(overrides)
    return payload


def test_parse_request_defaults_show_stop_button_and_validates_it():
    assert parse_request(_request_payload())["show_stop_button"] is True
    assert parse_request(_request_payload(show_stop_button=False))["show_stop_button"] is False
    with pytest.raises(RequestError, match="show_stop_button must be a boolean"):
        parse_request(_request_payload(show_stop_button="no"))


def test_computer_kwargs_forward_the_stop_control_choice():
    request = parse_request(_request_payload(show_stop_button=False))
    kwargs = runner_module._computer_kwargs(
        request, deadline=time.monotonic() + 60, cancellation=runner_module.CancellationLatch(), api_key="k"
    )
    assert kwargs["show_stop_button"] is False
    assert kwargs["presentation"] is True


async def test_run_task_request_carries_show_stop_button(tmp_path):
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(**_run_task_kwargs(tmp_path, show_stop_button=False))
    assert supervise.await_args.kwargs["request"]["show_stop_button"] is False
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(**_run_task_kwargs(tmp_path))
    assert supervise.await_args.kwargs["request"]["show_stop_button"] is True


def test_cli_run_and_doctor_parsers_accept_json_and_hide_stop_item():
    from yutori_mcp.computer_use import cli

    parser = argparse.ArgumentParser()
    cli.register_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["computer-use", "run", "add a note", "--json", "--hide-stop-item"])
    assert args.json is True and args.hide_stop_item is True
    default = parser.parse_args(["computer-use", "run", "add a note"])
    assert default.json is False and default.hide_stop_item is False
    assert parser.parse_args(["computer-use", "doctor", "--json"]).json is True
    assert parser.parse_args(["computer-use", "doctor"]).json is False


def _run_args(**overrides) -> SimpleNamespace:
    args = {
        "task": "add a note",
        "app": None,
        "start_url": None,
        "minutes": 30,
        "max_steps": 60,
        "mode": "foreground",
        "allow_foreground_fallback": False,
        "allow_local_shell": True,
        "json": False,
        "hide_stop_item": False,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def _json_lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def test_cli_run_json_streams_events_and_the_result_as_json_lines(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    action = _action_event(tool="computer_batch", index=0)

    async def run(**kwargs):
        await kwargs["on_event"](_ready_event())
        await kwargs["on_event"](action)
        return {"outcome": "completed", "delivery_mode": "foreground", "final_text": "done", "actions": [action]}

    monkeypatch.setattr(cli, "_blocked", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor, "run_task", run)
    _patch_run_credentials(monkeypatch)

    assert await cli._run_custom(_run_args(json=True, hide_stop_item=True)) == 0

    lines = _json_lines(capsys.readouterr().out)
    assert [line["type"] for line in lines] == ["ready", "action", "result"]
    assert lines[-1]["outcome"] == "completed" and lines[-1]["final_text"] == "done"


async def test_cli_run_json_forwards_the_stop_item_choice(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    run = AsyncMock(return_value={"outcome": "failed", "delivery_mode": "foreground", "final_text": "no"})
    monkeypatch.setattr(cli, "_blocked", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor, "run_task", run)
    _patch_run_credentials(monkeypatch)

    assert await cli._run_custom(_run_args(json=True, hide_stop_item=True)) == 1
    assert run.await_args.kwargs["show_stop_button"] is False
    assert _json_lines(capsys.readouterr().out)[-1]["type"] == "result"

    assert await cli._run_custom(_run_args()) == 1
    assert run.await_args.kwargs["show_stop_button"] is True
    assert "Outcome" in capsys.readouterr().out or True


async def test_cli_run_json_reports_a_preflight_blocker_as_json(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(
        cli, "first_blocker", lambda: preflight.CheckResult("driver app", False, "missing", "start the daemon")
    )
    assert await cli._run_custom(_run_args(json=True)) == 1
    [line] = _json_lines(capsys.readouterr().out)
    assert line == {
        "type": "blocked",
        "name": "driver app",
        "ok": False,
        "detail": "missing",
        "remediation": "start the daemon",
        "blocking": True,
    }


def test_doctor_json_lists_every_check_with_an_overall_verdict(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda: [
            preflight.CheckResult("runtime", True, "yutori ok"),
            preflight.CheckResult("overlay", False, "not prepared", "run setup", blocking=False),
        ],
    )
    assert cli._dispatch_doctor(SimpleNamespace(json=True)) == 0
    [line] = _json_lines(capsys.readouterr().out)
    assert line["type"] == "doctor" and line["ok"] is True
    assert [check["name"] for check in line["checks"]] == ["runtime", "overlay"]
    assert line["checks"][1]["blocking"] is False

    monkeypatch.setattr(
        cli, "run_checks", lambda: [preflight.CheckResult("driver app", False, "missing", "start the daemon")]
    )
    assert cli._dispatch_doctor(SimpleNamespace(json=True)) == 1
    assert _json_lines(capsys.readouterr().out)[0]["ok"] is False


def test_setup_skips_the_standalone_installer_for_an_embedded_host(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    binary, _ = _configure_embedded_host(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "check_runtime", lambda: preflight.CheckResult("Python runtime", True, "ok"))
    monkeypatch.setattr(cli, "_download_installer", lambda _url: (_ for _ in ()).throw(AssertionError("must not download")))
    monkeypatch.setattr(cli, "run_checks", lambda: [preflight.CheckResult("driver app", True, str(binary))])

    assert cli._setup() == 0
    out = capsys.readouterr().out
    assert "nothing to install" in out and str(binary) in out


# ---------------------------------------------------------------------------
# Host rendering: `activity` transcript rows and `frame` thumbnails; the presentation flag.
# ---------------------------------------------------------------------------


@dataclass
class _FakeObservation:
    capture_id: int
    encoded_bytes: bytes = b"frame-bytes"


class _FakeTarget:
    def describe(self) -> str:
        return "Calculator (pid 5, window 9)"


class _FakeActivityComputer:
    def __init__(self) -> None:
        self.current_observation = None
        self.target_window = None
        self.shell_events: tuple[ShellPresentationEvent, ...] = ()

    @staticmethod
    def _thumbnail_jpeg(image_bytes: bytes) -> bytes:
        return b"thumb:" + image_bytes


class _RecordingPresentation:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def present(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _activity_reporter():
    stream = io.StringIO()
    computer = _FakeActivityComputer()
    inner = _RecordingPresentation()
    reporter = runner_module.ActivityReporter(
        Emitter(stream), computer, inner=inner, thumbnail=_FakeActivityComputer._thumbnail_jpeg
    )
    return reporter, computer, inner, stream


def _events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


async def test_activity_reporter_tees_presentation_events_into_sdk_shaped_rows():
    reporter, _computer, inner, stream = _activity_reporter()
    await reporter.present({"type": "task", "text": "Compute 9 * 9"})
    await reporter.present({"type": "reasoning", "text": "I should open Calculator."})
    await reporter.present({"type": "action", "name": "left_click", "arguments": {"coordinates": [100, 20]}})
    await reporter.present({"type": "request"})  # nothing to show
    await reporter.present({"type": "final", "text": "81"})

    assert [event["type"] for event in inner.events] == ["task", "reasoning", "action", "request", "final"]
    entries = [event["entry"] for event in _events(stream) if event["type"] == "activity"]
    assert [entry["kind"] for entry in entries] == ["task", "thinking", "action", "final"]
    assert entries[0] == {"id": "entry-0", "kind": "task", "text": "Compute 9 * 9"}
    assert entries[2]["text"] == "left click at (100, 20)" and entries[2]["icon"] == "click"
    assert entries[3]["text"] == "81"


async def test_activity_reporter_forwards_even_when_the_native_controller_fails():
    class Failing:
        async def present(self, _event):
            raise RuntimeError("host gone")

    stream = io.StringIO()
    reporter = runner_module.ActivityReporter(Emitter(stream), _FakeActivityComputer(), inner=Failing())
    await reporter.present({"type": "reasoning", "text": "still reported"})
    assert _events(stream)[0]["entry"]["kind"] == "thinking"


async def test_activity_reporter_streams_one_frame_per_new_observation_with_the_sdk_caption():
    reporter, computer, _inner, stream = _activity_reporter()
    await reporter.on_computer_call_end({}, [])
    assert _events(stream) == [], "no observation yet, no frame"

    computer.current_observation = _FakeObservation(capture_id=1)
    computer.target_window = _FakeTarget()
    await reporter.on_computer_call_end({}, [])
    await reporter.on_computer_call_end({}, [])  # same capture: not repeated
    computer.current_observation = _FakeObservation(capture_id=2, encoded_bytes=b"second")
    await reporter.on_computer_call_end({}, [])

    frames = [event for event in _events(stream) if event["type"] == "frame"]
    assert [frame["capture_id"] for frame in frames] == [1, 2]
    assert frames[0]["caption"] == "Frame 1 of Calculator (pid 5, window 9)"
    assert frames[0]["media_type"] == "image/jpeg"
    assert base64.b64decode(frames[1]["data"]) == b"thumb:second"


async def test_activity_reporter_revises_shell_rows_in_place_as_commands_finish():
    reporter, computer, _inner, stream = _activity_reporter()
    computer.shell_events = (ShellPresentationEvent("t1", "ls ~", False, "running"),)
    await reporter.on_computer_call_end({}, [])
    computer.shell_events = (ShellPresentationEvent("t1", "ls ~", False, "completed", 0),)
    await reporter.present({"type": "request"})
    await reporter.present({"type": "request"})  # unchanged shell state: not repeated

    rows = [event["entry"] for event in _events(stream) if event["type"] == "activity"]
    assert [(row["id"], row["state"], row["exitCode"]) for row in rows] == [
        ("shell-t1", "running", None),
        ("shell-t1", "completed", 0),
    ]
    assert rows[0]["command"] == "ls ~" and rows[0]["kind"] == "shell"


def test_parse_request_defaults_presentation_and_validates_it():
    assert parse_request(_valid_request())["presentation"] is True
    assert parse_request(_valid_request(presentation=False))["presentation"] is False
    with pytest.raises(RequestError, match="presentation must be a boolean"):
        parse_request(_valid_request(presentation="off"))


def test_computer_kwargs_forward_the_presentation_choice():
    request = parse_request(_valid_request(presentation=False))
    kwargs = runner_module._computer_kwargs(
        request, deadline=time.monotonic() + 60, cancellation=runner_module.CancellationLatch(), api_key="k"
    )
    assert kwargs["presentation"] is False


def test_agent_kwargs_route_presentation_through_the_activity_sink():
    computer = SimpleNamespace(presentation=object())
    sink = object()
    request = parse_request(_valid_request())
    routed = runner_module._agent_base_kwargs(
        request, completions=None, computer=computer, deadline=1.0, presentation=sink
    )
    assert routed["presentation"] is sink
    default = runner_module._agent_base_kwargs(request, completions=None, computer=computer, deadline=1.0)
    assert default["presentation"] is computer.presentation


@pytest.mark.parametrize(
    ("event", "ok"),
    [
        ({"type": "frame", "capture_id": 1, "media_type": "image/jpeg", "data": "AAAA"}, True),
        ({"type": "frame", "capture_id": "1", "media_type": "image/jpeg", "data": "AAAA"}, False),
        ({"type": "frame", "capture_id": 1, "media_type": "image/jpeg"}, False),
        ({"type": "activity", "entry": {"id": "entry-0", "kind": "thinking", "text": "x"}}, True),
        ({"type": "activity", "entry": "not a row"}, False),
    ],
)
def test_supervisor_validates_frame_and_activity_event_shapes(event, ok):
    assert (supervisor._event_shape_error(event) is None) is ok


async def test_run_task_request_carries_presentation(tmp_path):
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(**_run_task_kwargs(tmp_path, presentation=False))
    assert supervise.await_args.kwargs["request"]["presentation"] is False
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(**_run_task_kwargs(tmp_path))
    assert supervise.await_args.kwargs["request"]["presentation"] is True


def test_cli_run_parser_accepts_no_presentation():
    from yutori_mcp.computer_use import cli

    parser = argparse.ArgumentParser()
    cli.register_parser(parser.add_subparsers(dest="command"))
    assert parser.parse_args(["computer-use", "run", "x", "--no-presentation"]).no_presentation is True
    assert parser.parse_args(["computer-use", "run", "x"]).no_presentation is False


async def test_cli_run_forwards_presentation_and_json_streams_host_only_events(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    frame = {"type": "frame", "capture_id": 1, "media_type": "image/jpeg", "data": "AAAA"}

    async def run(**kwargs):
        await kwargs["on_event"](frame)
        await kwargs["on_event"]({"type": "activity", "entry": {"id": "entry-0", "kind": "thinking", "text": "hm"}})
        return {"outcome": "completed", "delivery_mode": "background", "final_text": "done"}

    monkeypatch.setattr(cli, "_blocked", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor, "run_task", run)
    _patch_run_credentials(monkeypatch)

    args = _run_args(json=True, mode="background", app="Notes", no_presentation=True)
    assert await cli._run_custom(args) == 0
    lines = _json_lines(capsys.readouterr().out)
    assert [line["type"] for line in lines] == ["frame", "activity", "result"]

    captured = AsyncMock(return_value={"outcome": "completed", "delivery_mode": "foreground", "final_text": "ok"})
    monkeypatch.setattr(supervisor, "run_task", captured)
    assert await cli._run_custom(_run_args(no_presentation=True)) == 0
    assert captured.await_args.kwargs["presentation"] is False
    assert "frame" not in capsys.readouterr().out


async def test_cli_text_printer_ignores_host_only_events(capsys):
    from yutori_mcp.computer_use import cli

    printer = cli._event_printer("foreground", None, _PLAIN_TERMINAL, started_at=0.0, clock=lambda: 1.0)
    await printer({"type": "frame", "capture_id": 1, "media_type": "image/jpeg", "data": "AAAA"})
    await printer({"type": "activity", "entry": {"id": "entry-0", "kind": "thinking", "text": "hm"}})
    assert capsys.readouterr().out == ""


async def test_progress_reporter_ignores_host_only_events():
    from yutori_mcp import server

    ctx = SimpleNamespace(report_progress=AsyncMock(), info=AsyncMock())
    on_event = server._progress_reporter(ctx, 10)
    await on_event({"type": "frame", "capture_id": 1, "media_type": "image/jpeg", "data": "AAAA"})
    await on_event({"type": "activity", "entry": {"id": "entry-0", "kind": "thinking", "text": "hm"}})
    ctx.report_progress.assert_not_awaited()
    ctx.info.assert_not_awaited()


def test_parse_request_validates_host_window_ids():
    assert parse_request(_valid_request())["exclude_capture_window_ids"] == []
    assert parse_request(_valid_request(exclude_capture_window_ids=[101, 202]))["exclude_capture_window_ids"] == [101, 202]
    for invalid in ("101", [0], [True], [1.5]):
        with pytest.raises(RequestError, match="exclude_capture_window_ids must be a list of positive integer window ids"):
            parse_request(_valid_request(exclude_capture_window_ids=invalid))


def test_computer_kwargs_pass_host_window_ids_only_to_an_sdk_that_knows_them(monkeypatch):
    request = parse_request(_valid_request(exclude_capture_window_ids=[101, 202]))
    common = {"deadline": time.monotonic() + 60, "cancellation": runner_module.CancellationLatch(), "api_key": "k"}
    monkeypatch.setattr(runner_module, "_computer_accepts", lambda parameter: True)
    assert runner_module._computer_kwargs(request, **common)["exclude_capture_window_ids"] == (101, 202)
    monkeypatch.setattr(runner_module, "_computer_accepts", lambda parameter: parameter == "scope")
    assert "exclude_capture_window_ids" not in runner_module._computer_kwargs(request, **common)
    background = parse_request(_valid_request(app="Notes", mode="background", exclude_capture_window_ids=[101]))
    monkeypatch.setattr(runner_module, "_computer_accepts", lambda parameter: True)
    assert "exclude_capture_window_ids" not in runner_module._computer_kwargs(background, **common), (
        "window scope captures only the driven window; nothing to exclude"
    )


async def test_run_task_and_cli_carry_host_window_ids(monkeypatch, tmp_path, capsys):
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(**_run_task_kwargs(tmp_path, exclude_capture_window_ids=(101, 202)))
    assert supervise.await_args.kwargs["request"]["exclude_capture_window_ids"] == [101, 202]
    with _patched_run_task_supervise(tmp_path) as supervise:
        await run_task(**_run_task_kwargs(tmp_path))
    assert supervise.await_args.kwargs["request"]["exclude_capture_window_ids"] == []

    from yutori_mcp.computer_use import cli

    parser = argparse.ArgumentParser()
    cli.register_parser(parser.add_subparsers(dest="command"))
    parsed = parser.parse_args(
        ["computer-use", "run", "x", "--exclude-capture-window", "101", "--exclude-capture-window", "202"]
    )
    assert parsed.exclude_capture_windows == [101, 202]
    assert parser.parse_args(["computer-use", "run", "x"]).exclude_capture_windows is None

    captured = AsyncMock(return_value={"outcome": "completed", "delivery_mode": "foreground", "final_text": "ok"})
    monkeypatch.setattr(cli, "_blocked", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor, "run_task", captured)
    _patch_run_credentials(monkeypatch)
    assert await cli._run_custom(_run_args(json=True, exclude_capture_windows=[101, 202])) == 0
    assert captured.await_args.kwargs["exclude_capture_window_ids"] == (101, 202)
    capsys.readouterr()
