"""Which exact yutori SDK this runtime trusts.

A runtime installed from PyPI trusts the SDK release pinned in ``constants``. An application
that assembles its own runtime (Yutori Local builds one into its signed bundle from git commits)
writes a host pin next to the interpreter instead, so shipping an SDK change does not have to wait
for an SDK release and a yutori-mcp release that re-pins it. The pin carries the same digests the
release constants do, and ``preflight.check_runtime`` verifies them just as strictly.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    SDK_ARTIFACT_SHA256,
    SDK_INSTALLATION_SHA256,
    SDK_PROVENANCE_SHA256,
    SDK_VERSION,
)

HOST_PIN_FILENAME = "yutori-runtime-pin.json"
HOST_PIN_SCHEMA = 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SdkPinError(ValueError):
    """A host pin exists but cannot be trusted; the runtime must refuse to run rather than fall back."""


@dataclass(frozen=True)
class SdkPin:
    version: str
    artifact_sha256: str
    installation_sha256: str
    provenance_sha256: str
    # Where a host-assembled SDK came from (e.g. a git URL at a commit); None for the PyPI release pin.
    source: str | None = None

    @property
    def host_pinned(self) -> bool:
        return self.source is not None


RELEASE_PIN = SdkPin(SDK_VERSION, SDK_ARTIFACT_SHA256, SDK_INSTALLATION_SHA256, SDK_PROVENANCE_SHA256)


def host_pin_path() -> Path:
    # sys.prefix, not an environment variable: the pin describes this installation, so it has to
    # live inside it. A uvx or pip environment has no such file and keeps the release pin.
    return Path(sys.prefix) / HOST_PIN_FILENAME


def sdk_pin() -> SdkPin:
    path = host_pin_path()
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return RELEASE_PIN
    except OSError as error:
        raise SdkPinError(f"host pin {path} is unreadable: {error}") from error
    return parse_host_pin(raw, path)


def parse_host_pin(raw: str, path: Path) -> SdkPin:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SdkPinError(f"host pin {path} is not JSON: {error}") from error
    if not isinstance(data, dict) or data.get("schema") != HOST_PIN_SCHEMA:
        raise SdkPinError(f"host pin {path} is not schema {HOST_PIN_SCHEMA}")
    fields = (
        "sdk_version",
        "sdk_source",
        "sdk_artifact_sha256",
        "sdk_installation_sha256",
        "sdk_provenance_sha256",
    )
    values = {name: data.get(name) for name in fields}
    if missing := [name for name, value in values.items() if not isinstance(value, str) or not value]:
        raise SdkPinError(f"host pin {path} is missing {', '.join(missing)}")
    if malformed := [
        name for name in fields if name.endswith("_sha256") and not _SHA256_PATTERN.fullmatch(values[name])
    ]:
        raise SdkPinError(f"host pin {path} has malformed {', '.join(malformed)}")
    return SdkPin(
        version=values["sdk_version"],
        artifact_sha256=values["sdk_artifact_sha256"],
        installation_sha256=values["sdk_installation_sha256"],
        provenance_sha256=values["sdk_provenance_sha256"],
        source=values["sdk_source"],
    )


def host_pin_payload(pin: SdkPin) -> str:
    if pin.source is None:
        raise ValueError("a host pin needs the SDK source it was assembled from")
    payload = {
        "schema": HOST_PIN_SCHEMA,
        "sdk_version": pin.version,
        "sdk_source": pin.source,
        "sdk_artifact_sha256": pin.artifact_sha256,
        "sdk_installation_sha256": pin.installation_sha256,
        "sdk_provenance_sha256": pin.provenance_sha256,
    }
    return json.dumps(payload, indent=2) + "\n"
