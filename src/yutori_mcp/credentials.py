"""Per-environment API credentials, layered over the SDK's single-key store.

The SDK keeps one ``api_key`` in ``~/.yutori/config.json`` and its login flow always targets
production, so there was no supported way to hold a dev credential — every path was an
environment-variable workaround, and offering a production key to the dev stack fails as an
opaque 401 that reads like a missing entitlement.

This adds an ``environments`` map beside the existing key:

    {"api_key": "yt_prod...", "environments": {"dev": {"api_key": "yt_dev..."}}}

Nothing here writes to the SDK's own field, and a config with no ``environments`` key behaves
exactly as it does today, so existing installs are untouched.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from yutori.auth.credentials import get_config_path, resolve_api_key

ENVIRONMENTS_FIELD = "environments"
API_KEY_FIELD = "api_key"


def _load_config() -> dict[str, Any]:
    """The config file as a dict, or empty if absent, unreadable or malformed.

    Deliberately forgiving: a corrupt config should degrade to "no stored credential" and let
    the caller's remediation speak, not raise out of a preflight check.
    """
    try:
        data = json.loads(get_config_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def stored_environment_key(environment: str) -> str | None:
    """The key saved for ``environment``, or None if there isn't one."""
    environments = _load_config().get(ENVIRONMENTS_FIELD)
    if not isinstance(environments, dict):
        return None
    entry = environments.get(environment)
    if not isinstance(entry, dict):
        return None
    key = entry.get(API_KEY_FIELD)
    return key if isinstance(key, str) and key.strip() else None


def resolve_api_key_for_environment(environment: str) -> str | None:
    """Resolve the key to use for ``environment``.

    Order: ``YUTORI_API_KEY``, then the stored per-environment key, then the SDK's own chain.
    The environment variable stays first so an explicit override still wins, matching the
    precedence the SDK documents; the fallback is what keeps single-environment installs working
    unchanged.
    """
    override = os.environ.get("YUTORI_API_KEY")
    if override and override.strip():
        return override
    return stored_environment_key(environment) or resolve_api_key()


def _write_config_atomic(config_path: Path, config: dict[str, Any]) -> None:
    """Write ``config`` to ``config_path`` atomically via a same-directory temp file.

    A crash mid-write cannot leave a half-written config: the temp file is written in full and
    only then swapped in with ``os.replace``. The file is created 0600 before any secret reaches
    it, rather than chmod'ed afterwards.
    """
    handle, temporary = tempfile.mkstemp(dir=config_path.parent, prefix=".config-")
    try:
        os.fchmod(handle, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(handle, "w") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, config_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def save_environment_key(environment: str, api_key: str) -> Path:
    """Store ``api_key`` for ``environment`` and return the config path.

    Merges rather than replaces: the SDK's top-level key and any other environment's entry
    survive, which is the whole point of holding prod and dev side by side.
    """
    if not api_key.strip():
        raise ValueError("Refusing to store an empty API key")
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(config_path.parent, stat.S_IRWXU)

    config = _load_config()
    environments = config.get(ENVIRONMENTS_FIELD)
    if not isinstance(environments, dict):
        environments = {}
    environments[environment] = {API_KEY_FIELD: api_key.strip()}
    config[ENVIRONMENTS_FIELD] = environments

    _write_config_atomic(config_path, config)
    return config_path


def clear_environment_key(environment: str) -> bool:
    """Forget the key stored for ``environment``. True if one was removed."""
    config = _load_config()
    environments = config.get(ENVIRONMENTS_FIELD)
    if not isinstance(environments, dict) or environment not in environments:
        return False
    del environments[environment]
    if environments:
        config[ENVIRONMENTS_FIELD] = environments
    else:
        config.pop(ENVIRONMENTS_FIELD, None)

    _write_config_atomic(get_config_path(), config)
    return True


def mask(api_key: str) -> str:
    """A key rendered safe to print: last four characters only."""
    return f"…{api_key[-4:]}" if len(api_key) > 4 else "…"
