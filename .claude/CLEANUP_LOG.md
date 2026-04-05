# Cleanup Log

| Date | Action | Details |
|------|--------|---------|
| 2026-04-03 | Changed | Removed unused `DEFAULT_LIMIT = 10` from `src/yutori_mcp/formatters.py` — dead code, never referenced anywhere in the codebase |
| 2026-04-05 | Changed | Extracted duplicate `validate_webhook_url` into shared `WebhookUrl` annotated type in `schemas.py`; also fixed misplaced `output_fields` in `EditScoutInput` |
