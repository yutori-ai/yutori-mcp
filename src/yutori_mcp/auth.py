"""OAuth 2.0 + PKCE authentication flow for Yutori CLI."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .constants import (
    API_BASE_URL,
    AUTH_TIMEOUT_SECONDS,
    CLERK_CLIENT_ID,
    CLERK_INSTANCE_URL,
    CONFIG_DIR,
    CONFIG_FILE,
    ERROR_AUTH_FAILED,
    ERROR_AUTH_TIMEOUT,
    ERROR_STATE_MISMATCH,
    REDIRECT_PORT,
    REDIRECT_URI,
)


def _get_config_path() -> Path:
    return Path.home() / CONFIG_DIR / CONFIG_FILE


def generate_pkce() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_auth_url(code_challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLERK_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "openid profile email",
    }
    return f"{CLERK_INSTANCE_URL}/oauth/authorize?{urlencode(params)}"


class AuthResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.received = threading.Event()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    auth_result: AuthResult

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)

        if "error" in params:
            self.auth_result.error = params.get("error_description", params["error"])[0]
        else:
            self.auth_result.code = params.get("code", [None])[0]
            self.auth_result.state = params.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        if self.auth_result.error:
            body = f"<html><body><h1>Login Failed</h1><p>{self.auth_result.error}</p></body></html>"
        else:
            body = "<html><body><h1>Login Successful</h1><p>You can close this window and return to the terminal.</p></body></html>"

        self.wfile.write(body.encode())
        self.auth_result.received.set()


def wait_for_callback(timeout: int = AUTH_TIMEOUT_SECONDS) -> AuthResult:
    auth_result = AuthResult()
    CallbackHandler.auth_result = auth_result

    class ReusableServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableServer(("127.0.0.1", REDIRECT_PORT), CallbackHandler) as server:
        server.timeout = timeout
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        auth_result.received.wait(timeout=timeout)
        thread.join(timeout=1)

    return auth_result


def exchange_code_for_token(code: str, code_verifier: str) -> str:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{CLERK_INSTANCE_URL}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLERK_CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


def generate_api_key(jwt: str) -> str:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{API_BASE_URL}/generate_key",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        response.raise_for_status()
        return response.json()["key"]


def save_config(api_key: str) -> None:
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"api_key": api_key}, indent=2))
    os.chmod(config_path, 0o600)


def load_config() -> dict[str, Any] | None:
    config_path = _get_config_path()
    if not config_path.exists():
        return None
    try:
        return json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def clear_config() -> None:
    config_path = _get_config_path()
    if config_path.exists():
        config_path.unlink()


def run_login_flow() -> bool:
    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    auth_url = build_auth_url(code_challenge, state)

    print("Opening browser for login...")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")

    webbrowser.open(auth_url)

    print("Waiting for authentication...")
    auth_result = wait_for_callback()

    if not auth_result.received.is_set():
        print(f"Error: {ERROR_AUTH_TIMEOUT}", file=sys.stderr)
        return False

    if auth_result.error:
        print(f"Error: {auth_result.error}", file=sys.stderr)
        return False

    if auth_result.state != state:
        print(f"Error: {ERROR_STATE_MISMATCH}", file=sys.stderr)
        return False

    if not auth_result.code:
        print(f"Error: {ERROR_AUTH_FAILED}", file=sys.stderr)
        return False

    try:
        print("Exchanging authorization code...")
        jwt = exchange_code_for_token(auth_result.code, code_verifier)

        print("Generating API key...")
        api_key = generate_api_key(jwt)

        save_config(api_key)
        print(f"\nSuccess! API key saved to {_get_config_path()}")
        return True

    except httpx.HTTPStatusError as e:
        print(f"Error: {ERROR_AUTH_FAILED} ({e.response.status_code})", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


def show_status() -> None:
    config = load_config()
    if config and config.get("api_key"):
        api_key = config["api_key"]
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"Logged in (API key: {masked})")
        print(f"Config: {_get_config_path()}")
    else:
        env_key = os.environ.get("YUTORI_API_KEY")
        if env_key:
            masked = env_key[:6] + "..." + env_key[-4:] if len(env_key) > 10 else "***"
            print(f"Using YUTORI_API_KEY environment variable ({masked})")
        else:
            print("Not logged in. Run 'yutori login' to authenticate.")
