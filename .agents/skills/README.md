# Agent skills (Codex / OpenAI)

Each `yutori-*/` directory contains agent metadata (e.g. `agents/openai.yaml`) and a **symlink** for the skill content:

- **`SKILL.md`** in each directory is a symlink to the canonical file under `../../skills/` (repo root `skills/`).
- **Edit skill content only in `skills/`** (e.g. `skills/01-scout/SKILL.md`). The `.agents/skills/yutori-*/SKILL.md` symlinks point to those files so both Codex and the Claude Desktop packaging script use the same source.

| This directory              | Canonical source              |
|----------------------------|-------------------------------|
| `yutori-scout/SKILL.md`     | `skills/01-scout/SKILL.md`     |
| `yutori-research/SKILL.md`  | `skills/02-research/SKILL.md`  |
| `yutori-browse/SKILL.md`    | `skills/03-browse/SKILL.md`   |
| `yutori-competitor-watch/SKILL.md` | `skills/04-competitor-watch/SKILL.md` |
| `yutori-api-monitor/SKILL.md` | `skills/05-api-monitor/SKILL.md`   |

Do not replace the symlinks with copies; that would bring back duplicate content and drift.
