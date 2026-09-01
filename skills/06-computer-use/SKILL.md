---
name: yutori-computer-use
description: Run local Mac desktop tasks with Yutori computer use. Use when the user wants to operate macOS apps, websites in a local browser, or cross-app workflows on the visible desktop.
argument-hint: "[desktop task]"
---

# Computer Use

Run tasks on the user's visible Mac desktop with Yutori computer use.

## Setup

If the user has not set up computer use yet, ask them to run these commands in a real terminal:

```bash
uvx yutori-mcp login
uvx yutori-mcp computer-use setup
```

`setup` ends with the readiness checks and reports anything still missing. To re-check later,
or when a run fails, use `uvx yutori-mcp computer-use doctor`.

Use the smoke test when validating a fresh install:

```bash
uvx yutori-mcp computer-use smoke
```

Requirements: macOS 15+, Python 3.10+, `uvx`, a Yutori API key with computer-use access,
`CuaDriver.app` / `cua-driver==0.23.2`, and Screen Recording plus Accessibility permissions.
The optional native reasoning overlay may require Xcode Command Line Tools.

## Run a Task

Prefer the MCP tool when it is available:

```json
{
  "task": "$ARGUMENTS",
  "minutes": 30,
  "max_steps": 60
}
```

Use optional fields when helpful:

```json
{
  "task": "$ARGUMENTS",
  "app": "Safari",
  "start_url": "https://example.com",
  "minutes": 5,
  "max_steps": 80
}
```

If the MCP tool is unavailable, ask the user to run the CLI task runner:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --minutes 30 --max-steps 60
```

For an app-specific task:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --app Safari --start-url https://example.com
```

## Safety

- Tell the user not to touch the Mac while the task runs.
- Use `app` for single-app tasks so the runner starts from the intended context.
- Use `start_url` only with `app`.
- Keep `minutes` between 1 and 60 and `max_steps` positive.
- For longer runs, compact or summarize older screenshots and tool results so the conversation stays within `max_context_len`.
- Report the final result and any setup blocker from the tool or CLI output.
