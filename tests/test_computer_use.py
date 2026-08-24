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
from yutori_mcp.computer_use import runner as runner_module
from yutori_mcp.computer_use import supervisor
from yutori_mcp.computer_use.driver import (
    CuaDriverDesktop,
    DriverCLI,
    DriverError,
    DriverRefusal,
    _payload_ok,
    chunk_type_text,
    format_shell_result,
    normalize_key,
    pick_best_window,
    prepare_app,
)
from yutori_mcp.computer_use.constants import resolve_harness
from yutori_mcp.computer_use.lock import ComputerUseBusyError, DesktopLock
from yutori_mcp.computer_use.runner import (
    ActionReporter,
    Emitter,
    RequestError,
    RunGuard,
    classify_result,
    parse_request,
)
from yutori_mcp.computer_use.result import format_result
from yutori_mcp.computer_use.runtime import RuntimeValidationError, load_runtime
from yutori_mcp.computer_use.supervisor import (
    _stop_process_group,
    _supervise,
    python_runner_command,
    run_task,
)
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


def test_installer_checksum_aborts_before_execution(monkeypatch):
    from yutori_mcp.computer_use import cli

    monkeypatch.setattr(cli, "harness_blocker", lambda harness=None: None)
    monkeypatch.setattr(cli, "_download_installer", lambda _: b"installer")
    with patch("yutori_mcp.computer_use.cli.subprocess.run") as run:
        assert cli._setup() == 1
    run.assert_not_called()


def test_harness_resolution_orders_request_env_default(monkeypatch):
    monkeypatch.delenv("YUTORI_COMPUTER_USE_HARNESS", raising=False)
    assert resolve_harness() == "node"
    monkeypatch.setenv("YUTORI_COMPUTER_USE_HARNESS", "python")
    assert resolve_harness() == "python"
    assert resolve_harness("node") == "node"
    with pytest.raises(ValueError, match="Unknown computer-use harness"):
        resolve_harness("typescript")


def test_schema_accepts_only_known_harnesses():
    assert ComputerUseTaskInput(task="x").harness is None
    assert ComputerUseTaskInput(task="x", harness="python").harness == "python"
    with pytest.raises(ValidationError):
        ComputerUseTaskInput(task="x", harness="typescript")


def test_runtime_protocol_mismatch_has_one_remediation():
    with (
        patch(
            "yutori_mcp.computer_use.runtime.import_module",
            return_value=SimpleNamespace(PROTOCOL_VERSION=2, verify_runner=lambda: True),
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
            return_value=SimpleNamespace(PROTOCOL_VERSION=1, verify_runner=lambda: False),
        ),
        pytest.raises(RuntimeValidationError) as error,
    ):
        load_runtime()
    assert "integrity" in str(error.value)
    assert str(error.value).count(error.value.remediation) == 1


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
            command=["/python", "-m", "yutori_mcp.computer_use.runner"],
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
                command=["/python", "-m", "yutori_mcp.computer_use.runner"],
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


async def test_child_environment_has_path_and_disables_harness_telemetry():
    """Shell commands the model runs resolve tools from PATH, and the harness
    fires an import-time telemetry event unless the env var is off — the
    constructor flag alone is too late for that one.
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
            command=["/python", "-m", "yutori_mcp.computer_use.runner"],
            request={"type": "run"},
            api_key="yt-key",
            deadline=time.monotonic() + 1,
        )
    env = create.await_args.kwargs["env"]
    assert "/usr/bin" in env["PATH"].split(":")
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert env["CUA_TELEMETRY_ENABLED"] == "false"


def test_python_runner_command_uses_this_interpreter_isolated():
    # -I keeps the child's sys.path free of cwd/PYTHONPATH/user-site: a run
    # launched from inside the Yutori monorepo had its `yutori/` source tree
    # shadow the installed SDK and crash the runner on import.
    assert python_runner_command()[0] == sys.executable
    assert python_runner_command()[1:] == ["-I", "-m", "yutori_mcp.computer_use.runner"]


def _run_task_kwargs(tmp_path, **overrides):
    kwargs = {
        "task": "open calculator",
        "app": None,
        "start_url": None,
        "minutes": 1,
        "max_steps": 10,
        "api_key": "yt-key",
        "api_base_url": "https://api.dev.yutori.com/v1",
        "lock": DesktopLock(tmp_path / "desktop.lock"),
    }
    kwargs.update(overrides)
    return kwargs


async def test_run_task_python_harness_carries_driver_path_and_model(tmp_path):
    driver = tmp_path / "cua-driver"
    driver.write_text("")
    supervise = AsyncMock(return_value={"outcome": "completed"})
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_cua_driver", return_value=driver),
    ):
        result = await run_task(
            **_run_task_kwargs(tmp_path, harness="python")
        )
    assert result == {"outcome": "completed"}
    request = supervise.await_args.kwargs["request"]
    assert request["driver_path"] == str(driver)
    assert request["model"] == "n2-preview"
    assert request["protocol_version"] == 1
    assert supervise.await_args.kwargs["command"][0] == sys.executable


async def test_run_task_defaults_to_the_node_harness(tmp_path, monkeypatch):
    monkeypatch.delenv("YUTORI_COMPUTER_USE_HARNESS", raising=False)
    supervise = AsyncMock(return_value={"outcome": "completed"})

    class _RunnerPath:
        def __enter__(self):
            return "/wheel/runner.mjs"

        def __exit__(self, *args):
            return False

    runtime = SimpleNamespace(runner_path=lambda: _RunnerPath())
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_node", return_value=pathlib.Path("/opt/node")),
        patch.object(supervisor, "load_runtime", return_value=runtime),
    ):
        result = await run_task(**_run_task_kwargs(tmp_path))
    assert result == {"outcome": "completed"}
    assert supervise.await_args.kwargs["command"] == ["/opt/node", "/wheel/runner.mjs"]
    # The Node runner resolves cua-driver from PATH; the field is python-only.
    assert "driver_path" not in supervise.await_args.kwargs["request"]


async def test_run_task_env_var_selects_the_python_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("YUTORI_COMPUTER_USE_HARNESS", "python")
    driver = tmp_path / "cua-driver"
    driver.write_text("")
    supervise = AsyncMock(return_value={"outcome": "completed"})
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_cua_driver", return_value=driver),
    ):
        await run_task(**_run_task_kwargs(tmp_path))
    assert supervise.await_args.kwargs["command"][1:] == [
        "-I",
        "-m",
        "yutori_mcp.computer_use.runner",
    ]


async def test_run_task_rejects_an_unknown_harness(tmp_path):
    supervise = AsyncMock()
    with patch.object(supervisor, "_supervise", supervise):
        result = await run_task(**_run_task_kwargs(tmp_path, harness="typescript"))
    assert result["outcome"] == "failed"
    assert "Unknown computer-use harness" in result["final_text"]
    supervise.assert_not_awaited()


async def test_run_task_python_reports_a_missing_driver_without_spawning(tmp_path):
    supervise = AsyncMock()
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_cua_driver", return_value=None),
    ):
        result = await run_task(**_run_task_kwargs(tmp_path, harness="python"))
    assert result["outcome"] == "failed"
    assert "cua-driver" in result["final_text"]
    supervise.assert_not_awaited()


async def test_run_task_node_reports_missing_node_without_spawning(tmp_path):
    supervise = AsyncMock()
    with (
        patch.object(supervisor, "_supervise", supervise),
        patch.object(supervisor, "find_node", return_value=None),
    ):
        result = await run_task(**_run_task_kwargs(tmp_path, harness="node"))
    assert result["outcome"] == "failed"
    assert "Node 22" in result["final_text"]
    supervise.assert_not_awaited()


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
    for harness in ("node", "python"):
        names = [check.__name__ for check in preflight.checks_for(harness)]
        assert names.index("check_driver_binary") < names.index("check_driver_contract")


def test_checks_gate_each_harness_on_its_own_toolchain():
    node_names = [check.__name__ for check in preflight.checks_for("node")]
    python_names = [check.__name__ for check in preflight.checks_for("python")]
    assert "check_node" in node_names and "check_runtime" in node_names
    assert "check_harness" not in node_names
    assert "check_harness" in python_names
    assert "check_node" not in python_names and "check_runtime" not in python_names


def test_harness_blocker_reports_only_toolchain_failures():
    with patch.object(preflight, "find_node", return_value=None):
        blocker = preflight.harness_blocker("node")
    assert blocker is not None and blocker.name == "Node 22"
    with (
        patch.object(preflight.sys, "version_info", (3, 12, 0)),
        patch.object(preflight.importlib.util, "find_spec", return_value=object()),
    ):
        assert preflight.harness_blocker("python") is None


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


def test_harness_check_blocks_an_unsupported_interpreter():
    with patch.object(preflight.sys, "version_info", (3, 10, 4)):
        result = preflight.check_harness()
    assert not result.ok
    assert "3.10" in result.detail
    assert "uvx --python" in result.remediation


def test_harness_check_blocks_a_missing_cua_agent():
    with (
        patch.object(preflight.sys, "version_info", (3, 12, 0)),
        patch.object(preflight.importlib.util, "find_spec", return_value=None),
    ):
        result = preflight.check_harness()
    assert not result.ok
    assert "cua-agent" in result.detail


def test_harness_check_passes_on_a_supported_setup():
    with (
        patch.object(preflight.sys, "version_info", (3, 12, 0)),
        patch.object(
            preflight.importlib.util, "find_spec", return_value=object()
        ),
    ):
        assert preflight.check_harness().ok


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
        result = preflight.check_driver_contract()
    assert result.ok
    assert "0.18.0" in result.detail and preflight.DRIVER_VERSION in result.detail


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
            command=["/python", "-m", "yutori_mcp.computer_use.runner"],
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
            command=["/python", "-m", "yutori_mcp.computer_use.runner"],
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
            command=["/python", "-m", "yutori_mcp.computer_use.runner"],
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
            "refusal_code": "driver_refused",
            "elapsed_ms": 120,
        }
    )
    action_message = ctx.info.await_args.args[0]
    assert "action #7" in action_message
    assert "computer_batch -> refused" in action_message
    assert "driver_refused" in action_message
    assert "[120 ms]" in action_message
    assert ctx.report_progress.await_args.kwargs["progress"] == 7


async def test_handle_computer_use_pops_ctx_and_wires_on_event():
    import yutori_mcp.server as server

    run_task = AsyncMock(return_value={"outcome": "completed"})
    ctx = SimpleNamespace(report_progress=AsyncMock(), info=AsyncMock())
    with (
        patch("yutori_mcp.computer_use.preflight.first_blocker", return_value=None),
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
        patch("yutori_mcp.computer_use.supervisor.run_task", run_task),
        patch(
            "yutori_mcp.credentials.resolve_api_key_for_environment",
            return_value="yt-key",
        ),
        patch("yutori_mcp.server.resolve_base_url", return_value="https://api"),
    ):
        await server._handle_computer_use(None, {"task": "open calculator"})
    assert run_task.await_args.kwargs["on_event"] is None


# ---------------------------------------------------------------------------
# Runner: request parsing, event mapping, and run bounds.
# ---------------------------------------------------------------------------


def _valid_request(**overrides):
    request = {
        "protocol_version": 1,
        "type": "run",
        "task": "open calculator",
        "app": None,
        "start_url": None,
        "deadline_ms": 1_000_000,
        "max_steps": 10,
        "model": "n2-preview",
        "api_base_url": "https://api.dev.yutori.com/v1",
        "driver_path": "/x/cua-driver",
    }
    request.update(overrides)
    return request


def test_parse_request_accepts_the_supervisor_shape():
    parsed = parse_request(_valid_request())
    assert parsed["task"] == "open calculator"
    assert parsed["driver_path"] == "/x/cua-driver"


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"protocol_version": 2}, "UNSUPPORTED_PROTOCOL_VERSION"),
        ({"type": "walk"}, "INVALID_REQUEST"),
        ({"task": ""}, "INVALID_REQUEST"),
        ({"start_url": "https://x", "app": None}, "INVALID_REQUEST"),
        ({"deadline_ms": 0}, "INVALID_REQUEST"),
        ({"deadline_ms": True}, "INVALID_REQUEST"),
        ({"max_steps": -1}, "INVALID_REQUEST"),
        ({"driver_path": ""}, "INVALID_REQUEST"),
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
        ("Batch completed; completed=3.", "confirmed", "executed"),
        ("[ERROR] Refused an action.", "refused", "refused"),
        ("[ERROR] Action was not confirmed by the user.", "refused", "refused"),
        ("[ERROR] shell_command failed: command was killed after exceeding its "
         "10-second timeout", "timeout_after_possible_dispatch", "uncertain"),
        ("[ERROR] Invalid left_click call: bad coordinates", "unverifiable", "uncertain"),
        ({"type": "input_image", "image_url": "data:...",
          "result": {"status": "stopped", "error": "boom"}}, "unverifiable", "uncertain"),
        ({"type": "input_image", "image_url": "data:...",
          "result": {"status": "completed", "completed": 3}}, "confirmed", "executed"),
        ({"type": "input_image", "image_url": "data:...",
          "result": "[ERROR] bash failed: nope"}, "unverifiable", "uncertain"),
    ],
)
def test_classify_result_maps_outputs_to_statuses(output, raw_status, status):
    outputs = [{"type": "function_call_output", "call_id": "c1", "output": output}]
    assert classify_result(outputs) == raw_status
    from yutori_mcp.computer_use.runner import _status_for

    assert _status_for(raw_status) == status


class _CollectStream:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, data):
        self.lines.append(data)

    def flush(self):
        pass


async def test_action_reporter_events_satisfy_the_result_formatter():
    stream = _CollectStream()
    reporter = ActionReporter(Emitter(stream), time.monotonic())
    await reporter.on_computer_call_end(
        {"type": "function_call", "name": "Computer_Batch"},
        [{"type": "function_call_output", "call_id": "c1", "output": "ok"}],
    )
    await reporter.on_computer_call_end(
        {"type": "function_call", "name": "left_click"},
        [{"type": "function_call_output", "call_id": "c2",
          "output": "[ERROR] Refused a click."}],
    )
    events = [json.loads(line) for line in stream.lines]
    assert [event["index"] for event in events] == [0, 1]
    assert events[0]["tool"] == "computer_batch"
    assert events[1]["status"] == "refused"
    assert events[1]["refusal_code"] == "driver_refused"
    # format_result formats each action with **action, so every key must exist.
    formatted = format_result(
        {"outcome": "completed", "final_text": "ok", "actions": events}
    )
    assert "#0 computer_batch: executed" in formatted


async def test_run_guard_stops_at_the_step_cap_but_never_on_the_first_iteration():
    # Stopping iteration zero trips an UnboundLocalError inside the harness's
    # on_run_end, so the guard always allows one step.
    guard = RunGuard(1, time.monotonic() - 5)
    assert await guard.on_run_continue({}, [], []) is True
    assert await guard.on_run_continue({}, [], []) is False
    assert guard.limit_reached or guard.deadline_reached


async def test_run_guard_deadline_reports_separately_from_the_step_cap():
    guard = RunGuard(50, time.monotonic() - 1)
    assert await guard.on_run_continue({}, [], []) is True
    assert await guard.on_run_continue({}, [], []) is False
    assert guard.deadline_reached and not guard.limit_reached


async def test_run_guard_recovers_a_crashed_app_then_gives_up():
    relaunches: list[str] = []

    async def fake_prepare(cli, app, start_url):
        relaunches.append(app)
        if len(relaunches) >= 2:
            raise DriverError("still down")
        return {"name": app, "pid": os.getpid()}

    guard = RunGuard(
        50,
        time.monotonic() + 60,
        cli=object(),
        app="Calculator",
        start_url=None,
        target={"name": "Calculator", "pid": 2_147_000_000},
    )
    with patch.object(runner_module, "prepare_app", side_effect=fake_prepare):
        assert await guard.on_run_continue({}, [], []) is True  # first step is free
        # Recovery succeeds once (pid becomes this test process), so the run continues.
        assert await guard.on_run_continue({}, [], []) is True
        assert guard.recovery_attempts == 1
        # Kill the target again: the remaining attempt fails and the run stops.
        guard._target = {"name": "Calculator", "pid": 2_147_000_000}
        assert await guard.on_run_continue({}, [], []) is False
    assert guard.target_crashed
    assert relaunches == ["Calculator", "Calculator"]


# ---------------------------------------------------------------------------
# Driver client and desktop handler.
# ---------------------------------------------------------------------------


def test_payload_validation_raises_refusals_with_the_word_refused():
    with pytest.raises(DriverRefusal, match="refused"):
        _payload_ok("click", {"status": "refused"})
    with pytest.raises(DriverRefusal, match="refused"):
        _payload_ok("click", {"refusal": {"code": "protected_resource"}})


def test_payload_validation_applies_the_bare_code_rule():
    # Driver >=0.16 stamps informational codes on successful payloads.
    assert _payload_ok("click", {"code": "ok", "activated": True})["code"] == "ok"
    assert _payload_ok(
        "click", {"code": "ok", "request_accepted": True, "status": "done"}
    )
    with pytest.raises(DriverError):
        _payload_ok("click", {"code": "something_failed"})
    with pytest.raises(DriverError):
        _payload_ok("click", {"code": "x", "request_accepted": True, "status": "partial"})


def test_chunk_type_text_prefers_word_boundaries_and_reassembles():
    text = ("word " * 300).strip()
    chunks = chunk_type_text(text)
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks) == text
    assert chunk_type_text("a" * 1200) == ["a" * 500, "a" * 500, "a" * 200]


def test_normalize_key_maps_aliases_and_punctuation():
    assert normalize_key("Enter") == "return"
    assert normalize_key("esc") == "escape"
    assert normalize_key("meta") == "cmd"
    assert normalize_key("period") == "."
    assert normalize_key("q") == "q"


def test_pick_best_window_excludes_helper_strips():
    strips = [
        {"window_id": index, "bounds": {"width": 600, "height": 20}, "z_index": 9}
        for index in range(4)
    ]
    main_window = {
        "window_id": 99,
        "bounds": {"width": 400, "height": 500},
        "z_index": 1,
    }
    assert pick_best_window(strips + [main_window])["window_id"] == 99
    # All-helper fallback: the largest window by area.
    assert pick_best_window(strips)["window_id"] == 0


class _FakeCLI:
    def __init__(self, responses=None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    async def call(self, tool, args):
        self.calls.append((tool, args))
        response = self.responses.get(tool)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(args)
        return response or {}

    async def capture(self, tool, args):
        self.calls.append((tool, args))
        return {"screenshot_width": 3840, "screenshot_height": 2160}, b"png-bytes"


async def test_prepare_app_uses_the_bundle_id_heuristic_with_name_retry():
    attempts: list[dict] = []

    def launch(args):
        attempts.append(args)
        if "bundle_id" in args:
            raise DriverError("unknown bundle")
        return {"pid": 42, "name": "Calculator", "windows": []}

    cli = _FakeCLI({"launch_app": launch, "bring_to_front": {}})
    with patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()):
        target = await prepare_app(cli, "com.apple.calculator", "https://example.com")
    assert target == {"name": "Calculator", "pid": 42}
    assert attempts[0] == {
        "bundle_id": "com.apple.calculator",
        "urls": ["https://example.com"],
    }
    assert attempts[1] == {
        "name": "com.apple.calculator",
        "urls": ["https://example.com"],
    }
    # launch_app and bring_to_front never carry a session: a session id on any
    # action would pin capture_scope before the desktop session starts.
    assert all("session" not in args for _tool, args in cli.calls)


async def test_prepare_app_fronting_failures_are_not_fatal():
    cli = _FakeCLI(
        {
            "launch_app": {"pid": 7, "name": "TextEdit", "windows": [
                {"window_id": 3, "bounds": {"width": 800, "height": 600}}
            ]},
            "bring_to_front": DriverError("bring_to_front_exact_window_unverified"),
        }
    )
    with patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()):
        target = await prepare_app(cli, "TextEdit", None)
    assert target["pid"] == 7


async def test_desktop_screenshot_returns_base64_and_caches_native_size():
    cli = _FakeCLI()
    desktop = CuaDriverDesktop(cli, session="s1")
    image = await desktop.screenshot()
    assert image == "cG5nLWJ5dGVz"  # base64 of b"png-bytes"
    assert await desktop.get_dimensions() == (3840, 2160)


async def test_desktop_scroll_recovers_direction_and_amount():
    cli = _FakeCLI()
    desktop = CuaDriverDesktop(cli, session="s1")
    desktop._native_size = (3840, 2160)
    with patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()):
        # The loop converts the model's amount=5 into pixels: 5 * 2160 * 0.1.
        await desktop.scroll(100, 200, 0, 1080)
    tool, args = cli.calls[-1]
    assert tool == "scroll"
    assert args["direction"] == "down"
    assert args["amount"] == 15  # model amount 5, tripled like the previous runner
    assert args["by"] == "line"
    assert args["scope"] == "desktop" and args["session"] == "s1"


async def test_desktop_keypress_normalizes_and_routes_chords():
    cli = _FakeCLI()
    desktop = CuaDriverDesktop(cli, session="s1")
    with patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()):
        await desktop.keypress("Enter")
        await desktop.keypress(["cmd", "shift", "s"])
    assert cli.calls[0][0] == "press_key" and cli.calls[0][1]["key"] == "return"
    assert cli.calls[1][0] == "hotkey" and cli.calls[1][1]["keys"] == [
        "cmd",
        "shift",
        "s",
    ]


async def test_desktop_type_chunks_long_text_with_zero_delay():
    cli = _FakeCLI()
    desktop = CuaDriverDesktop(cli, session="s1")
    with patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()):
        await desktop.type("a" * 1200)
    type_calls = [args for tool, args in cli.calls if tool == "type_text"]
    assert [len(args["text"]) for args in type_calls] == [500, 500, 200]
    assert all(args["delay_ms"] == 0 for args in type_calls)


async def test_run_shell_command_kills_the_group_on_timeout():
    desktop = CuaDriverDesktop(_FakeCLI(), session="s1")
    with pytest.raises(TimeoutError, match="1-second timeout"):
        await desktop.run_shell_command("sleep 30", timeout_seconds=1)


async def test_run_shell_command_formats_exit_codes():
    desktop = CuaDriverDesktop(_FakeCLI(), session="s1")
    result = await desktop.run_shell_command("echo out; exit 3")
    assert result == "out\n[exit code 3]"
    assert await desktop.run_shell_command("true") == (
        "Command exited with code 0 and produced no output."
    )


async def test_run_bash_command_persists_the_working_directory(tmp_path):
    desktop = CuaDriverDesktop(_FakeCLI(), session="s1")
    await desktop.run_bash_command(f"cd {tmp_path}")
    result = await desktop.run_bash_command("pwd")
    assert tmp_path.name in result


def test_format_shell_result_keeps_the_exit_marker_within_the_cap():
    result = format_shell_result("x" * 9000, 2)
    assert len(result) <= 8000
    assert result.endswith("[exit code 2]")
    assert "[result truncated]" in result


class _RunnerCLITransportProcess:
    def __init__(self, stdout=b"{}", returncode=0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""


async def test_driver_cli_sends_compact_json_argv(tmp_path):
    create = AsyncMock(
        return_value=_RunnerCLITransportProcess(stdout=b'{"activated": true}')
    )
    cli = DriverCLI("/x/cua-driver", capture_dir=tmp_path)
    with patch("asyncio.create_subprocess_exec", create):
        payload = await cli.call("click", {"x": 1, "y": 2})
    assert payload == {"activated": True}
    argv = create.await_args.args
    assert argv[0] == "/x/cua-driver"
    assert argv[1] == "call" and argv[2] == "click"
    assert json.loads(argv[3]) == {"x": 1, "y": 2}
    assert argv[4] == "--raw"


async def test_driver_cli_capture_retries_until_a_frame_arrives(tmp_path):
    attempts = 0

    async def call(tool, args):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return {}  # mid-repaint: no pixel frame
        Path(args["screenshot_out_file"]).write_bytes(b"png")
        return {"screenshot_width": 100, "screenshot_height": 50}

    cli = DriverCLI("/x/cua-driver", capture_dir=tmp_path)
    with (
        patch.object(cli, "call", side_effect=call),
        patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()),
    ):
        payload, image = await cli.capture("get_desktop_state", {"session": "s"})
    assert attempts == 3
    assert payload["screenshot_width"] == 100 and image == b"png"


async def test_driver_cli_capture_reports_a_permanently_missing_frame(tmp_path):
    cli = DriverCLI("/x/cua-driver", capture_dir=tmp_path)
    with (
        patch.object(cli, "call", AsyncMock(return_value={})),
        patch("yutori_mcp.computer_use.driver.asyncio.sleep", AsyncMock()),
    ):
        with pytest.raises(DriverError, match="no usable pixel frame"):
            await cli.capture("get_desktop_state", {"session": "s"})


async def test_cua_agent_accepts_the_desktop_handler():
    """The harness's handler protocol is runtime-checkable on member PRESENCE.

    A handler missing any member (left_mouse_down/up included) silently falls
    through to "Unknown tool type", computer_handler stays None, and the first
    step dies with "No current screenshot". This pins the whole surface.
    """
    cua_agent = pytest.importorskip("cua_agent")

    desktop = CuaDriverDesktop(_FakeCLI(), session="s1")
    agent = cua_agent.ComputerAgent(
        model="yutori/n2-preview",
        tools=[desktop],
        api_key="yt-test",
        telemetry_enabled=False,
        tool_set="computer_use_tools-20260729",
    )
    await agent._initialize_computers()
    assert agent.computer_handler is not None
    # The optional shell capabilities are duck-typed by method presence.
    assert hasattr(desktop, "run_shell_command")
    assert hasattr(desktop, "run_bash_command")


async def test_failed_run_reports_completed_steps_and_redacts_the_key(monkeypatch):
    """A run that crashes on step N must report N steps, not zero.

    The failed result is emitted inside run_request, the only scope that can
    still see the guard's counter and the run clock.
    """

    class _FakeAgent:
        def __init__(self, **kwargs):
            self.callbacks = kwargs.get("callbacks") or []

        async def run(self, _messages, stream=False):
            del stream
            for _ in range(2):
                for callback in self.callbacks:
                    if hasattr(callback, "on_run_continue"):
                        assert await callback.on_run_continue({}, [], [])
                yield {"output": []}
            raise RuntimeError("boom mid-run holding yt-secret")

    monkeypatch.setitem(
        sys.modules, "cua_agent", SimpleNamespace(ComputerAgent=_FakeAgent)
    )
    monkeypatch.setattr(runner_module, "DriverCLI", lambda path: _FakeCLI())
    stream = _CollectStream()
    request = parse_request(
        _valid_request(deadline_ms=int((time.time() + 60) * 1000), max_steps=30)
    )
    outcome = await runner_module.run_request(
        request, Emitter(stream), api_key="yt-secret"
    )
    assert outcome == "failed"
    result = json.loads(stream.lines[-1])
    assert result["outcome"] == "failed"
    assert result["steps"] == 2
    assert result["elapsed_ms"] >= 0
    assert "yt-secret" not in json.dumps(result)
    assert "[REDACTED]" in result["final_text"]


@pytest.mark.parametrize("command", ["setup", "doctor", "smoke"])
def test_cli_reports_a_bad_harness_env_without_a_traceback(
    command, monkeypatch, capsys
):
    from yutori_mcp.computer_use import cli

    monkeypatch.setenv("YUTORI_COMPUTER_USE_HARNESS", "typescript")
    assert cli.dispatch(command) == 1
    assert "Unknown computer-use harness" in capsys.readouterr().out


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[DONE] All set.", "All set."),
        ("All set.\n\n[DONE]", "All set."),
        ("[INFEASIBLE] The app is gone.", "The app is gone."),
        ("[DONE]", None),
        (None, None),
    ],
)
def test_final_markers_are_stripped_from_either_end(text, expected):
    # A live run produced a trailing "[DONE]"; the wire text must carry neither.
    assert runner_module._strip_final_markers(text) == expected


def _run_namespace(**overrides):
    import argparse

    values = {
        "task": "open calculator",
        "harness": None,
        "app": None,
        "start_url": None,
        "minutes": 3,
        "max_steps": 60,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_cli_run_forwards_the_task_and_prints_the_result(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    run_task = AsyncMock(
        return_value={"outcome": "completed", "final_text": "done", "actions": []}
    )
    monkeypatch.setattr(cli, "first_blocker", lambda harness=None: None)
    monkeypatch.setattr(cli, "run_task", run_task)
    monkeypatch.setattr(
        cli, "resolve_api_key_for_environment", lambda environment: "yt-key"
    )
    code = cli.dispatch("run", _run_namespace(harness="python", app="Calculator"))
    assert code == 0
    kwargs = run_task.await_args.kwargs
    assert kwargs["harness"] == "python"
    assert kwargs["app"] == "Calculator"
    assert kwargs["on_event"] is not None
    assert "Outcome: completed" in capsys.readouterr().out


def test_cli_run_rejects_out_of_bounds_arguments_as_a_message(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    run_task = AsyncMock()
    monkeypatch.setattr(cli, "run_task", run_task)
    assert cli.dispatch("run", _run_namespace(minutes=99)) == 1
    run_task.assert_not_awaited()
    assert "minutes" in capsys.readouterr().out


def test_cli_run_gates_on_the_selected_harness_blocker(monkeypatch, capsys):
    from yutori_mcp.computer_use import cli

    run_task = AsyncMock()
    monkeypatch.setattr(cli, "run_task", run_task)
    monkeypatch.setattr(
        cli,
        "first_blocker",
        lambda harness=None: SimpleNamespace(
            detail="Node 22 not found", remediation="brew install node@22"
        ),
    )
    assert cli.dispatch("run", _run_namespace(harness="node")) == 1
    run_task.assert_not_awaited()
    output = capsys.readouterr().out
    assert "Node 22 not found" in output and "brew install node@22" in output


async def test_api_timer_accumulates_model_time():
    clock = iter([10.0, 12.5, 20.0, 21.0])
    timings = runner_module.RunTimings()
    timer = runner_module.ApiTimer(timings, clock=lambda: next(clock))
    await timer.on_api_start({})
    await timer.on_api_end({}, None)
    await timer.on_api_start({})
    await timer.on_api_end({}, None)
    assert timings.model_ms == 3500
    assert timings.model_calls == 2


async def test_action_reporter_subtracts_capture_and_settle_from_action_time():
    """The call span includes the post-action capture and settle; the perf
    breakdown's action_ms must not, or phases double count against total."""
    clock = iter([100.0, 104.0])  # call start, call end
    timings = runner_module.RunTimings()
    desktop_timings = SimpleNamespace(capture_ms=1000, settle_ms=300, captures=2)
    stream = _CollectStream()
    reporter = runner_module.ActionReporter(
        Emitter(stream),
        run_start=0.0,
        timings=timings,
        desktop_timings=desktop_timings,
        clock=lambda: next(clock),
    )
    await reporter.on_computer_call_start({"name": "left_click"})
    desktop_timings.capture_ms += 700
    desktop_timings.settle_ms += 300
    await reporter.on_computer_call_end(
        {"name": "left_click"}, [{"output": {"type": "input_image", "image_url": "d"}}]
    )
    event = json.loads(stream.lines[-1])
    assert event["duration_ms"] == 4000
    assert timings.action_ms == 3000  # 4000 span - 700 capture - 300 settle
    assert timings.tool_calls == 1


def test_timings_payload_accounts_every_phase():
    timings = runner_module.RunTimings()
    timings.model_ms, timings.model_calls = 6000, 3
    timings.action_ms, timings.tool_calls = 2000, 4
    desktop_timings = SimpleNamespace(capture_ms=1500, captures=5, settle_ms=900)
    payload = runner_module._timings_payload(12000, 3, timings, desktop_timings)
    assert payload["other_ms"] == 12000 - 6000 - 2000 - 1500 - 900
    assert payload["screenshots"] == 5
    payload = runner_module._timings_payload(1000, 0, timings, None)
    assert payload["screenshot_ms"] == 0 and payload["other_ms"] == 0


def test_format_perf_renders_phases_and_basic_step_rate():
    from yutori_mcp.computer_use.result import format_perf

    detailed = format_perf(
        {
            "elapsed_ms": 103000,
            "steps": 12,
            "timings": {
                "model_ms": 61000,
                "model_calls": 12,
                "action_ms": 18000,
                "tool_calls": 12,
                "screenshot_ms": 9800,
                "screenshots": 14,
                "settle_ms": 3600,
                "other_ms": 10600,
            },
        }
    )
    assert detailed[0] == "Perf: total 103.0s over 12 steps (8.6s/step)"
    assert "model 61.0s over 12 calls (5.1s avg)" in detailed[1]
    assert "screenshots 9.8s over 14 captures (0.7s avg)" in detailed[1]
    assert "settle 3.6s" in detailed[1] and "other 10.6s" in detailed[1]

    # The Node runner carries no phase timings; the summary stops at step rate.
    basic = format_perf({"elapsed_ms": 70000, "steps": 6})
    assert basic == ["Perf: total 70.0s over 6 steps (11.7s/step)"]
    assert format_perf({"elapsed_ms": 70000}) == []


def test_format_result_appends_action_durations():
    text = format_result(
        {
            "outcome": "completed",
            "actions": [
                {
                    "index": 0,
                    "tool": "left_click",
                    "status": "executed",
                    "raw_status": "confirmed",
                    "delivery_mode": "foreground",
                    "route": "pixel",
                    "refusal_code": None,
                    "elapsed_ms": 5000,
                    "duration_ms": 4200,
                }
            ],
        }
    )
    assert "took 4200 ms" in text
