"""Pins for the macOS computer-use harness.

The harness is the SDK's `yutori.navigator.N2ComputerAgent` plus this module,
so the MCP server is the single owner of the driver release it was verified
against and the tool set a run exposes.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1

MODEL = "n2-preview"

# The dated id decides which tools the model may call, and the server default
# has already moved twice, so a run always sends it explicitly. This is the
# trained nested computer_batch + bash surface with held click modifiers; the
# SDK translates it to this package's driver handler.
TOOL_SET = "computer_use_tools-20260815"

# The cua-driver release this harness was verified against, and the checksum of
# its installer script. Both are hard gates: the modifier-click contract is
# release-specific, and setup executes the installer.
DRIVER_VERSION = "0.19.3"
DRIVER_INSTALLER_SHA256 = (
    "52293f8683c6c41ef8df0bb17907f3bd9266314e04f7b0c8f3c4576e7ba139f7"
)
