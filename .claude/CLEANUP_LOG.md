# Cleanup Log

| Date | Action | Details |
|------|--------|---------|
| 2026-04-03 | Changed | Removed unused `DEFAULT_LIMIT = 10` from `src/yutori_mcp/formatters.py` — dead code, never referenced anywhere in the codebase |
| 2026-04-05 | Changed | Deduplicated `validate_webhook_url` in `schemas.py` — extracted shared `_check_webhook_https()` function, replaced 4 identical copy-pasted validators. Also fixed misplaced `output_fields` field in `EditScoutInput`. |
