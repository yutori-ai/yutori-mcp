from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import queue
import re
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, url2pathname, urlopen

from .constants import (
    DRIVER_VERSION,
    MCP_VERSION,
    MODEL,
    SDK_ARTIFACT_SHA256,
    SDK_INSTALLATION_SHA256,
    SDK_PROVENANCE_SHA256,
    SDK_VERSION,
    TOOL_SET,
)
from .result import compact_json_line, structured_content

DRIVER_APP = Path("/Applications/CuaDriver.app")
DRIVER_PATHS = (
    # ~/.local/bin first: it is where the installer actually puts the CLI, and omitting it made
    # find_cua_driver() return None on a mini that had a working driver at
    # /Users/<user>/.local/bin/cua-driver — preflight would have blocked a healthy machine.
    Path.home() / ".local" / "bin" / "cua-driver",
    Path("/opt/homebrew/bin/cua-driver"),
    Path("/usr/local/bin/cua-driver"),
    Path.home() / ".cargo" / "bin" / "cua-driver",
)
# An MCP client launched from the Dock inherits a minimal PATH that omits Homebrew, so every
# tool we shell out to is resolved from an explicit list instead of the ambient PATH. The
# runner subprocess needs this as its PATH too: shell commands the model runs resolve their
# tools from it.
TOOL_SEARCH_DIRECTORIES = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)
_EDITABLE_SDK_OVERRIDE = "YUTORI_MCP_ALLOW_EDITABLE_SDK"
_INSTALLER_GENERATED_FILES = {"INSTALLER", "RECORD", "REQUESTED", "direct_url.json"}
_SDK_PROVENANCE_PATH = Path("yutori/navigator/macos/assets/provenance.json")
# Shared remediation text for checks whose fix is "(re)install the pinned CuaDriver setup":
# check_driver_app, check_driver_binary, check_driver_contract, check_overlay, and
# check_capture's driver-not-found branch all point here, so the wording can't drift
# across five call sites if it's ever revised.
_SETUP_REMEDIATION = "Run: yutori-mcp computer-use setup"
# Shared remediation text for checks whose fix is granting the driver's TCC permissions:
# check_permissions and check_capture's driver-capture-failed branch both point here.
_PERMISSIONS_GRANT_REMEDIATION = "Run: cua-driver permissions grant"
# An application that embeds cua-driver (the driver's EMBEDDING contract) hands this runtime its
# own binary and the private socket of the daemon it spawned. Both must be set together; the
# runtime then never looks for /Applications/CuaDriver.app and permissions are read from the host
# daemon, whose TCC identity is the host application's. Duplicated from the SDK's transport module
# (which reads the same names) because this module must stay importable without the SDK.
ENV_DRIVER_BINARY = "YUTORI_CUA_DRIVER_BINARY"
ENV_DRIVER_SOCKET = "YUTORI_CUA_DRIVER_SOCKET"
ENV_DRIVER_EMBEDDED = "CUA_DRIVER_EMBEDDED"
_EMBEDDED_HOST_REMEDIATION = "Start the embedded cua-driver daemon from the host application, then retry."
_EMBEDDED_PERMISSIONS_REMEDIATION = (
    "Grant Accessibility and Screen Recording to the host application in "
    "System Settings > Privacy & Security, then restart it."
)
_EMBEDDED_RPC_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class EmbeddedDriverHost:
    binary: Path
    socket: Path


def embedded_driver_host() -> EmbeddedDriverHost | None:
    """The host-owned driver named by the environment, or None for the standalone CuaDriver.app.

    Raises ValueError when only one of the two variables is set: half a configuration would
    otherwise fall back silently to the standalone install and a second permission identity.
    """
    binary = os.environ.get(ENV_DRIVER_BINARY)
    socket_path = os.environ.get(ENV_DRIVER_SOCKET)
    if not binary and not socket_path:
        return None
    if not (binary and socket_path):
        raise ValueError(f"{ENV_DRIVER_BINARY} and {ENV_DRIVER_SOCKET} must be set together.")
    return EmbeddedDriverHost(Path(binary), Path(socket_path))


def _configured_embedded_host() -> EmbeddedDriverHost | None:
    """A fully configured embedded host, or None; the half-set case is check_driver_app's to report."""
    try:
        return embedded_driver_host()
    except ValueError:
        return None


def _driver_socket_arguments() -> list[str]:
    host = _configured_embedded_host()
    return ["--socket", str(host.socket)] if host is not None else []


def _login_remediation(environment: str) -> str:
    from ..adapter import DEFAULT_ENVIRONMENT

    if environment == DEFAULT_ENVIRONMENT:
        return "Run: uvx yutori-mcp login"
    return f"Run: uvx yutori-mcp --env {environment} login"


def _api_access_remediation(environment: str) -> str:
    return f"{_login_remediation(environment)}. If already logged in, confirm this key has computer-use access."


def find_cua_driver() -> Path | None:
    host = _configured_embedded_host()
    if host is not None:
        return host.binary if host.binary.is_file() else None
    for path in DRIVER_PATHS:
        if path.is_file():
            return path
    return None


def child_search_path() -> str:
    """PATH for a subprocess, with any resolved cua-driver directory taking precedence."""
    directories = list(TOOL_SEARCH_DIRECTORIES)
    driver = find_cua_driver()
    if driver and str(driver.parent) not in directories:
        directories.insert(0, str(driver.parent))
    return ":".join(directories)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    remediation: str | None = None
    blocking: bool = True


def _result(
    name: str,
    ok: bool,
    detail: str,
    remediation: str,
    *,
    blocking: bool = True,
) -> CheckResult:
    return CheckResult(name, ok, detail, None if ok else remediation, blocking)


def check_macos() -> CheckResult:
    ok = platform.system() == "Darwin" and int(platform.mac_ver()[0].split(".")[0] or 0) >= 15
    return _result(
        "macOS",
        ok,
        platform.mac_ver()[0] or platform.system(),
        "Use a Mac running macOS 15 or later.",
    )


def check_architecture() -> CheckResult:
    machine = platform.machine()
    return _result(
        "architecture",
        machine in {"arm64", "x86_64"},
        machine,
        "Use an arm64 or x86_64 Mac.",
    )


def _editable_distribution(distribution: importlib.metadata.Distribution) -> bool:
    direct_url = distribution.read_text("direct_url.json")
    if not direct_url:
        return False
    try:
        metadata = json.loads(direct_url)
    except json.JSONDecodeError:
        return False
    if not isinstance(metadata, dict) or not isinstance(metadata.get("dir_info"), dict):
        return False
    return metadata["dir_info"].get("editable") is True


def _provenance_path(distribution: importlib.metadata.Distribution, *, editable: bool) -> Path:
    if not editable:
        return Path(distribution.locate_file(_SDK_PROVENANCE_PATH))
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    parsed = urlparse(direct_url.get("url", ""))
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("editable SDK source is not a local file URL")
    return Path(url2pathname(parsed.path)) / _SDK_PROVENANCE_PATH


def _stable_distribution_digest(distribution: importlib.metadata.Distribution) -> str:
    """Hash installed wheel-owned files using normalized RECORD-style entries."""
    records: list[str] = []
    for package_path in distribution.files or ():
        relative_path = str(package_path)
        path = Path(relative_path)
        if (
            ".." in path.parts
            or path.is_absolute()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
            or path.name in _INSTALLER_GENERATED_FILES
        ):
            continue
        installed_path = Path(distribution.locate_file(package_path))
        if not installed_path.is_file():
            raise FileNotFoundError(relative_path)
        digest = hashlib.sha256(installed_path.read_bytes()).digest()
        encoded_digest = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        records.append(f"{relative_path},sha256={encoded_digest}")
    payload = "".join(f"{record}\n" for record in sorted(records)).encode()
    return hashlib.sha256(payload).hexdigest()


def check_runtime() -> CheckResult:
    remediation = f"Reinstall the pinned runtime: uvx --refresh --from yutori-mcp=={MCP_VERSION} yutori-mcp"

    def report(ok: bool, detail: str) -> CheckResult:
        return _result("Python runtime", ok, detail, remediation)

    try:
        distribution = importlib.metadata.distribution("yutori")
        version = distribution.version
    except (ImportError, importlib.metadata.PackageNotFoundError, OSError, ValueError) as error:
        return report(False, str(error))

    editable = _editable_distribution(distribution)
    if editable:
        override = os.environ.get(_EDITABLE_SDK_OVERRIDE) == "1"
        detail = f"yutori {version}; editable installation; override {'enabled' if override else 'required'}"
        if not override or version != SDK_VERSION:
            return report(False, detail)
        try:
            provenance = _provenance_path(distribution, editable=True).read_bytes()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return report(False, str(error))
        ok = hashlib.sha256(provenance).hexdigest() == SDK_PROVENANCE_SHA256
        return report(ok, detail)

    try:
        installation_digest = _stable_distribution_digest(distribution)
    except OSError as error:
        return report(False, f"installed file unavailable: {error}")
    if version != SDK_VERSION or installation_digest != SDK_INSTALLATION_SHA256:
        detail = (
            f"yutori {version}; artifact sha256 {SDK_ARTIFACT_SHA256}; installation sha256 {installation_digest}"
        )
        return report(False, detail)
    try:
        provenance = _provenance_path(distribution, editable=False).read_bytes()
    except (OSError, ValueError) as error:
        return report(False, str(error))
    provenance_digest = hashlib.sha256(provenance).hexdigest()
    detail = (
        f"yutori {version}; artifact sha256 {SDK_ARTIFACT_SHA256}; installation sha256 {installation_digest}; "
        f"provenance sha256 {provenance_digest}"
    )
    return report(provenance_digest == SDK_PROVENANCE_SHA256, detail)


def _run_safely(
    command: list[str], *, timeout: float, text: bool = True
) -> subprocess.CompletedProcess[Any] | None:
    """Run ``command``, or None if the process could not even be launched/timed out.

    Every check below treats a missing binary, a spawn failure, or a timeout identically —
    "this probe is unavailable" — while still wanting the exit code and captured output when the
    process *did* run (including a nonzero exit, which is real signal, not a launch failure).
    """
    try:
        return subprocess.run(command, capture_output=True, text=text, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def check_compiler() -> CheckResult:
    result = _run_safely(["xcrun", "--sdk", "macosx", "--find", "swiftc"], timeout=10)
    if result is None:
        compiler, ok = "not found", False
    else:
        compiler = result.stdout.strip()
        ok = result.returncode == 0 and bool(compiler)
    return _result(
        "Swift compiler",
        ok,
        compiler or "not found",
        "Install Xcode Command Line Tools, then rerun computer-use setup.",
        blocking=False,
    )


def check_overlay() -> CheckResult:
    try:
        from yutori.navigator.macos import check_macos_overlay

        check = check_macos_overlay()
        detail = str(check.prepared.binary) if check.available and check.prepared else str(check.reason)
        ok = check.available
    except (ImportError, OSError, RuntimeError) as error:
        detail, ok = str(error), False
    return _result(
        "reasoning overlay",
        ok,
        detail,
        _SETUP_REMEDIATION,
        blocking=False,
    )


def check_driver_app() -> CheckResult:
    try:
        host = embedded_driver_host()
    except ValueError as error:
        return _result("driver app", False, str(error), _EMBEDDED_HOST_REMEDIATION)
    if host is not None:
        return _result(
            "driver app",
            host.binary.is_file(),
            f"embedded host binary {host.binary}",
            _EMBEDDED_HOST_REMEDIATION,
        )
    return _result(
        "driver app",
        DRIVER_APP.is_dir(),
        str(DRIVER_APP),
        _SETUP_REMEDIATION,
    )


def check_driver_binary() -> CheckResult:
    driver = find_cua_driver()
    return _result(
        "cua-driver binary",
        driver is not None,
        str(driver or "not found"),
        _SETUP_REMEDIATION,
    )


def _driver_json(command: str) -> dict[str, object]:
    driver = find_cua_driver()
    if driver is None:
        raise FileNotFoundError("cua-driver is not installed in a known location")
    output = subprocess.run(
        [str(driver), command, "--json"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    return json.loads(output)


def driver_version() -> str | None:
    """The installed driver's version, parsed from whatever shape this release reports.

    `status --json` is not JSON on every release — 0.18.0 prints plain text and json.loads blew
    up with "Expecting value: line 1 column 1", which surfaced as a blocked driver contract on a
    working machine. `--version` is the stable surface.
    """
    driver = find_cua_driver()
    if driver is None:
        return None
    result = _run_safely([str(driver), "--version"], timeout=10)
    if result is None:
        return None
    match = re.search(r"\d+\.\d+\.\d+", result.stdout)
    return match.group(0) if match else None


def check_driver_contract() -> CheckResult:
    """Require the driver release that implements the advertised action contract."""
    installed = driver_version()
    if installed is None:
        return _result(
            "driver contract",
            False,
            "driver did not report a version",
            _SETUP_REMEDIATION,
        )
    return _result(
        "driver contract",
        installed == DRIVER_VERSION,
        installed,
        f"Install CuaDriver {DRIVER_VERSION}: yutori-mcp computer-use setup",
    )


def _socket_accepts_connections(path: Path) -> bool:
    if not path.is_socket():
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        try:
            client.connect(str(path))
        except OSError:
            return False
    return True


def _read_rpc_result(stream: Any, request_id: int, timeout: float) -> dict[str, Any]:
    """The ``result`` of the JSON-RPC response carrying ``request_id`` from a line stream.

    A reader thread feeds lines into a queue so a proxy that never answers cannot hang the
    preflight past ``timeout``; the daemon's log lines and unrelated responses are skipped.
    """
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in stream:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"cua-driver proxy did not answer request {request_id} within {timeout:g}s")
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError("cua-driver proxy closed its output before answering")
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if message.get("error"):
            raise RuntimeError(f"cua-driver proxy error: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("cua-driver proxy returned a non-object result")
        return result


def _embedded_permissions(host: EmbeddedDriverHost) -> dict[str, Any]:
    """``check_permissions`` through the host daemon's MCP proxy.

    ``cua-driver permissions status`` only knows the standalone daemon identity and reports
    ``unknown`` for an embedded daemon, so the tool call is the one surface that reads the
    grants the run will actually have: the daemon answers from inside the host's TCC chain.
    """
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "yutori-mcp-preflight", "version": MCP_VERSION},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "check_permissions", "arguments": {"prompt": False}},
        },
    ]
    process = subprocess.Popen(
        [str(host.binary), "mcp", "--embedded", "--socket", str(host.socket)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, ENV_DRIVER_EMBEDDED: "1"},
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("".join(compact_json_line(message) + "\n" for message in messages))
        process.stdin.flush()
        result = _read_rpc_result(process.stdout, request_id=2, timeout=_EMBEDDED_RPC_TIMEOUT_SECONDS)
    finally:
        process.kill()
        process.wait(timeout=5)
    return structured_content(result)


def check_daemon_identity() -> CheckResult:
    host = _configured_embedded_host()
    if host is not None:
        return _result(
            "daemon identity",
            _socket_accepts_connections(host.socket),
            f"embedded daemon at {host.socket}",
            _EMBEDDED_HOST_REMEDIATION,
        )
    result = _run_safely(["pgrep", "-f", "/Applications/CuaDriver.app/Contents/MacOS/"], timeout=10)
    return _result(
        "daemon identity",
        result is not None and result.returncode == 0,
        "app-bundle daemon",
        "Start it with: open -n -g -a CuaDriver --args serve",
    )


def check_permissions() -> CheckResult:
    host = _configured_embedded_host()
    if host is not None:
        try:
            info = _embedded_permissions(host)
            ok = bool(info.get("accessibility")) and bool(info.get("screen_recording"))
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
            ok = False
        return _result(
            "permissions",
            ok,
            "Accessibility and Screen Recording (host application)",
            _EMBEDDED_PERMISSIONS_REMEDIATION,
        )
    try:
        info = _driver_json("permissions")
        ok = bool(info.get("accessibility")) and bool(info.get("screen_recording"))
    except (OSError, subprocess.SubprocessError, ValueError):
        ok = False
    return _result(
        "permissions",
        ok,
        "Accessibility and Screen Recording",
        _PERMISSIONS_GRANT_REMEDIATION,
    )


def _console_lock_state() -> bool | None:
    """Read the machine's lock state without depending on this process's GUI session."""
    result = _run_safely(["/usr/sbin/ioreg", "-n", "Root", "-d", "1"], timeout=5)
    if result is None:
        return None
    match = re.search(r'"IOConsoleLocked"\s*=\s*(Yes|No)', result.stdout)
    return match.group(1) == "Yes" if match else None


def check_gui_session() -> CheckResult:
    """Ask the machine who owns the console and whether that console is unlocked.

    The previous probe ran Quartz in-process and read CGSessionCopyCurrentDictionary. Over SSH
    that process has no window session, so it reported "inactive or locked" on a Mac that was
    logged in and driving apps fine — blocking every remote and headless setup. The console
    owner and IORegistry lock state are properties of the machine, so they remain truthful over
    SSH without asking the caller's process whether it has a GUI session.
    """
    result = _run_safely(["/usr/bin/stat", "-f", "%Su", "/dev/console"], timeout=5)
    owner = result.stdout.strip() if result is not None else ""
    # root or _windowserver owns the console at the login window, i.e. nobody is logged in.
    logged_in = bool(owner) and owner not in {"root", "_windowserver"}
    locked = _console_lock_state() if logged_in else None
    ok = logged_in and locked is False
    if not logged_in:
        detail = "no user logged in at the console"
        remediation = "Log in to the Mac and unlock the desktop."
    elif locked is True:
        detail = f"console user {owner}; screen locked"
        remediation = "Unlock the Mac desktop."
    elif locked is None:
        detail = f"console user {owner}; lock state unavailable"
        remediation = "Verify /usr/sbin/ioreg can report IOConsoleLocked, then retry."
    else:
        detail = f"console user {owner}"
        remediation = ""
    return _result(
        "GUI session",
        ok,
        detail,
        remediation,
    )


def check_capture() -> CheckResult:
    """Capture through the driver, which is the identity that actually holds the grant.

    Shelling out to /usr/sbin/screencapture tested whether THIS process could record the screen.
    Over SSH it never can, and it never needs to: TCC is granted to the CuaDriver app bundle, and
    that is the only thing that captures during a run.
    """
    driver = find_cua_driver()
    if driver is None:
        return _result(
            "desktop capture",
            False,
            "cua-driver not found",
            _SETUP_REMEDIATION,
            blocking=False,
        )
    embedded = _configured_embedded_host() is not None
    with tempfile.TemporaryDirectory(prefix="cua-capture-check-") as directory:
        # Under $TMPDIR, whose /var -> /private/var symlink the driver rejects as an unresolved
        # ancestor, so hand it a fully resolved path.
        target = Path(directory).resolve() / "capture.png"
        result = _run_safely(
            [
                str(driver),
                "call",
                "get_desktop_state",
                json.dumps({"screenshot_out_file": str(target)}),
                "--raw",
                *_driver_socket_arguments(),
            ],
            timeout=30,
            text=False,
        )
        if result is None:
            return _result(
                "desktop capture",
                False,
                "driver capture failed",
                _EMBEDDED_PERMISSIONS_REMEDIATION if embedded else _PERMISSIONS_GRANT_REMEDIATION,
                blocking=False,
            )
        ok = target.is_file() and target.stat().st_size > 0
    return _result(
        "desktop capture",
        ok,
        "driver captured the desktop" if ok else "driver produced no image",
        (
            _EMBEDDED_PERMISSIONS_REMEDIATION
            if embedded
            else "Allow Screen Recording for CuaDriver in System Settings."
        ),
        blocking=False,
    )


def check_api_key() -> CheckResult:
    from ..adapter import current_environment, resolve_run_credentials

    environment = current_environment()
    key, _ = resolve_run_credentials(environment)
    return _result(
        "API key",
        bool(key),
        "resolved" if key else "missing",
        _login_remediation(environment),
    )


def check_api_access() -> CheckResult:
    """Probe the endpoint a run uses, and read the BODY, not just the status.

    Two traps, both hit for real. /v1/models can 403 for keys that drive computer use fine, so
    this asks chat/completions instead. And the API answers a billing failure with HTTP 200 carrying
    {"error": {"type": "billing_error"}} — a key with no prepaid balance looked healthy here while
    every task failed at zero steps with an empty stderr. Status codes alone cannot see that.
    """
    from ..adapter import current_environment, resolve_run_credentials

    environment = current_environment()
    remediation = _api_access_remediation(environment)

    def report(ok: bool, detail: str) -> CheckResult:
        # `remediation` is read here at call time, not capture time, so the
        # invalid_model/billing overrides below (assigned to the enclosing
        # function's `remediation` before either return) still apply.
        return _result("Yutori API", ok, detail, remediation)

    try:
        key, base_url = resolve_run_credentials(environment)
        request = Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(
                {
                    "model": MODEL,
                    "tool_set": TOOL_SET,
                    "messages": [{"role": "user", "content": "ping"}],
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode() or "{}")
    except HTTPError as error:
        if error.code in {401, 403}:
            return report(False, "credential rejected")
        try:
            body = error.read()
            error_payload = json.loads(body.decode()) if isinstance(body, bytes) else {}
        except (AttributeError, OSError, ValueError):
            error_payload = {}
        api_error = error_payload.get("error") if isinstance(error_payload, dict) else None
        if isinstance(api_error, dict):
            message = str(api_error.get("message") or f"probe failed (HTTP {error.code})")
            if api_error.get("code") == "invalid_model":
                remediation = (
                    f"This build requests {MODEL!r}; use an environment where that model is enabled "
                    "or select a build configured for an available computer-use model."
                )
            return report(False, message)
        return report(False, f"probe failed (HTTP {error.code})")
    except (URLError, OSError, ValueError):
        return report(False, "unreachable")

    error = payload.get("error")
    if isinstance(error, dict):
        kind = str(error.get("type") or error.get("code") or "error")
        message = str(error.get("message") or kind)
        remediation = (
            "Add prepaid balance to this key's account, then retry."
            if "billing" in kind or "funds" in kind
            else remediation
        )
        return report(False, message)
    if not payload.get("choices"):
        return report(False, "no completion returned")
    return report(True, "computer-use model returned a completion")


_PLATFORM_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_macos,
    check_architecture,
)

_ENVIRONMENT_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_runtime,
    check_driver_app,
    # Ordered before the contract check, which shells out to the binary: "cua-driver is not
    # installed where we look" is the actionable blocker, not the timeout it would cause.
    check_driver_binary,
    check_driver_contract,
    check_daemon_identity,
    check_permissions,
    check_gui_session,
    check_capture,
    check_compiler,
    check_overlay,
    check_api_key,
    check_api_access,
)

# The latency-sensitive gate before every real run. Keep checks that prevent a
# session from starting safely, but leave diagnostic-only probes to ``doctor``:
# desktop capture, compiler, and overlay failures are explicitly non-blocking,
# while ``check_api_access`` makes a synthetic model request that the run's first
# real completion immediately supersedes. On a healthy development Mac those
# four probes accounted for most of the pre-run wall time.
_RUN_BLOCKING_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_macos,
    check_architecture,
    check_runtime,
    # Resolve credentials before touching the driver so a missing login fails fast.
    check_api_key,
    check_driver_app,
    check_driver_binary,
    check_driver_contract,
    check_daemon_identity,
    check_permissions,
    check_gui_session,
)


def checks_for() -> tuple[Callable[[], CheckResult], ...]:
    return _PLATFORM_CHECKS + _ENVIRONMENT_CHECKS


def run_checks() -> list[CheckResult]:
    platform_results = [check() for check in _PLATFORM_CHECKS]
    runtime = check_runtime()
    if not runtime.ok:
        return [*platform_results, runtime]
    return [*platform_results, runtime, *(check() for check in _ENVIRONMENT_CHECKS[1:])]


def first_blocker() -> CheckResult | None:
    """Return the first safety blocker without running diagnostic-only probes.

    ``run_checks()`` remains the exhaustive readiness audit used by
    ``computer-use doctor``, including capture/overlay warnings and the synthetic
    API completion. A real run needs only the cheap local gates here; its first
    model request is the authoritative API-access check.
    """
    for check in _RUN_BLOCKING_CHECKS:
        result = check()
        if not result.ok:
            return result
    return None


def blocker_message(blocker: CheckResult) -> str:
    """Render a blocking check's detail and remediation as the one-line message callers print."""
    return f"{blocker.detail} Fix: {blocker.remediation}"
