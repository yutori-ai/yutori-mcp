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
SDK_VERSION = "0.9.9"
# The PyPI wheel digest and a digest derived from its stable RECORD entries.
# Doctor compares the latter with the unpacked installation before any task runs.
SDK_ARTIFACT_SHA256 = "c1e64663033eb3a6550d6b3af8de331825ebdf5e8ecb7f39575b9544a1b59908"
SDK_INSTALLATION_SHA256 = "5309391db4d8af899cc84f517969545faf75e8878f04d9278d5af8ed9ded0be6"
SDK_PROVENANCE_SHA256 = "7cab595d2f00e1a9ab5cd121204fbd6dd4c24163ead3eb76f76eef7fba495fc4"

# The cua-driver release that implements this tool contract, and the checksum
# of its installer script. Both are hard gates.
DRIVER_VERSION = "0.23.2"
DRIVER_INSTALLER_SHA256 = "317ba3a49fdba10f2a7f1b9f392c1bc1b7657f3aae85e1e2e43684cf17a1bf3b"
