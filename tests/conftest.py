"""Pytest configuration for yutori-mcp tests.

Import paths are configured in pyproject.toml via
``tool.pytest.ini_options.pythonpath = ["src"]``, so the suite runs from a
source checkout without installing the package.
"""


def _scout_list_response(scouts=None, total=0, summary=None, **overrides):
    """Build a ``list_scouts``-shaped response payload for tests.

    Shared by test_formatters.py (formatting a real payload) and test_server.py
    (mocking the adapter's return value), which had each independently written
    out the same ``{"scouts": [...], "total": ..., "summary": {...}}`` shape.
    """
    if summary is None:
        summary = {"active": 0, "paused": 0, "done": 0}
    response = {
        "scouts": scouts if scouts is not None else [],
        "total": total,
        "summary": summary,
    }
    response.update(overrides)
    return response
