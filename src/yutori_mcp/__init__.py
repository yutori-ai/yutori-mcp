"""Yutori MCP Server - Web monitoring and browsing automation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yutori-mcp")
except PackageNotFoundError:
    # Running from a source checkout without the package installed
    # (e.g. `pytest` via pythonpath, or PYTHONPATH=src).
    __version__ = "0.0.0+unknown"
