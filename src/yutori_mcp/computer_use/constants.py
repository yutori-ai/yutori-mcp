"""Immutable versions for the Python-only macOS computer-use runtime."""

from __future__ import annotations

from .. import __version__

PROTOCOL_VERSION = 1
MCP_VERSION = __version__
MODEL = "n2-preview"
TOOL_SET = "computer_use_tools-20260815"
SDK_VERSION = "0.9.1"
# The PyPI wheel digest and a digest derived from its stable RECORD entries.
# Doctor compares the latter with the unpacked installation before any task runs.
SDK_ARTIFACT_SHA256 = "f29f7281e7f4664657d86db622d453c0f824c74b4b8c08e8dcef0e51004d354a"
SDK_INSTALLATION_SHA256 = "f30ae52505f674d3842add33f7912ad4e647ce5afc2c7a84e5a84571dc4cb28c"
SDK_PROVENANCE_SHA256 = "c496f00cc9db293dfbef0fea37e896eba0262205b66577d5fcfa0b243ed8e339"

# The cua-driver release that implements this tool contract, and the checksum
# of its installer script. Both are hard gates.
DRIVER_VERSION = "0.19.3"
DRIVER_INSTALLER_SHA256 = "52293f8683c6c41ef8df0bb17907f3bd9266314e04f7b0c8f3c4576e7ba139f7"
