"""Immutable versions for the Python-only macOS computer-use runtime."""

from __future__ import annotations

from .. import __version__

PROTOCOL_VERSION = 2
MCP_VERSION = __version__
MODEL = "n2"
# The eval-exact n2 desktop surface, and the SDK's TOOL_SET_COMPUTER_USE_LATEST:
# computer_batch + bash + read/write/edit, with `screenshot` as a batch member
# rather than a tool of its own. Pinned by date rather than read from the SDK
# constant so an SDK bump can never silently move the surface the model is
# served -- see doctor's tool_set preflight, which sends this exact string.
TOOL_SET = "computer_use_tools-20260830"
# The two delivery modes this runtime implements. "foreground" drives the visible desktop
# (the model sees the whole screen and the user keeps their hands off); "background" drives
# one target app window through the SDK's window scope without taking the user's focus.
# Every `action`/`result` protocol event carries one of these (runner.py, and result.py's
# terminal_result() shape, which the supervisor's timeout/cancellation fallbacks build
# through); centralized so no call site can drift onto a stray literal.
DELIVERY_MODE_FOREGROUND = "foreground"
DELIVERY_MODE_BACKGROUND = "background"
DELIVERY_MODES = (DELIVERY_MODE_FOREGROUND, DELIVERY_MODE_BACKGROUND)
SDK_VERSION = "0.9.14"
# The PyPI wheel digest and a digest derived from its stable RECORD entries.
# Doctor compares the latter with the unpacked installation before any task runs.
SDK_ARTIFACT_SHA256 = "81f2b7e124eaba0501e23142a9a83f145ddb69d9e46ace9955e12a794b02aca7"
SDK_INSTALLATION_SHA256 = "629893d1286a8893db1538c216d6d077c1ff20515f9e5b4b0708e9a503cbe35f"
SDK_PROVENANCE_SHA256 = "7cab595d2f00e1a9ab5cd121204fbd6dd4c24163ead3eb76f76eef7fba495fc4"

# The cua-driver release that implements this tool contract, and the checksum
# of its installer script. Both are hard gates.
DRIVER_VERSION = "0.23.2"
DRIVER_INSTALLER_SHA256 = "317ba3a49fdba10f2a7f1b9f392c1bc1b7657f3aae85e1e2e43684cf17a1bf3b"
