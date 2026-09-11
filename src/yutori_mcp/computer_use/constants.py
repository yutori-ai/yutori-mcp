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
# Request frames are always sent as WebP. Keep this explicit instead of relying on
# N2ComputerAgent's default so an SDK upgrade cannot silently move high-resolution
# macOS screenshots back to a larger wire encoding.
OBSERVATION_FORMAT = "webp"
# The two delivery modes this runtime implements. "foreground" drives the visible desktop
# (the model sees the whole screen and the user keeps their hands off); "background" drives
# one target app window through the SDK's window scope without taking the user's focus.
# Every `action`/`result` protocol event carries one of these (runner.py, and result.py's
# terminal_result() shape, which the supervisor's timeout/cancellation fallbacks build
# through); centralized so no call site can drift onto a stray literal.
DELIVERY_MODE_FOREGROUND = "foreground"
DELIVERY_MODE_BACKGROUND = "background"
DELIVERY_MODES = (DELIVERY_MODE_FOREGROUND, DELIVERY_MODE_BACKGROUND)
# Foreground runs keep the overlay in screen recordings and screen shares by default: the SDK
# takes the model's frames through the overlay host with its own windows filtered out
# (`exclude_overlay_from_capture=False`), so the model never sees it and nothing fades around a
# capture. Set to "0" to make the overlay opt out of screen capture altogether instead, which
# also hides it from recorders. Read by the runner; the supervisor forwards it to the runner's
# environment.
ENV_RECORDABLE_OVERLAY = "YUTORI_RECORDABLE_OVERLAY"
SDK_VERSION = "0.9.26"
# The PyPI wheel digest and a digest derived from its stable RECORD entries.
# Doctor compares the latter with the unpacked installation before any task runs.
SDK_ARTIFACT_SHA256 = "d4fa68c29d72e26bf470918036c93677e0cfd9a4d699dc900a03f79768997b70"
SDK_INSTALLATION_SHA256 = "044ba07b30f484f530d516ec0de445a4a42dd791918c2149cf78868ca30fad59"
SDK_PROVENANCE_SHA256 = "04ccce6bc1c85552bdb9ad68fc3973c88c3b4ddf7e2314e9779fc00a33812c3d"

# The cua-driver release that implements this tool contract, and the checksum
# of its installer script. Both are hard gates.
DRIVER_VERSION = "0.23.2"
DRIVER_INSTALLER_SHA256 = "317ba3a49fdba10f2a7f1b9f392c1bc1b7657f3aae85e1e2e43684cf17a1bf3b"
