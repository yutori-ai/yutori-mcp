# Cleanup Backlog

Items identified for future cleanup runs. Pick one per run.

## Completed
- [x] `schemas.py` — Duplicate `validate_webhook_url` method copied identically in 4 classes; extracted to shared `_check_webhook_https` validator.
- [x] `schemas.py:119-127` — `output_fields` field defined after a `@field_validator`, breaking conventional Pydantic class ordering; moved before validators.
