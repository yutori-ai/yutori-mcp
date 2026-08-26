"""Immutable versions for the Python-only macOS computer-use runtime."""

from __future__ import annotations

from .. import __version__

PROTOCOL_VERSION = 1
MCP_VERSION = __version__
MODEL = "n2"
TOOL_SET = "computer_use_tools-20260815"
SDK_VERSION = "0.9.2"
# The PyPI wheel digest and a digest derived from its stable RECORD entries.
# Doctor compares the latter with the unpacked installation before any task runs.
SDK_ARTIFACT_SHA256 = "bb2de3d77e73be6abe8e27663e425518dc6fe0170502a7337c5b59456a9e8cad"
SDK_INSTALLATION_SHA256 = "19af218c916ad34efd8212377ed9b540b87b825fda8e42b98066f8bd723f7d64"
SDK_PROVENANCE_SHA256 = "aa3c8b58281f0dcde3446cc87cfd8b9c4e57b6c5e0b2be56450eef33c09dd59c"

# The cua-driver release that implements this tool contract, and the checksum
# of its installer script. Both are hard gates.
DRIVER_VERSION = "0.19.3"
DRIVER_INSTALLER_SHA256 = "52293f8683c6c41ef8df0bb17907f3bd9266314e04f7b0c8f3c4576e7ba139f7"
