"""Constants for Yutori authentication and configuration."""

import os

CLERK_INSTANCE_URL = os.environ.get("CLERK_INSTANCE_URL", "https://clerk.yutori.com")
CLERK_CLIENT_ID = os.environ.get("CLERK_CLIENT_ID", "TGiyfoPbG01Sakpe")
REDIRECT_PORT = 54320
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
AUTH_TIMEOUT_SECONDS = 300
CONFIG_DIR = ".yutori"
CONFIG_FILE = "config.json"
API_BASE_URL = os.environ.get("YUTORI_API_BASE_URL", "https://api.yutori.ai").rstrip("/")
API_VERSION_PATH = "/v1"


def build_api_url(path: str) -> str:
    base_url = API_BASE_URL
    if base_url.endswith(API_VERSION_PATH):
        return f"{base_url}{path}"
    return f"{base_url}{API_VERSION_PATH}{path}"

ERROR_NO_API_KEY = "API key required. Run 'yutori-mcp login' or set YUTORI_API_KEY."
ERROR_AUTH_TIMEOUT = "Login timed out. Please try again."
ERROR_STATE_MISMATCH = "Security validation failed (state mismatch). Please try again."
ERROR_AUTH_FAILED = "Authentication failed"
