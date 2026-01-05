"""Yutori MCP Server - Web monitoring and browsing automation."""

from .client import YutoriAPIError, YutoriClient
from .server import create_server, main, run_server

__all__ = [
    "YutoriClient",
    "YutoriAPIError",
    "create_server",
    "run_server",
    "main",
]

__version__ = "0.1.1"
