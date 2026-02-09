"""Yutori MCP Server - Web monitoring and browsing automation."""

from .adapter import MCPClientAdapter, YutoriAPIError
from .server import create_server, main, run_server

__all__ = [
    "MCPClientAdapter",
    "YutoriAPIError",
    "create_server",
    "run_server",
    "main",
]

__version__ = "0.2.0"
