from __future__ import annotations

import argparse
import asyncio
import hashlib
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlopen

from ..credentials import resolve_api_key_for_environment

from .constants import DRIVER_INSTALLER_SHA256, DRIVER_VERSION
from .preflight import (
    check_driver_binary,
    check_harness,
    child_search_path,
    find_cua_driver,
    run_checks,
)
from .result import format_result
from .supervisor import run_task


def _doctor() -> int:
    results = run_checks()
    for result in results:
        print(f"{'PASS' if result.ok else 'BLOCKED'} {result.name}: {result.detail}")
        if result.remediation:
            print(f"  Fix: {result.remediation}")
    return 0 if all(result.ok for result in results) else 1


def _download_installer(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def _setup() -> int:
    harness_result = check_harness()
    if not harness_result.ok:
        print(harness_result.remediation)
        return 1
    version = DRIVER_VERSION
    installer = _download_installer(
        f"https://github.com/trycua/cua/releases/download/cua-driver-rs-v{version}/install.sh"
    )
    if hashlib.sha256(installer).hexdigest() != DRIVER_INSTALLER_SHA256:
        print("Driver installer checksum mismatch; nothing was executed.")
        return 1
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "install.sh"
        path.write_bytes(installer)
        path.chmod(0o700)
        env = {
            "PATH": child_search_path(),
            "HOME": str(Path.home()),
            "CUA_DRIVER_RS_VERSION": version,
        }
        subprocess.run([str(path)], env=env, check=True)
    subprocess.run(
        ["open", "-n", "-g", "-a", "CuaDriver", "--args", "serve"], check=True
    )
    # Resolved after the installer runs, and by absolute path: a Dock-launched MCP client's PATH
    # does not include Homebrew, so the bare name would not be found here.
    driver = find_cua_driver()
    if driver is None:
        print(check_driver_binary().remediation)
        return 1
    subprocess.run([str(driver), "permissions", "grant"], check=True)
    return _doctor()


async def _smoke_live() -> int:
    harness_result = check_harness()
    if not harness_result.ok:
        print(harness_result.remediation)
        return 1
    mechanical = await asyncio.create_subprocess_exec(
        "osascript",
        "-e",
        'tell application "Calculator" to activate',
        "-e",
        'tell application "System Events" to keystroke "6*7="',
        "-e",
        "delay 1",
        "-e",
        'tell application "System Events" to tell process "Calculator" to get value of first static text of window 1',
        stdout=asyncio.subprocess.PIPE,
    )
    output, _ = await mechanical.communicate()
    if mechanical.returncode != 0 or "42" not in output.decode():
        print("Mechanical Calculator check failed; verify Accessibility permission.")
        return 1
    result = await run_task(
        task="In Calculator, clear the display, compute 9 * 9, and report the result.",
        app="Calculator",
        start_url=None,
        minutes=1,
        max_steps=10,
        api_key=resolve_api_key_for_environment("dev"),
        api_base_url="https://api.dev.yutori.com/v1",
    )
    print(format_result(result))
    return 0 if result.get("outcome") == "completed" else 1


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "computer-use", help="Set up and diagnose the macOS computer-use preview"
    )
    commands = parser.add_subparsers(dest="computer_use_command", required=True)
    commands.add_parser("setup", help="Install and configure the pinned CuaDriver")
    commands.add_parser("doctor", help="Run all computer-use readiness checks")
    commands.add_parser("smoke", help="Run Calculator mechanical and live checks")


def dispatch(command: str) -> int:
    if command == "setup":
        return _setup()
    if command == "doctor":
        return _doctor()
    if command == "smoke":
        return asyncio.run(_smoke_live())
    raise ValueError(f"Unknown computer-use command: {command}")
