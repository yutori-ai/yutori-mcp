from __future__ import annotations

import importlib.util
import json
import platform
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..credentials import resolve_api_key_for_environment

from .constants import (
    DRIVER_VERSION,
    HARNESS_PYTHON_MAX_EXCLUSIVE,
    HARNESS_PYTHON_MIN,
)

DRIVER_APP = Path("/Applications/CuaDriver.app")
DEV_ACCESS_REMEDIATION = (
    "Store a dev key with: yutori-mcp --env dev login "
    "(a production key is rejected by the dev stack). If it is already a dev key, ask "
    "Yutori for n2-preview access."
)
# Named rather than inlined at both call sites: the previous text blamed missing access for
# what is almost always a production key offered to dev, sending people to the wrong fix.
DEV_ENVIRONMENT = "dev"
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


def check_harness() -> CheckResult:
    """Whether the interpreter can run the pinned cua-agent harness.

    The harness needs a newer Python than the rest of the MCP server declares,
    so the dependency carries an interpreter marker and this check is what
    tells a 3.10 install why the tool is unavailable instead of an ImportError
    mid-run. find_spec keeps the probe cheap — actually importing cua_agent
    pulls litellm into the server process for no reason.
    """
    floor = ".".join(str(part) for part in HARNESS_PYTHON_MIN)
    ceiling = ".".join(str(part) for part in HARNESS_PYTHON_MAX_EXCLUSIVE)
    remediation = (
        "Reinstall with a supported interpreter: uvx --python 3.12 --refresh yutori-mcp"
    )
    version = ".".join(str(part) for part in sys.version_info[:3])
    if not (HARNESS_PYTHON_MIN <= sys.version_info[:2] < HARNESS_PYTHON_MAX_EXCLUSIVE):
        return _result(
            "harness",
            False,
            f"Python {version} is outside the supported window [{floor}, {ceiling})",
            remediation,
        )
    if importlib.util.find_spec("cua_agent") is None:
        return _result(
            "harness", False, "cua-agent is not installed", remediation
        )
    return _result("harness", True, f"cua-agent on Python {version}", remediation)


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

    Deliberately not an equality gate. DRIVER_VERSION records the release this
    harness was verified against, and a different one is not automatically broken: 0.18.0 drove a
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
    pinned = DRIVER_VERSION
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
    key = resolve_api_key_for_environment(DEV_ENVIRONMENT)
    return _result(
        "API key",
        bool(key),
        "resolved" if key else "missing",
        # Not plain `login`: that saves a production key, which is the misdiagnosis this
        # change exists to remove.
        f"Run: uvx yutori-mcp --env {DEV_ENVIRONMENT} login",
    )


def check_dev_access() -> CheckResult:
    """Probe the endpoint a run uses, and read the BODY, not just the status.

    Two traps, both hit for real. /v1/models 403s for keys that drive n2-preview fine, so this
    asks chat/completions instead. And the API answers a billing failure with HTTP 200 carrying
    {"error": {"type": "billing_error"}} — a key with no prepaid balance looked healthy here while
    every task failed at zero steps with an empty stderr. Status codes alone cannot see that.
    """
    try:
        key = resolve_api_key_for_environment(DEV_ENVIRONMENT)
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
    check_harness,
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
