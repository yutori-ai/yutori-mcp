"""Pins for the macOS computer-use harness.

These used to live in the Node runtime wheel's manifest. The harness is now the
pinned `cua-agent` package plus this module, so the MCP server itself is the
single owner of the driver release it was verified against and the tool set a
run exposes.
"""

from __future__ import annotations

import os

PROTOCOL_VERSION = 1

MODEL = "n2-preview"

# Two runner implementations speak the same JSONL protocol: "python" (the
# default) spawns this package's runner module driving the pinned cua-agent
# loop, "node" spawns the legacy runtime wheel's runner.mjs under Node 22.
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
# has already moved twice — so a run always sends it explicitly. 20260729 is
# the hybrid batch surface (GUI computer_batch + screenshot + shell_command)
# the previous Node runner shipped; 20260815 is deliberately not used because
# it advertises a held `modifier` on the click family that this executor
# cannot deliver.
TOOL_SET = "computer_use_tools-20260729"

# The cua-driver release this harness was verified against, and the checksum of
# its installer script. check_driver_contract reports drift against the version
# without blocking (0.18.0 drove full tasks correctly while the pin read
# 0.19.3); the installer checksum is a hard gate because setup executes it.
DRIVER_VERSION = "0.19.3"
DRIVER_INSTALLER_SHA256 = (
    "52293f8683c6c41ef8df0bb17907f3bd9266314e04f7b0c8f3c4576e7ba139f7"
)

# The agent loop dependency needs a newer interpreter than the rest of the MCP
# server (cua-agent declares >=3.11,<3.14), so the computer-use tool gates on
# this at preflight instead of raising the whole package's floor.
HARNESS_PYTHON_MIN = (3, 11)
HARNESS_PYTHON_MAX_EXCLUSIVE = (3, 14)
