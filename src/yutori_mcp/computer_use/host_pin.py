"""Write a host pin for the SDK installed in this interpreter.

For applications that assemble their own runtime, run once at build time after installing the SDK:

    python -m yutori_mcp.computer_use.host_pin \\
        --sdk-source git+https://github.com/yutori-ai/yutori-sdk-python@<commit> \\
        --sdk-artifact-sha256 <sha256 of the wheel that was installed>

The installation and provenance digests are computed here, with the same routines check_runtime
verifies against, so the pin cannot drift from the check. The pin is then verified before exiting.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import re
import sys

from .preflight import (
    _editable_distribution,
    _provenance_path,
    _stable_distribution_digest,
    check_runtime,
)
from .sdk_pin import SdkPin, host_pin_path, host_pin_payload


def build_host_pin(source: str, artifact_sha256: str) -> SdkPin:
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
        raise ValueError(f"--sdk-artifact-sha256 must be a lowercase sha256 digest, got {artifact_sha256!r}")
    distribution = importlib.metadata.distribution("yutori")
    if _editable_distribution(distribution):
        raise ValueError("refusing to pin an editable SDK installation; install a built wheel")
    provenance = _provenance_path(distribution, editable=False).read_bytes()
    return SdkPin(
        version=distribution.version,
        artifact_sha256=artifact_sha256,
        installation_sha256=_stable_distribution_digest(distribution),
        provenance_sha256=hashlib.sha256(provenance).hexdigest(),
        source=source,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m yutori_mcp.computer_use.host_pin", description=__doc__)
    parser.add_argument("--sdk-source", required=True, help="where the installed SDK was built from")
    parser.add_argument("--sdk-artifact-sha256", required=True, help="sha256 of the installed SDK wheel")
    args = parser.parse_args(argv)

    try:
        pin = build_host_pin(args.sdk_source, args.sdk_artifact_sha256)
    except (ValueError, OSError, importlib.metadata.PackageNotFoundError) as error:
        print(f"host pin not written: {error}", file=sys.stderr)
        return 1
    path = host_pin_path()
    path.write_text(host_pin_payload(pin))
    result = check_runtime()
    if not result.ok:
        path.unlink()
        print(
            f"host pin failed verification and was removed: {result.detail}",
            file=sys.stderr,
        )
        return 1
    print(f"host pin written to {path}: {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
