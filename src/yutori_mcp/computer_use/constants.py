"""Pins for the macOS computer-use harness.

These used to live in the Node runtime wheel's manifest. The harness is now the
SDK's `yutori.navigator.N2ComputerAgent` plus this module, so the MCP server
itself is the single owner of the driver release it was verified against and
the tool set a run exposes.
"""

from __future__ import annotations

import os

PROTOCOL_VERSION = 1

MODEL = "n2-preview"

# Two runner implementations speak the same JSONL protocol: "python" (the
# default) spawns this package's runner module driving the SDK-owned
# yutori.navigator loop, "node" spawns the legacy runtime wheel's runner.mjs
# under Node 22.
# The Node harness is opt-in only — its wheel installs via the `node-harness`
# extra — and is kept for head-to-head comparison until the evaluation
# concludes, when the flag and the losing harness are expected to be removed.
HARNESS_NODE = "node"
HARNESS_PYTHON = "python"
HARNESSES = (HARNESS_NODE, HARNESS_PYTHON)
DEFAULT_HARNESS = HARNESS_PYTHON
ENV_VAR_HARNESS = "YUTORI_COMPUTER_USE_HARNESS"


def resolve_harness(requested: str | None = None) -> str:
    """The harness a run should use: explicit request, then env, then default."""
    value = requested or os.environ.get(ENV_VAR_HARNESS) or DEFAULT_HARNESS
    if value not in HARNESSES:
        raise ValueError(
            f"Unknown computer-use harness {value!r}; choose one of {', '.join(HARNESSES)}."
        )
    return value

# The dated id decides which tools the model may call, and the server default
# has already moved twice — so a run always sends it explicitly. 20260815 is
# the trained batch-only surface: nested computer_batch members, screenshot,
# bash, and held modifiers on clicks.
TOOL_SET = "computer_use_tools-20260815"

# The cua-driver release this harness was verified against, and the checksum of
# its installer script. Both are hard gates: modifier-click fidelity depends on
# the exact driver contract, and setup executes the installer.
DRIVER_VERSION = "0.19.3"
DRIVER_INSTALLER_SHA256 = (
    "52293f8683c6c41ef8df0bb17907f3bd9266314e04f7b0c8f3c4576e7ba139f7"
)
