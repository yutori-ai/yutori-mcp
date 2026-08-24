from __future__ import annotations

from importlib import import_module
from typing import Any

PROTOCOL_VERSION = 1
# The Node runner's wheel is an optional extra now that the Python harness is
# the default, so "not installed" usually means the extra was never asked for
# rather than a broken install.
REMEDIATION = (
    "The node harness is optional: reinstall yutori-mcp with its node-harness "
    "extra (yutori-mcp[node-harness]), or use the default python harness."
)


class RuntimeValidationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(f"Computer-use runtime {reason}. {REMEDIATION}")
        self.remediation = REMEDIATION


def load_runtime() -> Any:
    try:
        runtime = import_module("yutori_computer_use_runtime")
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeValidationError("is not installed") from error
    if runtime.PROTOCOL_VERSION != PROTOCOL_VERSION:
        raise RuntimeValidationError(
            f"protocol mismatch (expected {PROTOCOL_VERSION}, got {runtime.PROTOCOL_VERSION})"
        )
    if not runtime.verify_runner():
        raise RuntimeValidationError("failed its integrity check")
    return runtime


def get_manifest() -> dict[str, Any]:
    return dict(load_runtime().get_manifest())
