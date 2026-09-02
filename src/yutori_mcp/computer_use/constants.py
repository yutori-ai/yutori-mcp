"""Immutable versions for the Python-only macOS computer-use runtime."""

from __future__ import annotations

from .. import __version__

PROTOCOL_VERSION = 1
MCP_VERSION = __version__
MODEL = "n2"
TOOL_SET = "computer_use_tools-20260815"
# The only delivery mode this runtime implements today. Repeated verbatim across every
# `action`/`result` protocol event (runner.py, and result.py's terminal_result() shape,
# which the supervisor's timeout/cancellation fallbacks now build through); centralized
# so a future second mode can't be introduced with one of those spots left on the old
# literal by accident.
DELIVERY_MODE_FOREGROUND = "foreground"
SDK_VERSION = "0.9.10"
# The PyPI wheel digest and a digest derived from its stable RECORD entries.
# Doctor compares the latter with the unpacked installation before any task runs.
SDK_ARTIFACT_SHA256 = "ede183e6796b10451de4ba00da63d61872e46e4bb4fea66fdaa4d58133e334b5"
SDK_INSTALLATION_SHA256 = "43dd4313e8c09a93db8fc37c490cbb3c0435bd0b91f03a628d8807320be6ecc0"
SDK_PROVENANCE_SHA256 = "7cab595d2f00e1a9ab5cd121204fbd6dd4c24163ead3eb76f76eef7fba495fc4"

# The cua-driver release that implements this tool contract, and the checksum
# of its installer script. Both are hard gates.
DRIVER_VERSION = "0.23.2"
DRIVER_INSTALLER_SHA256 = "317ba3a49fdba10f2a7f1b9f392c1bc1b7657f3aae85e1e2e43684cf17a1bf3b"
