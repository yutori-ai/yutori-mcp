from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import tempfile
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


def _login_remediation(environment: str) -> str:
    from ..adapter import DEFAULT_ENVIRONMENT

    if environment == DEFAULT_ENVIRONMENT:
        return "Run: uvx yutori-mcp login"
    return f"Run: uvx yutori-mcp --env {environment} login"


def _api_access_remediation(environment: str) -> str:
    return f"{_login_remediation(environment)}. If already logged in, confirm this key has computer-use access."


def find_cua_driver() -> Path | None:
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
        "Run: yutori-mcp computer-use setup",
        blocking=False,
    )


def check_driver_app() -> CheckResult:
    return _result(
        "driver app",
        DRIVER_APP.is_dir(),
        str(DRIVER_APP),
        "Run: yutori-mcp computer-use setup",
    )


def check_driver_binary() -> CheckResult:
    driver = find_cua_driver()
    return _result(
        "cua-driver binary",
        driver is not None,
        str(driver or "not found"),
        "Run: yutori-mcp computer-use setup",
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
            "Run: yutori-mcp computer-use setup",
        )
    return _result(
        "driver contract",
        installed == DRIVER_VERSION,
        installed,
        f"Install CuaDriver {DRIVER_VERSION}: yutori-mcp computer-use setup",
    )


def check_daemon_identity() -> CheckResult:
    result = _run_safely(["pgrep", "-f", "/Applications/CuaDriver.app/Contents/MacOS/"], timeout=10)
    return _result(
        "daemon identity",
        result is not None and result.returncode == 0,
        "app-bundle daemon",
        "Start it with: open -n -g -a CuaDriver --args serve",
    )


def check_permissions() -> CheckResult:
    try:
        info = _driver_json("permissions")
        ok = bool(info.get("accessibility")) and bool(info.get("screen_recording"))
    except (OSError, subprocess.SubprocessError, ValueError):
        ok = False
    return _result(
        "permissions",
        ok,
        "Accessibility and Screen Recording",
        "Run: cua-driver permissions grant",
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
            "Run: yutori-mcp computer-use setup",
            blocking=False,
        )
    with tempfile.TemporaryDirectory(prefix="cua-capture-check-") as directory:
        # Under $TMPDIR, whose /var -> /private/var symlink the driver rejects as an unresolved
        # ancestor, so hand it a fully resolved path.
        target = Path(directory).resolve() / "capture.png"
        result = _run_safely(
            [str(driver), "call", "get_desktop_state", json.dumps({"screenshot_out_file": str(target)}), "--raw"],
            timeout=30,
            text=False,
        )
        if result is None:
            return _result(
                "desktop capture",
                False,
                "driver capture failed",
                "Run: cua-driver permissions grant",
                blocking=False,
            )
        ok = target.is_file() and target.stat().st_size > 0
    return _result(
        "desktop capture",
        ok,
        "driver captured the desktop" if ok else "driver produced no image",
        "Allow Screen Recording for CuaDriver in System Settings.",
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


def checks_for() -> tuple[Callable[[], CheckResult], ...]:
    return _PLATFORM_CHECKS + _ENVIRONMENT_CHECKS


def run_checks() -> list[CheckResult]:
    platform_results = [check() for check in _PLATFORM_CHECKS]
    runtime = check_runtime()
    if not runtime.ok:
        return [*platform_results, runtime]
    return [*platform_results, runtime, *(check() for check in _ENVIRONMENT_CHECKS[1:])]


def first_blocker() -> CheckResult | None:
    for check in checks_for():
        result = check()
        if not result.ok and result.blocking:
            return result
    return None


def blocker_message(blocker: CheckResult) -> str:
    """Render a blocking check's detail and remediation as the one-line message callers print."""
    return f"{blocker.detail} Fix: {blocker.remediation}"
