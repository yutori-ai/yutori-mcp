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

In Claude Code, including Claude sessions hosted by Conductor, run the CLI through the Bash
tool with stdout attached. This makes `runner ready`, each `action #...` line, shell-command
previews, and the final result appear in the agent's live progress. Do not redirect the command
to a file or call the MCP `run_computer_use_task` tool from these sessions: Claude's headless
Agent SDK does not expose MCP progress or log notifications to its host.

For a foreground run:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --minutes 9 --max-steps 60
```

Run that as a foreground Bash call and set the Bash tool timeout slightly longer than the CLI's
`--minutes` limit (Claude's foreground Bash timeout is capped at 10 minutes). Pass the task as
one safely quoted argument and do not use `eval`.

For a longer run, start the same CLI command as a background Bash task without redirecting its
output, then wait on it with `TaskOutput` in blocking intervals. Relay meaningful new action
lines in progress updates between waits. The computer-use `--minutes` value remains the absolute
run deadline.

For an app-specific task:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --app Safari --start-url https://example.com --minutes 9
```

For a background window-delivery run (the CLI process itself can still be a foreground Bash
call):

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --app Notes --mode background --minutes 9
```

In another MCP host that visibly renders MCP progress notifications, prefer the MCP tool when it
is available:

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

If neither the CLI nor the MCP tool is available, ask the user to run the CLI task runner:

```bash
uvx yutori-mcp computer-use run "$ARGUMENTS" --minutes 30 --max-steps 60
```

To stop the active run from the Mac (background runs have no on-screen Stop button):

```bash
uvx yutori-mcp computer-use stop
```

## Safety

- In foreground mode, tell the user not to touch the Mac while the task runs; the same Show activity window is available from the menu bar item if they want to read what the agent is doing.
- In background mode, tell the user they can keep working but should leave the target app's window alone, and that only that window is captured. A menu bar item shows the latest frame, offers Show activity (a window with the run's conversation with the model: its thinking, every action, and every shell command), and offers Stop (also ⇧⌘Esc). Keyboard input may not reach a minimized window.
- Use `mode: "background"` only with `app`; use `allow_foreground_fallback` only with background mode, and warn that it may briefly flash the target window.
- Background keyboard delivery is app-dependent: some apps accept background clicks but not typed keys (Calculator, for one), and the agent then reports the refusal instead of typing. For typing-heavy background tasks, suggest `allow_foreground_fallback`.
- Use `app` for single-app tasks so the runner starts from the intended context.
- Use `start_url` only with `app`.
- Keep `minutes` between 1 and 60 and `max_steps` positive.
- For longer runs, compact or summarize older screenshots and tool results so the conversation stays within `max_context_len`.
- Report the final result and any setup blocker from the tool or CLI output.
