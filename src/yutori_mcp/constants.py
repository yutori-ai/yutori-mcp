"""Constants for Yutori authentication and configuration."""

import os

CLERK_INSTANCE_URL = "https://clerk.yutori.com"
CLERK_CLIENT_ID = os.environ.get("CLERK_CLIENT_ID", "TGiyfoPbG01Sakpe")
REDIRECT_PORT = 54320
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
AUTH_TIMEOUT_SECONDS = 300
CONFIG_DIR = ".yutori"
CONFIG_FILE = "config.json"
API_BASE_URL = os.environ.get("YUTORI_API_BASE_URL", "https://api.yutori.ai/v1")

ERROR_NO_API_KEY = "API key required. Run 'yutori login' or set YUTORI_API_KEY."
ERROR_AUTH_TIMEOUT = "Login timed out. Please try again."
ERROR_STATE_MISMATCH = "Security validation failed (state mismatch). Please try again."
ERROR_AUTH_FAILED = "Authentication failed"
