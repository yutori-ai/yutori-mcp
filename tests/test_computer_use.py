from __future__ import annotations

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
from urllib.error import HTTPError
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError
from yutori.navigator.macos import MacOSPresentationStatus, ShellPresentationEvent
from yutori.navigator.macos.transport import CuaDriverToolError, CuaDriverUncertainActionError

from yutori_mcp.computer_use import preflight, runner as runner_module, supervisor
from yutori_mcp.computer_use.app import pick_best_window, prepare_app
from yutori_mcp.computer_use.constants import (
    DRIVER_VERSION,
    MCP_VERSION,
    PROTOCOL_VERSION,
    SDK_ARTIFACT_SHA256,
    SDK_INSTALLATION_SHA256,
    SDK_PROVENANCE_SHA256,
    SDK_VERSION,
    TOOL_SET,
)
from yutori_mcp.computer_use.lock import ComputerUseBusyError, DesktopLock
from yutori_mcp.computer_use.result import format_result, redact
from yutori_mcp.computer_use.runner import (
    ActionReporter,
    Emitter,
    RequestError,
    RunGuard,
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
from yutori_mcp.schemas import COMPUTER_USE_DEFAULT_MINUTES, ComputerUseTaskInput


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


def test_main_applies_explicit_environment_before_computer_use_dispatch(monkeypatch):
    import yutori_mcp.computer_use.cli as computer_use_cli
    from yutori_mcp import server

    observed: dict[str, str | None] = {}

    def record_dispatch(_command: str, _args: object) -> int:
        observed["environment"] = os.environ.get("YUTORI_ENV")
        return 0

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

    observed: dict[str, str | None] = {}

    def record_dispatch(_command: str, _args: object) -> int:
        observed["environment"] = os.environ.get("YUTORI_ENV")
        return 0

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

    observed: dict[str, str | None] = {}

    def record_dispatch(_command: str, _args: object) -> int:
        observed["environment"] = os.environ.get("YUTORI_ENV")
        return 0

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
        {
            "type": "action",
            "index": 1,
            "tool": "computer_batch",
            "status": "executed",
            "raw_status": "confirmed",
            "delivery_mode": "foreground",
            "route": "pixel",
            "refusal_code": None,
            "elapsed_ms": 42,
        },
        _result_event(),
    ]
    process = _Process(_stream(*(json.dumps(event) for event in events)), _stream(""))
    seen: list[dict] = []

    async def on_event(event):
        seen.append(event)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
            on_event=on_event,
        )
    assert [event["type"] for event in seen] == ["ready", "action"]
    assert result["actions"] == [events[1]]


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

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )

    assert result["outcome"] == "failed"
    assert "exceeded" in result["final_text"]


async def test_supervisor_rejects_runner_provenance_drift():
    process = _Process(
        _stream(json.dumps(_ready_event(sdk_version="0.8.1"))),
        _stream(""),
    )

    async def stop(_process):
        process.returncode = -signal.SIGTERM

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch("yutori_mcp.computer_use.supervisor._stop_process_group", side_effect=stop),
    ):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
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
        {"type": "result", "outcome": "completed"},
    ],
)
async def test_supervisor_rejects_malformed_events(event):
    process = _Process(_stream(json.dumps(_ready_event()), json.dumps(event)), _stream(""))
    process.returncode = 0
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
    assert result["outcome"] == "failed"
    assert "malformed" in result["final_text"]


async def test_supervisor_rejects_invalid_utf8_in_an_otherwise_valid_event():
    stream = asyncio.StreamReader()
    stream.feed_data(json.dumps(_ready_event()).encode() + b"\n")
    stream.feed_data(b'{"type":"result","outcome":"completed","delivery_mode":"foreground","final_text":"\xff"}\n')
    stream.feed_eof()
    process = _Process(stream, _stream(""))
    process.returncode = 0
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
    assert result["outcome"] == "failed"
    assert "invalid JSON" in result["final_text"]


async def test_supervisor_overwrites_child_supplied_actions():
    action = {
        "type": "action",
        "index": 0,
        "tool": "left_click",
        "status": "executed",
        "raw_status": "confirmed",
        "delivery_mode": "foreground",
        "route": "pixel",
        "refusal_code": None,
    }
    process = _Process(
        _stream(
            json.dumps(_ready_event()),
            json.dumps(action),
            json.dumps(_result_event(actions=[{"tool": "forged"}])),
        ),
        _stream(""),
    )
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
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
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
    assert result["outcome"] == "failed"
    assert "after its terminal event" in result["final_text"]


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

    async def stop(_process):
        process.returncode = -signal.SIGTERM

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch("yutori_mcp.computer_use.supervisor._stop_process_group", side_effect=stop) as stopped,
    ):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="secret",
            deadline=time.monotonic() + 0.01,
        )
    assert result["outcome"] == "limit"
    stopped.assert_awaited_once_with(process)


async def test_supervisor_eof_does_not_bypass_the_deadline():
    process = _Process(_stream(json.dumps(_ready_event()), json.dumps(_result_event())), _stream(""))

    async def wait_forever():
        await asyncio.Future()

    async def stop(_process):
        process.returncode = -signal.SIGTERM

    process.wait = wait_forever
    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch("yutori_mcp.computer_use.supervisor._stop_process_group", side_effect=stop) as stopped,
    ):
        result = await _supervise(
            command=python_runner_command(),
            request={"type": "run"},
            api_key="secret",
            deadline=time.monotonic() + 0.01,
        )
    assert result["outcome"] == "limit"
    stopped.assert_awaited_once_with(process)


async def test_supervisor_cancellation_is_aborted_and_stops_group():
    process = _Process(asyncio.StreamReader(), asyncio.StreamReader())

    async def stop(_process):
        process.returncode = -signal.SIGTERM

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch("yutori_mcp.computer_use.supervisor._stop_process_group", side_effect=stop) as stopped,
    ):
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


def test_runner_removes_api_key_before_spawning_a_real_shell(monkeypatch):
    secret = "yt-child-shell-secret"
    monkeypatch.setenv("YUTORI_API_KEY", secret)
    assert runner_module._take_api_key() == secret
    assert "YUTORI_API_KEY" not in os.environ
    subprocess.run(["/bin/sh", "-c", 'test -z "${YUTORI_API_KEY+x}"'], check=True)


def test_python_runner_is_isolated_and_has_no_node_path():
    assert python_runner_command() == [sys.executable, "-I", "-m", "yutori_mcp.computer_use.runner"]
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


async def test_run_task_uses_only_python_runner_and_sdk_driver_discovery(tmp_path):
    driver = tmp_path / "cua-driver"
    driver.write_text("")
    supervise = AsyncMock(return_value={"outcome": "completed"})
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_cua_driver", return_value=driver),
    ):
        result = await run_task(**_run_task_kwargs(tmp_path))
    assert result["outcome"] == "completed"
    assert supervise.await_args.kwargs["command"] == python_runner_command()
    request = supervise.await_args.kwargs["request"]
    assert request["model"] == "n2"
    assert "driver_path" not in request and "harness" not in request


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

    monkeypatch.setattr(lock_module, "DesktopLock", lambda: lock)
    monkeypatch.setattr(preflight, "first_blocker", first_blocker)
    monkeypatch.setattr(supervisor, "run_task", run_with_lock)
    monkeypatch.setattr(
        "yutori_mcp.adapter.resolve_run_credentials",
        lambda: ("api-key", "https://api.yutori.com/v1"),
    )

    result, raw = await server._handle_computer_use(None, {"task": "open calculator"})
    assert result["outcome"] == "completed"
    assert raw == {}
    assert lock._file is None


def test_runtime_constants_select_latest_python_surface():
    assert TOOL_SET == "computer_use_tools-20260815"
    assert SDK_VERSION == "0.9.2"
    assert all(len(digest) == 64 for digest in (SDK_ARTIFACT_SHA256, SDK_INSTALLATION_SHA256, SDK_PROVENANCE_SHA256))
    assert '"yutori==0.9.2"' in Path(__file__).parents[1].joinpath("pyproject.toml").read_text()


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


def test_first_blocker_skips_warnings(monkeypatch):
    def warning():
        return preflight.CheckResult("overlay", False, "missing", "setup", blocking=False)

    def blocker():
        return preflight.CheckResult("driver", False, "missing", "setup")

    monkeypatch.setattr(preflight, "checks_for", lambda: (warning, blocker))
    assert preflight.first_blocker().name == "driver"


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


def test_setup_prepares_overlay_after_driver_permissions(monkeypatch, tmp_path):
    from yutori_mcp.computer_use import cli

    driver = tmp_path / "cua-driver"
    driver.write_text("")
    monkeypatch.setattr(cli, "check_runtime", lambda: preflight.CheckResult("runtime", True, "ok"))
    monkeypatch.setattr(cli, "_download_installer", lambda _: b"installer")
    monkeypatch.setattr(cli, "DRIVER_INSTALLER_SHA256", hashlib.sha256(b"installer").hexdigest())
    monkeypatch.setattr(cli, "find_cua_driver", lambda: driver)
    prepared = SimpleNamespace(binary=tmp_path / "overlay")
    prepare = patch("yutori.navigator.macos.prepare_macos_overlay", return_value=prepared)
    with prepare as prepare_overlay, patch.object(cli.subprocess, "run"), patch.object(cli, "_doctor", return_value=0):
        assert cli._setup() == 0
    prepare_overlay.assert_called_once_with()


def test_setup_treats_overlay_file_errors_as_warnings(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    driver = tmp_path / "cua-driver"
    driver.write_text("")
    monkeypatch.setattr(cli, "check_runtime", lambda: preflight.CheckResult("runtime", True, "ok"))
    monkeypatch.setattr(cli, "_download_installer", lambda _: b"installer")
    monkeypatch.setattr(cli, "DRIVER_INSTALLER_SHA256", hashlib.sha256(b"installer").hexdigest())
    monkeypatch.setattr(cli, "find_cua_driver", lambda: driver)
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
    monkeypatch.setattr("yutori.navigator.macos.MacOSComputer", FakeComputer)
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


async def test_smoke_reserves_desktop_before_mechanical_check(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    lock_path = tmp_path / "desktop.lock"
    mechanical_check = AsyncMock()
    preflight = Mock()
    monkeypatch.setattr(cli, "DesktopLock", lambda: DesktopLock(lock_path))
    monkeypatch.setattr(cli, "first_blocker", preflight)
    monkeypatch.setattr(cli, "_mechanical_calculator_check", mechanical_check)

    with DesktopLock(lock_path):
        assert await cli._smoke_live() == 1

    preflight.assert_not_called()
    mechanical_check.assert_not_awaited()
    assert "Another computer-use task controls this Mac" in capsys.readouterr().out


async def test_smoke_does_not_print_mismatched_clipboard_contents(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    secret = "clipboard-secret-value"
    monkeypatch.setattr(cli, "DesktopLock", lambda: DesktopLock(tmp_path / "desktop.lock"))
    monkeypatch.setattr(cli, "first_blocker", lambda: None)
    monkeypatch.setattr(cli, "_mechanical_calculator_check", AsyncMock(return_value=secret))

    assert await cli._smoke_live() == 1

    output = capsys.readouterr().out
    assert "did not match '42'" in output
    assert secret not in output


async def test_smoke_allows_two_minutes_for_live_check(monkeypatch, tmp_path):
    from yutori_mcp.computer_use import cli

    run = AsyncMock(return_value={"outcome": "completed"})
    monkeypatch.setattr(cli, "DesktopLock", lambda: DesktopLock(tmp_path / "desktop.lock"))
    monkeypatch.setattr(cli, "first_blocker", lambda: None)
    monkeypatch.setattr(cli, "_mechanical_calculator_check", AsyncMock(return_value="42"))
    monkeypatch.setattr(cli, "run_task", run)
    monkeypatch.setattr(cli, "format_result", lambda _result: "complete")
    monkeypatch.setattr(
        "yutori_mcp.adapter.resolve_run_credentials",
        lambda: ("dev-key", "https://api.yutori.com/v1"),
    )

    assert await cli._smoke_live() == 0

    assert run.await_args.kwargs["minutes"] == 2
    assert run.await_args.kwargs["lock"]._depth == 0


def test_pick_best_window_excludes_helper_strips():
    strips = [{"window_id": index, "bounds": {"width": 600, "height": 20}, "z_index": 9} for index in range(4)]
    main = {"window_id": 99, "bounds": {"width": 400, "height": 500}, "z_index": 1}
    assert pick_best_window(strips + [main])["window_id"] == 99
    assert pick_best_window(strips)["window_id"] == 0


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
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "com.apple.calculator", "https://example.com")
    assert target == {"name": "Calculator", "pid": 42}
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
        wait=AsyncMock(),
    )

    target = await prepare_app(computer, "Finder", None)

    assert target == {"name": "Finder", "pid": 42}
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
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "Finder", None)
    assert target == {"name": "Finder", "pid": 42}
    computer._call_tool.assert_awaited_once_with("list_apps", {}, read_only=True)
    computer.bring_to_front.assert_awaited_once_with(42, None)


async def test_prepare_app_does_not_retry_uncertain_fronting():
    computer = SimpleNamespace(
        launch_app=AsyncMock(return_value={"pid": 42, "name": "Calculator"}),
        bring_to_front=AsyncMock(side_effect=CuaDriverUncertainActionError("acknowledgement lost")),
        wait=AsyncMock(),
    )
    target = await prepare_app(computer, "Calculator", None)
    assert target == {"name": "Calculator", "pid": 42}
    computer.bring_to_front.assert_awaited_once_with(42, None)
    computer.wait.assert_awaited_once_with(800)


async def test_smoke_reports_preflight_detail_and_fix(monkeypatch, tmp_path, capsys):
    from yutori_mcp.computer_use import cli

    blocker = preflight.CheckResult(
        "Yutori API",
        False,
        "Invalid model",
        "Use a supported model.",
    )
    monkeypatch.setattr(cli, "DesktopLock", lambda: DesktopLock(tmp_path / "desktop.lock"))
    monkeypatch.setattr(cli, "first_blocker", lambda: blocker)
    mechanical = AsyncMock()
    monkeypatch.setattr(cli, "_mechanical_calculator_check", mechanical)

    assert await cli._smoke_live() == 1
    assert capsys.readouterr().out == "Invalid model Fix: Use a supported model.\n"
    mechanical.assert_not_awaited()


def _valid_request(**overrides):
    request = {
        "protocol_version": 1,
        "type": "run",
        "task": "open calculator",
        "app": None,
        "start_url": None,
        "deadline_ms": 1_000_000,
        "max_steps": 10,
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
        ({"protocol_version": 2}, "UNSUPPORTED_PROTOCOL_VERSION"),
        ({"type": "walk"}, "INVALID_REQUEST"),
        ({"task": ""}, "INVALID_REQUEST"),
        ({"start_url": "https://x", "app": None}, "INVALID_REQUEST"),
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


class _CollectStream:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, data):
        self.lines.append(data)

    def flush(self):
        pass


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


class _FakeComputer:
    instances: list[_FakeComputer] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.presentation = _FakePresentation()
        self.presentation_status = MacOSPresentationStatus(True, True, "active", "yutori", codec="webp")
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
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def aclose(self):
        self.closed = True


class _FakeAgent:
    instances: list[_FakeAgent] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
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
        item = {"name": "left_click", "arguments": {"coordinates": [1, 1]}}
        for callback in self.callbacks:
            if hasattr(callback, "on_computer_call_start"):
                await callback.on_computer_call_start(item)
        for callback in self.callbacks:
            if hasattr(callback, "on_computer_call_end"):
                await callback.on_computer_call_end(item, [{"output": "ok"}])
        yield {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Done [DONE]"}]}]}


async def test_run_request_wires_sdk_runtime_and_reports_effective_state(monkeypatch):
    _FakeComputer.instances.clear()
    _FakeAgent.instances.clear()
    monkeypatch.setattr(runner_module, "MacOSComputer", _FakeComputer)
    monkeypatch.setattr(runner_module, "N2ComputerAgent", _FakeAgent)
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() + 60) * 1000)))
    outcome = await runner_module.run_request(request, Emitter(stream), "yt-secret")
    assert outcome == "completed"
    computer = _FakeComputer.instances[-1]
    agent = _FakeAgent.instances[-1]
    assert computer.kwargs == {
        "presentation": True,
        "allow_local_shell": True,
        "execution_deadline": pytest.approx(computer.kwargs["execution_deadline"]),
        "cancellation": computer.kwargs["cancellation"],
        "known_secrets": ("yt-secret",),
    }
    assert agent.kwargs["tool_set"] == TOOL_SET
    assert agent.kwargs["presentation"] is computer.presentation
    assert agent.kwargs["supports_click_modifiers"] is True
    assert "Shell commands run headlessly" in agent.kwargs["instructions"]
    assert "Do not use osascript" in agent.kwargs["instructions"]
    assert "Never inspect a GUI application's databases" in agent.kwargs["instructions"]
    assert "use at most three shell calls for research" in agent.kwargs["instructions"]
    assert "never inspect browser profile databases" in agent.kwargs["instructions"]
    assert "stop immediately instead of trying alternate URLs" in agent.kwargs["instructions"]
    assert "Never ask them to give you a password" in agent.kwargs["instructions"]
    assert "Do not install software or packages" in agent.kwargs["instructions"]
    assert computer.closed
    result = json.loads(stream.lines[-1])
    assert result["final_text"] == "Done"
    assert result["reasoning_overlay_requested"] is True
    assert result["reasoning_overlay_effective"] is True
    assert result["codec"] == "webp"
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
    monkeypatch.setattr(runner_module, "MacOSComputer", _FakeComputer)
    monkeypatch.setattr(runner_module, "N2ComputerAgent", _CancelledAgent)
    stream = _CollectStream()
    request = parse_request(_valid_request(deadline_ms=int((time.time() + 60) * 1000)))

    assert await runner_module.run_request(request, Emitter(stream), "yt-secret") == "aborted"

    events = [json.loads(line) for line in stream.lines]
    assert [event["type"] for event in events[-2:]] == ["action", "result"]
    assert events[-2]["raw_status"] == "interrupted"
    assert events[-2]["status"] == "uncertain"


def test_format_result_handles_python_runtime_action_fields():
    text = format_result(
        {
            "outcome": "completed",
            "actions": [
                {
                    "index": 0,
                    "tool": "bash",
                    "status": "executed",
                    "raw_status": "confirmed",
                    "delivery_mode": "foreground",
                    "route": "pixel",
                    "refusal_code": None,
                    "elapsed_ms": 10,
                    "duration_ms": 5,
                    "command": "echo safe",
                    "run_in_background": True,
                    "background_task_id": "bash-1",
                }
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
