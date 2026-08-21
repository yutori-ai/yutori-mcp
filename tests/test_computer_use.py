from __future__ import annotations

import asyncio
import json
import os
import signal
import pathlib
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from yutori_mcp.computer_use import preflight
from yutori_mcp.computer_use.lock import ComputerUseBusyError, DesktopLock
from yutori_mcp.computer_use.runtime import RuntimeValidationError, load_runtime
from yutori_mcp.computer_use import supervisor
from yutori_mcp.computer_use.supervisor import _stop_process_group, _supervise
from yutori_mcp.schemas import ComputerUseTaskInput


@pytest.mark.parametrize("minutes", [0.9, 15.1])
def test_schema_rejects_minutes(minutes):
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", minutes=minutes)


@pytest.mark.parametrize("max_steps", [0, 101])
def test_schema_rejects_max_steps(max_steps):
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", max_steps=max_steps)


def test_schema_requires_app_for_url_and_forbids_unknowns():
    with pytest.raises(ValidationError, match="start_url requires app"):
        ComputerUseTaskInput(task="x", start_url="https://example.com")
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", surprise=True)


@pytest.mark.parametrize(
    "platform,environment,expected",
    [("linux", "dev", False), ("darwin", "prod", False), ("darwin", "dev", True)],
)
def test_registration_gate(platform, environment, expected):
    with (
        patch("yutori_mcp.server.sys.platform", platform),
        patch.dict("os.environ", {"YUTORI_ENV": environment}),
    ):
        from yutori_mcp.server import _computer_use_enabled

        assert _computer_use_enabled() is expected


@pytest.mark.parametrize(
    "platform,environment,expected",
    [("linux", "dev", False), ("darwin", "prod", False), ("darwin", "dev", True)],
)
def test_tool_listing_gate(platform, environment, expected):
    script = f"""
import asyncio, importlib, os, sys
import yutori_mcp.server as server
sys.platform = {platform!r}
os.environ['YUTORI_ENV'] = {environment!r}
server = importlib.reload(server)
names = [tool.name for tool in asyncio.run(server.mcp.list_tools())]
raise SystemExit(0 if ('run_computer_use_task' in names) is {expected!r} else 1)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run([sys.executable, "-c", script], env=env, check=False)
    assert result.returncode == 0


def test_lock_rejects_second_owner_and_releases(tmp_path):
    path = tmp_path / "desktop.lock"
    with DesktopLock(path), pytest.raises(ComputerUseBusyError), DesktopLock(path):
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


def _runtime(protocol=1, verified=True):
    return SimpleNamespace(PROTOCOL_VERSION=protocol, verify_runner=lambda: verified)


def test_runtime_protocol_mismatch_has_one_remediation():
    with (
        patch(
            "yutori_mcp.computer_use.runtime.import_module", return_value=_runtime(2)
        ),
        pytest.raises(RuntimeValidationError) as error,
    ):
        load_runtime()
    assert "protocol mismatch" in str(error.value)
    assert str(error.value).count(error.value.remediation) == 1


def test_runtime_hash_mismatch_has_one_remediation():
    with (
        patch(
            "yutori_mcp.computer_use.runtime.import_module",
            return_value=_runtime(verified=False),
        ),
        pytest.raises(RuntimeValidationError) as error,
    ):
        load_runtime()
    assert "integrity" in str(error.value)
    assert str(error.value).count(error.value.remediation) == 1


def test_installer_checksum_aborts_before_execution(monkeypatch):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(cli, "check_node", lambda: SimpleNamespace(ok=True))
    monkeypatch.setattr(
        cli,
        "get_manifest",
        lambda: {"driver_version": "0.19.3", "driver_installer_sha256": "bad"},
    )
    monkeypatch.setattr(cli, "_download_installer", lambda _: b"installer")
    with patch("yutori_mcp.computer_use.cli.subprocess.run") as run:
        assert cli._setup() == 1
    run.assert_not_called()


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


async def test_supervisor_redacts_key_and_keeps_it_out_of_argv():
    secret = "yt-super-secret"
    process = _Process(
        _stream(json.dumps({"type": "error", "code": "X", "message": secret})),
        _stream(f"diagnostic {secret}"),
    )
    create = AsyncMock(return_value=process)
    with patch("asyncio.create_subprocess_exec", create):
        result = await _supervise(
            node="/node",
            runner="/runner.mjs",
            request={"type": "run"},
            api_key=secret,
            deadline=time.monotonic() + 1,
        )
    assert secret not in json.dumps(result)
    assert secret not in " ".join(create.await_args.args)
    assert process.stdin.data.count(b"\n") == 1


async def test_stop_process_group_escalates_to_kill():
    process = SimpleNamespace(pid=123, returncode=None, wait=AsyncMock(return_value=0))

    def killed(_pid, sig):
        if sig == signal.SIGKILL:
            process.returncode = -signal.SIGKILL

    async def expire(awaitable, _timeout):
        awaitable.close()
        raise TimeoutError

    with (
        patch(
            "yutori_mcp.computer_use.supervisor.os.killpg", side_effect=killed
        ) as kill,
        patch(
            "yutori_mcp.computer_use.supervisor.asyncio.wait_for", side_effect=expire
        ),
    ):
        await _stop_process_group(process)
    assert [call.args[1] for call in kill.call_args_list] == [
        signal.SIGTERM,
        signal.SIGKILL,
    ]


async def test_cancellation_returns_structured_result_and_stops_group():
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    process = _Process(stdout, stderr)
    create = AsyncMock(return_value=process)

    async def stop(_process):
        process.returncode = -signal.SIGTERM

    with (
        patch("asyncio.create_subprocess_exec", create),
        patch(
            "yutori_mcp.computer_use.supervisor._stop_process_group", side_effect=stop
        ) as stopped,
    ):
        task = asyncio.create_task(
            _supervise(
                node="/node",
                runner="/runner",
                request={"type": "run"},
                api_key="secret",
                deadline=time.monotonic() + 60,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        result = await task
    assert result["outcome"] == "failed"
    assert "cancelled" in result["final_text"]
    stopped.assert_awaited()


@pytest.mark.parametrize("command", ["setup", "doctor", "smoke"])
def test_cli_dispatches_commands(command):
    from yutori_mcp.computer_use import cli

    target = {"setup": "_setup", "doctor": "_doctor", "smoke": "asyncio"}[command]
    if command == "smoke":

        def close_and_return(coroutine):
            coroutine.close()
            return 7

        with patch.object(cli.asyncio, "run", side_effect=close_and_return) as called:
            assert cli.dispatch(command) == 7
            called.assert_called_once()
    else:
        with patch.object(cli, target, return_value=7) as called:
            assert cli.dispatch(command) == 7
            called.assert_called_once()


async def test_child_environment_can_resolve_cua_driver_and_sips():
    """The runner execs `cua-driver` and `sips` by bare name.

    An env built from scratch without PATH makes every run die with ENOENT before the model is
    ever called, and no faked-subprocess test would notice.
    """
    process = _Process(
        _stream(
            json.dumps({"type": "result", "outcome": "completed", "final_text": "ok"})
        ),
        _stream(""),
    )
    create = AsyncMock(return_value=process)
    with patch("asyncio.create_subprocess_exec", create):
        await _supervise(
            node="/node",
            runner="/runner.mjs",
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
    child_path = create.await_args.kwargs["env"]["PATH"]
    assert "/usr/bin" in child_path.split(":")
    assert "/opt/homebrew/bin" in child_path.split(":")


def test_child_search_path_prefers_the_resolved_driver_directory(tmp_path):
    driver = tmp_path / "cua-driver"
    driver.write_text("")
    with patch.object(preflight, "DRIVER_PATHS", (driver,)):
        assert preflight.child_search_path().split(":")[0] == str(tmp_path)
        assert preflight.find_cua_driver() == driver


def test_driver_json_reports_a_missing_binary_instead_of_shelling_out():
    with patch.object(preflight, "DRIVER_PATHS", ()):
        with patch.object(preflight.subprocess, "run") as run:
            with pytest.raises(FileNotFoundError):
                preflight._driver_json("status")
            run.assert_not_called()


def test_missing_driver_binary_is_the_reported_blocker_before_the_contract_check():
    with patch.object(preflight, "DRIVER_PATHS", ()):
        result = preflight.check_driver_binary()
    assert not result.ok
    assert result.remediation == "Run: yutori-mcp computer-use setup"
    names = [check.__name__ for check in preflight.CHECKS]
    assert names.index("check_driver_binary") < names.index("check_driver_contract")


@pytest.mark.parametrize(
    "check,patched,value",
    [
        ("check_macos", "platform.system", lambda: "Linux"),
        ("check_architecture", "platform.machine", lambda: "ppc64"),
    ],
)
def test_platform_blockers_each_return_one_remediation(check, patched, value):
    module, attribute = patched.split(".")
    with patch.object(getattr(preflight, module), attribute, value):
        result = getattr(preflight, check)()
    assert not result.ok
    assert result.remediation


def test_env_flag_after_import_still_registers_the_tool():
    """`uvx yutori-mcp --env dev` applies --env after this module is imported.

    Registration that only happened at import left the documented onboarding command
    advertising no computer-use tool at all, while the gate itself reported enabled.
    """
    import yutori_mcp.server as server

    with patch.dict(server._TOOL_HANDLERS, clear=False):
        server._TOOL_HANDLERS.pop("run_computer_use_task", None)
        with patch.object(server.sys, "platform", "darwin"):
            with patch.dict(os.environ, {"YUTORI_ENV": "dev"}):
                assert server._computer_use_enabled()
                server._register_computer_use_tool()
                assert "run_computer_use_task" in server._TOOL_HANDLERS
                listed = [tool.name for tool in asyncio.run(server.mcp.list_tools())]
                assert "run_computer_use_task" in listed
                # Idempotent: main() calls this after the import-time call already ran.
                server._register_computer_use_tool()
    asyncio.run(server.mcp.list_tools())


def test_registration_is_skipped_when_already_registered_regardless_of_gate():
    import yutori_mcp.server as server

    with patch.dict(server._TOOL_HANDLERS, {"run_computer_use_task": lambda *a: None}):
        with patch.object(server, "_computer_use_enabled") as gate:
            server._register_computer_use_tool()
            gate.assert_not_called()


def test_driver_is_found_in_the_installer_default_location(tmp_path):
    """The installer puts the CLI in ~/.local/bin, which the search list originally omitted.

    On a Mac mini with a working driver at ~/.local/bin/cua-driver, find_cua_driver() returned
    None and preflight would have blocked a healthy machine.
    """
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    driver = local_bin / "cua-driver"
    driver.write_text("")
    with patch.object(preflight.Path, "home", staticmethod(lambda: tmp_path)):
        candidates = [
            tmp_path / ".local" / "bin" / "cua-driver",
            preflight.Path("/opt/homebrew/bin/cua-driver"),
        ]
        with patch.object(preflight, "DRIVER_PATHS", tuple(candidates)):
            assert preflight.find_cua_driver() == driver
            assert preflight.check_driver_binary().ok
            assert preflight.child_search_path().split(":")[0] == str(local_bin)


def test_local_bin_precedes_homebrew_in_the_search_order():
    # The installer's location must win over a stale Homebrew copy.
    ordered = [str(p) for p in preflight.DRIVER_PATHS]
    assert ordered[0].endswith("/.local/bin/cua-driver")
    assert any(p.endswith("/opt/homebrew/bin/cua-driver") for p in ordered)


def test_gui_session_reads_the_console_owner_not_this_process():
    """Over SSH the old Quartz probe reported "inactive" on a Mac that was logged in."""
    with patch.object(preflight.subprocess, "run") as run:
        run.return_value = SimpleNamespace(stdout="n2operator\n", returncode=0)
        result = preflight.check_gui_session()
    assert result.ok and "n2operator" in result.detail
    assert run.call_args.args[0][:2] == ["/usr/bin/stat", "-f"]


@pytest.mark.parametrize("owner", ["root", "_windowserver", ""])
def test_gui_session_blocks_only_at_the_login_window(owner):
    with patch.object(preflight.subprocess, "run") as run:
        run.return_value = SimpleNamespace(stdout=owner + "\n", returncode=0)
        assert not preflight.check_gui_session().ok


def test_capture_goes_through_the_driver_not_screencapture(tmp_path):
    driver = tmp_path / "cua-driver"
    driver.write_text("")

    def fake_run(argv, **kwargs):
        # The driver writes the file; this proves we asked IT, not /usr/sbin/screencapture.
        assert argv[0] == str(driver) and argv[1:3] == ["call", "get_desktop_state"]
        pathlib.Path(json.loads(argv[3])["screenshot_out_file"]).write_bytes(
            b"png-bytes"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(preflight, "DRIVER_PATHS", (driver,)):
        with patch.object(preflight.subprocess, "run", side_effect=fake_run):
            assert preflight.check_capture().ok


def test_driver_contract_reports_drift_without_blocking_a_working_driver():
    """0.18.0 drove a full task while the pin read 0.19.3; refusing that would be wrong."""
    with patch.object(preflight, "driver_version", return_value="0.18.0"):
        with patch.object(
            preflight, "get_manifest", return_value={"driver_version": "0.19.3"}
        ):
            result = preflight.check_driver_contract()
    assert result.ok
    assert "0.18.0" in result.detail and "0.19.3" in result.detail


def test_driver_contract_blocks_when_the_driver_cannot_answer():
    with patch.object(preflight, "driver_version", return_value=None):
        assert not preflight.check_driver_contract().ok


def test_driver_version_parses_plain_text_output():
    """`status --json` is not JSON on 0.18.0; --version is the stable surface."""
    with patch.object(
        preflight, "find_cua_driver", return_value=preflight.Path("/x/cua-driver")
    ):
        with patch.object(preflight.subprocess, "run") as run:
            run.return_value = SimpleNamespace(
                stdout="cua-driver 0.18.0\n", returncode=0
            )
            assert preflight.driver_version() == "0.18.0"


@pytest.mark.parametrize(
    "code,expected", [(401, False), (403, False), (400, True), (422, True)]
)
def test_dev_access_treats_only_auth_rejections_as_failure(code, expected):
    """/v1/models 403s for keys that drive n2-preview fine, so probe what a run actually uses."""
    from urllib.error import HTTPError

    with patch.object(
        preflight, "resolve_api_key_for_environment", return_value="yt-test"
    ):
        with patch.object(
            preflight, "urlopen", side_effect=HTTPError("u", code, "m", {}, None)
        ):
            assert preflight.check_dev_access().ok is expected


def test_dev_access_catches_a_billing_error_returned_with_http_200():
    """The API answers insufficient balance with HTTP 200 and an error body.

    A key with no prepaid balance passed this check while every task failed at zero steps with an
    empty stderr, so the status code alone is not enough to tell.
    """
    body = json.dumps(
        {"error": {"message": "Insufficient prepaid balance.", "type": "billing_error"}}
    ).encode()

    class Response:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch.object(
        preflight, "resolve_api_key_for_environment", return_value="yt-test"
    ):
        with patch.object(preflight, "urlopen", return_value=Response()):
            result = preflight.check_dev_access()
    assert not result.ok
    assert "balance" in result.detail
    assert "prepaid balance" in (result.remediation or "")


def test_dev_access_passes_only_on_a_real_completion():
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    class Response:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch.object(
        preflight, "resolve_api_key_for_environment", return_value="yt-test"
    ):
        with patch.object(preflight, "urlopen", return_value=Response()):
            assert preflight.check_dev_access().ok


def test_lock_module_avoids_apis_newer_than_the_declared_python_floor():
    """The package declares requires-python >=3.10 but imported typing.Self, which is 3.11+.

    The import alone broke test collection on 3.10, and nothing caught it until CI ran for the
    first time. Guarding the specific mistake rather than the whole file.
    """
    source = pathlib.Path(preflight.__file__).parent.joinpath("lock.py").read_text()
    assert "from typing import Self" not in source
    assert "-> Self:" not in source


async def test_supervisor_forwards_ready_and_action_events():
    events = [
        {"type": "ready", "protocol_version": 1},
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
        {"type": "result", "outcome": "completed", "final_text": "ok"},
    ]
    process = _Process(
        _stream(*(json.dumps(event) for event in events)), _stream("")
    )
    seen: list[dict] = []

    async def on_event(event):
        seen.append(event)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            node="/node",
            runner="/runner.mjs",
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
            on_event=on_event,
        )
    assert [event["type"] for event in seen] == ["ready", "action"]
    assert result["outcome"] == "completed"
    assert result["actions"] == [events[1]]


async def test_supervisor_survives_raising_event_callback():
    process = _Process(
        _stream(
            json.dumps({"type": "action", "index": 1, "tool": "screenshot",
                        "status": "executed", "raw_status": "confirmed",
                        "delivery_mode": "foreground", "route": "pixel",
                        "refusal_code": None, "elapsed_ms": 5}),
            json.dumps({"type": "result", "outcome": "completed", "final_text": "ok"}),
        ),
        _stream(""),
    )

    async def on_event(_event):
        raise RuntimeError("notification transport died")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            node="/node",
            runner="/runner.mjs",
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
            on_event=on_event,
        )
    assert result["outcome"] == "completed"


async def test_supervisor_disables_hanging_event_callback(monkeypatch):
    """A wedged notification transport must not stall the run past its deadline.

    The first blocked callback is cancelled after the bounded wait and the callback
    is disabled, so later events don't pay the timeout again.
    """
    monkeypatch.setattr(supervisor, "EVENT_CALLBACK_TIMEOUT_SECONDS", 0.05)
    action = {"type": "action", "index": 1, "tool": "screenshot",
              "status": "executed", "raw_status": "confirmed",
              "delivery_mode": "foreground", "route": "pixel",
              "refusal_code": None, "elapsed_ms": 5}
    process = _Process(
        _stream(
            json.dumps(action),
            json.dumps(action | {"index": 2}),
            json.dumps({"type": "result", "outcome": "completed", "final_text": "ok"}),
        ),
        _stream(""),
    )
    invocations = 0

    async def on_event(_event):
        nonlocal invocations
        invocations += 1
        await asyncio.Event().wait()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        result = await _supervise(
            node="/node",
            runner="/runner.mjs",
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 5,
            on_event=on_event,
        )
    assert result["outcome"] == "completed"
    assert invocations == 1


async def test_progress_reporter_formats_ready_and_action_events():
    from yutori_mcp.server import _progress_reporter

    ctx = SimpleNamespace(report_progress=AsyncMock(), info=AsyncMock())
    on_event = _progress_reporter(ctx, max_steps=60)

    await on_event({"type": "ready", "protocol_version": 1})
    ready_message = ctx.info.await_args.args[0]
    assert "60 steps" in ready_message
    assert ctx.report_progress.await_args.kwargs["progress"] == 0

    await on_event(
        {
            "type": "action",
            "index": 7,
            "tool": "computer_batch",
            "status": "refused",
            "refusal_code": "stale_same_turn_action",
            "elapsed_ms": 120,
        }
    )
    action_message = ctx.info.await_args.args[0]
    assert "action #7" in action_message
    assert "computer_batch -> refused" in action_message
    assert "stale_same_turn_action" in action_message
    assert "[120 ms]" in action_message
    assert ctx.report_progress.await_args.kwargs["progress"] == 7


async def test_handle_computer_use_pops_ctx_and_wires_on_event():
    import yutori_mcp.server as server

    run_task = AsyncMock(return_value={"outcome": "completed"})
    ctx = SimpleNamespace(report_progress=AsyncMock(), info=AsyncMock())
    with (
        patch("yutori_mcp.computer_use.preflight.first_blocker", return_value=None),
        patch(
            "yutori_mcp.computer_use.preflight.find_node",
            return_value=pathlib.Path("/node"),
        ),
        patch("yutori_mcp.computer_use.supervisor.run_task", run_task),
        patch(
            "yutori_mcp.credentials.resolve_api_key_for_environment",
            return_value="yt-key",
        ),
        patch("yutori_mcp.server.resolve_base_url", return_value="https://api"),
    ):
        result, _ = await server._handle_computer_use(
            None, {"task": "open calculator", "ctx": ctx}
        )
    assert result == {"outcome": "completed"}
    on_event = run_task.await_args.kwargs["on_event"]
    assert on_event is not None
    await on_event({"type": "action", "index": 1, "tool": "screenshot",
                    "status": "executed", "refusal_code": None, "elapsed_ms": 3})
    ctx.info.assert_awaited()

    run_task.reset_mock()
    with (
        patch("yutori_mcp.computer_use.preflight.first_blocker", return_value=None),
        patch(
            "yutori_mcp.computer_use.preflight.find_node",
            return_value=pathlib.Path("/node"),
        ),
        patch("yutori_mcp.computer_use.supervisor.run_task", run_task),
        patch(
            "yutori_mcp.credentials.resolve_api_key_for_environment",
            return_value="yt-key",
        ),
        patch("yutori_mcp.server.resolve_base_url", return_value="https://api"),
    ):
        await server._handle_computer_use(None, {"task": "open calculator"})
    assert run_task.await_args.kwargs["on_event"] is None
