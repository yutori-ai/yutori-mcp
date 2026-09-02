---
name: yutori-computer-use
description: Run local Mac desktop tasks with Yutori computer use. Use when the user wants to operate macOS apps, websites in a local browser, or cross-app workflows on the visible desktop, or to drive one app's window in the background while they keep working.
argument-hint: "[desktop task]"
---

# Computer Use

Run tasks on the user's visible Mac desktop with Yutori computer use, or drive a single app's
window in the background while the user keeps working.

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

When the user asks to run a task "in the background" or "while I keep working", use background
mode (requires `app`; the model sees and drives only that app's window and never takes focus).
Infer `app` from the task ("in Notes", "in Safari"); if no single app is named, ask which app to
target before calling the tool. "In the foreground" or no mention means the default foreground run:

```json
{
  "task": "$ARGUMENTS",
  "app": "Notes",
  "mode": "background"
}
```

Add `"allow_foreground_fallback": true` only if the user accepts the target window briefly
flashing to the front when an action cannot be delivered in the background.

If the MCP tool is unavailable, ask the user to run the CLI task runner:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --minutes 30 --max-steps 60
```

For an app-specific task:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --app Safari --start-url https://example.com
```

For a background run:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --app Notes --mode background
```

To stop the active run from the Mac (background runs have no on-screen Stop button):

```bash
uvx yutori-mcp computer-use stop
```

## Safety

- In foreground mode, tell the user not to touch the Mac while the task runs.
- In background mode, tell the user they can keep working but should leave the target app's window alone, and that only that window is captured. A menu bar item shows the latest frame and offers Stop (also ⇧⌘Esc). Keyboard input may not reach a minimized window.
- Use `mode: "background"` only with `app`; use `allow_foreground_fallback` only with background mode, and warn that it may briefly flash the target window.
- Background keyboard delivery is app-dependent: some apps accept background clicks but not typed keys (Calculator, for one), and the agent then reports the refusal instead of typing. For typing-heavy background tasks, suggest `allow_foreground_fallback`.
- Use `app` for single-app tasks so the runner starts from the intended context.
- Use `start_url` only with `app`.
- Keep `minutes` between 1 and 60 and `max_steps` positive.
- For longer runs, compact or summarize older screenshots and tool results so the conversation stays within `max_context_len`.
- Report the final result and any setup blocker from the tool or CLI output.
