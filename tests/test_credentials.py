"""Per-environment credential storage.

The SDK holds one api_key and its login flow is production-only, so a prod key offered to dev
fails as a 401 that reads like a missing entitlement. These cover the layer that fixes that,
and in particular that a config with no `environments` key behaves exactly as before.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from unittest.mock import patch

import pytest

from yutori_mcp import credentials


@pytest.fixture
def config(tmp_path):
    path = tmp_path / ".yutori" / "config.json"
    path.parent.mkdir(parents=True)
    with patch.object(credentials, "get_config_path", lambda: path):
        yield path


def test_stores_a_key_per_environment_without_touching_the_sdk_field(config):
    config.write_text(json.dumps({"api_key": "yt-prod"}))
    credentials.save_environment_key("dev", "yt-dev")
    data = json.loads(config.read_text())
    # The SDK's own key must survive: holding prod and dev side by side is the point.
    assert data["api_key"] == "yt-prod"
    assert data["environments"]["dev"]["api_key"] == "yt-dev"


def test_a_config_without_environments_behaves_exactly_as_before(config):
    config.write_text(json.dumps({"api_key": "yt-prod"}))
    with patch.object(credentials, "resolve_api_key", return_value="yt-prod"):
        with patch.dict(os.environ, {}, clear=True):
            assert credentials.resolve_api_key_for_environment("dev") == "yt-prod"
    assert credentials.stored_environment_key("dev") is None


def test_environment_key_beats_the_sdk_fallback(config):
    config.write_text(
        json.dumps(
            {"api_key": "yt-prod", "environments": {"dev": {"api_key": "yt-dev"}}}
        )
    )
    with patch.object(credentials, "resolve_api_key", return_value="yt-prod"):
        with patch.dict(os.environ, {}, clear=True):
            assert credentials.resolve_api_key_for_environment("dev") == "yt-dev"
            assert credentials.resolve_api_key_for_environment("prod") == "yt-prod"


def test_the_environment_variable_still_wins(config):
    config.write_text(json.dumps({"environments": {"dev": {"api_key": "yt-dev"}}}))
    with patch.dict(os.environ, {"YUTORI_API_KEY": "yt-override"}, clear=True):
        assert credentials.resolve_api_key_for_environment("dev") == "yt-override"


def test_saving_one_environment_leaves_the_other_alone(config):
    credentials.save_environment_key("dev", "yt-dev")
    credentials.save_environment_key("staging", "yt-staging")
    data = json.loads(config.read_text())
    assert data["environments"]["dev"]["api_key"] == "yt-dev"
    assert data["environments"]["staging"]["api_key"] == "yt-staging"


def test_the_config_is_owner_only(config):
    credentials.save_environment_key("dev", "yt-dev")
    # Created 0600 before any secret is written, rather than chmod'ed afterwards.
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_a_corrupt_config_degrades_to_no_stored_credential(config):
    config.write_text("{not json")
    assert credentials.stored_environment_key("dev") is None
    with patch.object(credentials, "resolve_api_key", return_value=None):
        with patch.dict(os.environ, {}, clear=True):
            assert credentials.resolve_api_key_for_environment("dev") is None


def test_clearing_removes_only_that_environment(config):
    credentials.save_environment_key("dev", "yt-dev")
    credentials.save_environment_key("staging", "yt-staging")
    assert credentials.clear_environment_key("dev") is True
    data = json.loads(config.read_text())
    assert "dev" not in data["environments"]
    assert data["environments"]["staging"]["api_key"] == "yt-staging"
    assert credentials.clear_environment_key("dev") is False


def test_an_empty_key_is_refused(config):
    with pytest.raises(ValueError):
        credentials.save_environment_key("dev", "   ")


def test_mask_never_reveals_more_than_the_last_four(config):
    assert credentials.mask("yt_supersecretvalue") == "…alue"
    assert credentials.mask("ab") == "…"


def test_ambient_yutori_env_does_not_change_what_login_means(
    config, monkeypatch, capsys
):
    """A shell-exported YUTORI_ENV must not turn plain `login` into the dev paste path.

    The README tells people to export that variable. Letting it reach the auth dispatch meant a
    plain `login` silently skipped the production browser flow, and `logout` cleared a dev entry
    instead of the real credential.
    """
    import yutori_mcp.server as server

    monkeypatch.setenv("YUTORI_ENV", "dev")
    seen: dict[str, object] = {}

    def record(command, environment=None):
        seen["command"] = command
        seen["environment"] = environment
        raise SystemExit(0)

    monkeypatch.setattr(server, "_handle_auth_command", record)
    monkeypatch.setattr(sys, "argv", ["yutori-mcp", "login"])
    with pytest.raises(SystemExit):
        server.main()
    # None, not "dev": only an explicit --env selects a non-default environment.
    assert seen == {"command": "login", "environment": None}


def test_an_explicit_env_flag_still_selects_the_environment(monkeypatch):
    import yutori_mcp.server as server

    seen: dict[str, object] = {}

    def record(command, environment=None):
        seen["environment"] = environment
        raise SystemExit(0)

    monkeypatch.setattr(server, "_handle_auth_command", record)
    monkeypatch.setattr(sys, "argv", ["yutori-mcp", "--env", "dev", "login"])
    with pytest.raises(SystemExit):
        server.main()
    assert seen["environment"] == "dev"


def test_the_adapter_resolves_the_key_for_the_targeted_environment(config, monkeypatch):
    """Non-computer-use tools were served the SDK's single key.

    After `--env dev login`, browsing/research/scout calls sent the production key at the dev API
    because MCPClientAdapter never consulted the environments map.
    """
    from yutori_mcp import adapter

    config.write_text(
        json.dumps(
            {"api_key": "yt-prod", "environments": {"dev": {"api_key": "yt-dev"}}}
        )
    )
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)
    monkeypatch.setenv("YUTORI_ENV", "dev")
    assert adapter.current_environment() == "dev"
    # The `config` fixture already redirects credentials.get_config_path, which is what the
    # adapter's resolver reads through.
    assert (
        adapter.resolve_api_key_for_environment(adapter.current_environment())
        == "yt-dev"
    )


@pytest.mark.parametrize(
    "environment,expected_command",
    [
        ("prod", "uvx yutori-mcp login"),
        ("dev", "uvx yutori-mcp --env dev login"),
    ],
)
def test_the_missing_key_remediation_points_at_the_selected_login(
    monkeypatch, environment, expected_command
):
    from yutori_mcp.computer_use import preflight

    monkeypatch.setenv("YUTORI_ENV", environment)
    with patch("yutori_mcp.credentials.resolve_api_key_for_environment", return_value=None):
        result = preflight.check_api_key()
    assert not result.ok
    assert expected_command in (result.remediation or "")
