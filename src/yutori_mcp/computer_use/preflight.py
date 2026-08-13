from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from yutori.auth.credentials import resolve_api_key

from .runtime import RuntimeValidationError, get_manifest

NODE_PATHS = (
    Path("/opt/homebrew/opt/node@22/bin/node"),
    Path("/usr/local/opt/node@22/bin/node"),
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
)
DRIVER_APP = Path("/Applications/CuaDriver.app")
DRIVER_PATHS = (
    Path("/opt/homebrew/bin/cua-driver"),
    Path("/usr/local/bin/cua-driver"),
    Path.home() / ".cargo" / "bin" / "cua-driver",
)
# An MCP client launched from the Dock inherits a minimal PATH that omits Homebrew, so every
# tool we shell out to is resolved from an explicit list instead of the ambient PATH. The
# runner subprocess needs this as its PATH too: it execs `cua-driver` by bare name, and its
# macOS observation encoder execs `sips`.
TOOL_SEARCH_DIRECTORIES = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


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


def check_driver_binary() -> CheckResult:
    driver = find_cua_driver()
    return _result(
        "cua-driver binary",
        driver is not None,
        str(driver or "not found"),
        "Run: yutori-mcp computer-use setup",
    )


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    remediation: str | None = None


def _result(name: str, ok: bool, detail: str, remediation: str) -> CheckResult:
    return CheckResult(name, ok, detail, None if ok else remediation)


def check_macos() -> CheckResult:
    ok = (
        platform.system() == "Darwin"
        and int(platform.mac_ver()[0].split(".")[0] or 0) >= 15
    )
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


def find_node() -> Path | None:
    for path in NODE_PATHS:
        if path.is_file():
            try:
                version = subprocess.run(
                    [str(path), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            if version.startswith("v22."):
                return path
    return None


def check_node() -> CheckResult:
    node = find_node()
    return _result(
        "Node 22",
        node is not None,
        str(node or "not found"),
        "Install Node 22 with: brew install node@22",
    )


def check_runtime() -> CheckResult:
    try:
        manifest = get_manifest()
        return CheckResult("runtime", True, f"protocol {manifest['protocol_version']}")
    except RuntimeValidationError as error:
        return CheckResult("runtime", False, str(error), error.remediation)


def check_driver_app() -> CheckResult:
    return _result(
        "driver app",
        DRIVER_APP.is_dir(),
        str(DRIVER_APP),
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


def check_driver_contract() -> CheckResult:
    try:
        info = _driver_json("status")
        manifest = get_manifest()
        ok = (
            info.get("version") == manifest["driver_version"]
            and info.get("tool_set") == manifest["tool_set"]
        )
        return _result(
            "driver contract",
            ok,
            f"version {info.get('version')}",
            "Run: yutori-mcp computer-use setup",
        )
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        RuntimeValidationError,
    ) as error:
        return CheckResult(
            "driver contract", False, str(error), "Run: yutori-mcp computer-use setup"
        )


def check_daemon_identity() -> CheckResult:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "/Applications/CuaDriver.app/Contents/MacOS/"],
            capture_output=True,
            check=False,
        )
    except OSError:
        result = subprocess.CompletedProcess([], 1)
    return _result(
        "daemon identity",
        result.returncode == 0,
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


def check_gui_session() -> CheckResult:
    try:
        result = subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                "import Quartz; print(Quartz.CGSessionCopyCurrentDictionary())",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = subprocess.CompletedProcess([], 1, stdout="")
    ok = (
        result.returncode == 0
        and "kCGSessionOnConsoleKey = 1" in result.stdout
        and "CGSSessionScreenIsLocked = 1" not in result.stdout
    )
    return _result(
        "GUI session",
        ok,
        "active and unlocked" if ok else "inactive or locked",
        "Log in to the Mac and unlock the desktop.",
    )


def check_capture() -> CheckResult:
    try:
        result = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "/dev/null"],
            capture_output=True,
            check=False,
        )
    except OSError:
        result = subprocess.CompletedProcess([], 1)
    return _result(
        "desktop capture",
        result.returncode == 0,
        "capture test",
        "Allow Screen Recording for CuaDriver in System Settings.",
    )


def check_api_key() -> CheckResult:
    key = resolve_api_key()
    return _result(
        "API key",
        bool(key),
        "resolved" if key else "missing",
        "Run: uvx yutori-mcp login",
    )


def check_dev_access() -> CheckResult:
    try:
        key = resolve_api_key()
        request = Request(
            "https://api.dev.yutori.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urlopen(request, timeout=10) as response:
            ok = response.status < 400
    except (HTTPError, URLError, OSError, ValueError):
        ok = False
    return _result(
        "dev API",
        ok,
        "reachable" if ok else "unavailable",
        "Ask Yutori for dev n2-preview access, then retry.",
    )


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_macos,
    check_architecture,
    check_node,
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
    check_api_key,
    check_dev_access,
)


def run_checks() -> list[CheckResult]:
    return [check() for check in CHECKS]


def first_blocker() -> CheckResult | None:
    for check in CHECKS:
        result = check()
        if not result.ok:
            return result
    return None
