# Cleanup Backlog

Items identified for future cleanup runs. Pick one per run.

- [ ] `tests/test_adapter.py:109` — `mock_resolve` is assigned but never used (dead code)
- [ ] `src/yutori_mcp/formatters.py:245` — Asymmetric separator `--- Update #{i} —` uses `---` on left but em-dash `—` on right
- [ ] `src/yutori_mcp/server.py:195,214` — `arguments: dict` should be `arguments: dict[str, Any]` to match codebase convention
