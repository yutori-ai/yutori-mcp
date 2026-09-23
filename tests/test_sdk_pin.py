from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from yutori_mcp.computer_use import host_pin, preflight, sdk_pin
from yutori_mcp.computer_use.sdk_pin import (
    RELEASE_PIN,
    SdkPin,
    SdkPinError,
    host_pin_payload,
)

PROVENANCE = b"provenance"
ARTIFACT = "a" * 64


@pytest.fixture
def prefix(monkeypatch, tmp_path):
    root = tmp_path / "python"
    root.mkdir()
    monkeypatch.setattr(sdk_pin.sys, "prefix", str(root))
    return root


@pytest.fixture
def installed_sdk(monkeypatch, tmp_path):
    """A non-editable yutori distribution with one package file and the provenance asset."""
    site = tmp_path / "site-packages"
    package_file = Path("yutori/runtime.py")
    (site / package_file).parent.mkdir(parents=True)
    (site / package_file).write_text("from git")
    provenance = site / "yutori/navigator/macos/assets/provenance.json"
    provenance.parent.mkdir(parents=True)
    provenance.write_bytes(PROVENANCE)
    distribution = SimpleNamespace(
        version="0.9.99",
        files=[package_file],
        locate_file=lambda path: site / path,
        read_text=lambda _name: None,
    )
    monkeypatch.setattr(preflight.importlib.metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(host_pin.importlib.metadata, "distribution", lambda _: distribution)
    return site / package_file


def _write_pin(prefix: Path, **overrides) -> None:
    payload = json.loads(host_pin_payload(SdkPin("0.9.99", ARTIFACT, "b" * 64, "c" * 64, "git+https://x@abc")))
    payload.update(overrides)
    (prefix / sdk_pin.HOST_PIN_FILENAME).write_text(json.dumps(payload))


def test_release_pin_applies_without_a_host_pin(prefix):
    assert sdk_pin.sdk_pin() == RELEASE_PIN
    assert not RELEASE_PIN.host_pinned


def test_host_pin_replaces_the_release_pin(prefix):
    _write_pin(prefix)
    pin = sdk_pin.sdk_pin()
    assert pin.host_pinned
    assert (pin.version, pin.artifact_sha256, pin.source) == (
        "0.9.99",
        ARTIFACT,
        "git+https://x@abc",
    )


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"schema": 2}, "schema"),
        ({"sdk_source": ""}, "missing sdk_source"),
        ({"sdk_installation_sha256": "B" * 64}, "malformed sdk_installation_sha256"),
    ],
)
def test_untrustworthy_host_pin_fails_closed(prefix, overrides, message):
    _write_pin(prefix, **overrides)
    with pytest.raises(SdkPinError, match=message):
        sdk_pin.sdk_pin()
    result = preflight.check_runtime()
    assert not result.ok
    assert result.remediation == "Reinstall the application that bundles this runtime."


def test_non_json_host_pin_fails_closed(prefix):
    (prefix / sdk_pin.HOST_PIN_FILENAME).write_text("{")
    assert not preflight.check_runtime().ok


def test_writer_pins_the_installed_sdk_and_check_runtime_accepts_it(prefix, installed_sdk):
    assert host_pin.main(["--sdk-source", "git+https://x@abc", "--sdk-artifact-sha256", ARTIFACT]) == 0
    pin = sdk_pin.sdk_pin()
    assert pin.version == "0.9.99"
    assert pin.provenance_sha256 == hashlib.sha256(PROVENANCE).hexdigest()
    result = preflight.check_runtime()
    assert result.ok, result.detail
    assert "host-pinned from git+https://x@abc" in result.detail


def test_modified_sdk_fails_a_host_pinned_runtime(prefix, installed_sdk):
    assert host_pin.main(["--sdk-source", "git+https://x@abc", "--sdk-artifact-sha256", ARTIFACT]) == 0
    installed_sdk.write_text("modified after the build")
    result = preflight.check_runtime()
    assert not result.ok
    assert result.remediation == "Reinstall the application that bundles this runtime."


def test_writer_rejects_a_malformed_artifact_digest(prefix, installed_sdk):
    assert host_pin.main(["--sdk-source", "git+https://x@abc", "--sdk-artifact-sha256", "nope"]) == 1
    assert not (prefix / sdk_pin.HOST_PIN_FILENAME).exists()


def test_writer_refuses_an_editable_sdk(prefix, monkeypatch):
    editable = SimpleNamespace(
        version="0.9.99",
        read_text=lambda _name: json.dumps({"url": "file:///src", "dir_info": {"editable": True}}),
    )
    monkeypatch.setattr(host_pin.importlib.metadata, "distribution", lambda _: editable)
    assert host_pin.main(["--sdk-source", "-e /src", "--sdk-artifact-sha256", ARTIFACT]) == 1
    assert not (prefix / sdk_pin.HOST_PIN_FILENAME).exists()
