from __future__ import annotations

import json
import platform
import re
import subprocess
import tempfile
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
DEV_ACCESS_REMEDIATION = "Ask Yutori for dev n2-preview access, then retry."
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
    try:
        output = subprocess.run(
            [str(driver), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", output)
    return match.group(0) if match else None


def check_driver_contract() -> CheckResult:
    """Require a driver that answers, and report whether it matches the pin.

    Deliberately not an equality gate. The manifest's driver_version records the release this
    runtime was verified against, and a different one is not automatically broken: 0.18.0 drove a
    full task correctly while the pin read 0.19.3. Blocking there would have refused a working
    machine over a version string. A live smoke run is the real gate, so this reports the drift
    rather than pretending to know it is fatal.
    """
    installed = driver_version()
    if installed is None:
        return _result(
            "driver contract",
            False,
            "driver did not report a version",
            "Run: yutori-mcp computer-use setup",
        )
    try:
        pinned = str(get_manifest()["driver_version"])
    except (RuntimeValidationError, KeyError):
        pinned = "unknown"
    matches = installed == pinned
    detail = (
        f"{installed}"
        if matches
        else f"{installed} (verified against {pinned}; run smoke to confirm)"
    )
    return _result(
        "driver contract", True, detail, "Run: yutori-mcp computer-use setup"
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
    """Ask the machine who owns the console, not this process about itself.

    The previous probe ran Quartz in-process and read CGSessionCopyCurrentDictionary. Over SSH
    that process has no window session, so it reported "inactive or locked" on a Mac that was
    logged in and driving apps fine — blocking every remote and headless setup. The console
    owner is a property of the machine and answers the question that actually matters.
    """
    try:
        owner = subprocess.run(
            ["/usr/bin/stat", "-f", "%Su", "/dev/console"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        owner = ""
    # root or _windowserver owns the console at the login window, i.e. nobody is logged in.
    ok = bool(owner) and owner not in {"root", "_windowserver"}
    return _result(
        "GUI session",
        ok,
        f"console user {owner}" if ok else "no user logged in at the console",
        "Log in to the Mac and unlock the desktop.",
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
        )
    with tempfile.TemporaryDirectory(prefix="cua-capture-check-") as directory:
        # Under $TMPDIR, whose /var -> /private/var symlink the driver rejects as an unresolved
        # ancestor, so hand it a fully resolved path.
        target = Path(directory).resolve() / "capture.png"
        try:
            subprocess.run(
                [
                    str(driver),
                    "call",
                    "get_desktop_state",
                    json.dumps({"screenshot_out_file": str(target)}),
                    "--raw",
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return _result(
                "desktop capture",
                False,
                "driver capture failed",
                "Run: cua-driver permissions grant",
            )
        ok = target.is_file() and target.stat().st_size > 0
    return _result(
        "desktop capture",
        ok,
        "driver captured the desktop" if ok else "driver produced no image",
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
    """Probe the endpoint a run uses, and read the BODY, not just the status.

    Two traps, both hit for real. /v1/models 403s for keys that drive n2-preview fine, so this
    asks chat/completions instead. And the API answers a billing failure with HTTP 200 carrying
    {"error": {"type": "billing_error"}} — a key with no prepaid balance looked healthy here while
    every task failed at zero steps with an empty stderr. Status codes alone cannot see that.
    """
    try:
        key = resolve_api_key()
        request = Request(
            "https://api.dev.yutori.com/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "n2-preview",
                    "tool_set": "computer_use_tools-20260728",
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
            return _result(
                "dev API", False, "credential rejected", DEV_ACCESS_REMEDIATION
            )
        return _result(
            "dev API", True, f"reachable (HTTP {error.code})", DEV_ACCESS_REMEDIATION
        )
    except (URLError, OSError, ValueError):
        return _result("dev API", False, "unreachable", DEV_ACCESS_REMEDIATION)

    error = payload.get("error")
    if isinstance(error, dict):
        kind = str(error.get("type") or error.get("code") or "error")
        message = str(error.get("message") or kind)
        remediation = (
            "Add prepaid balance to this key's account, then retry."
            if "billing" in kind or "funds" in kind
            else DEV_ACCESS_REMEDIATION
        )
        return _result("dev API", False, message, remediation)
    if not payload.get("choices"):
        return _result(
            "dev API", False, "no completion returned", DEV_ACCESS_REMEDIATION
        )
    return _result(
        "dev API", True, "n2-preview returned a completion", DEV_ACCESS_REMEDIATION
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
