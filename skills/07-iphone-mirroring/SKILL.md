---
name: yutori-iphone-mirroring
description: Experimentally control a nearby iPhone through Apple's iPhone Mirroring app and Yutori computer use. Use only for low-risk iPhone Mirroring experiments from a Mac, not production or remote iPhone automation.
---

# iPhone Mirroring (Experimental)

> This capability is purely experimental. Reliability is not guaranteed, and background
> typing, scrolling, app switching, and reconnection may fail. Do not present it as a supported
> production workflow.

Control an iPhone through Apple's iPhone Mirroring window on the Mac. The iPhone UI is a
visual surface inside the Mac app: expect pixel actions rather than semantic accessibility
elements.

## Requirements

- Yutori computer use is ready (`uvx yutori-mcp computer-use doctor`).
- iPhone Mirroring is already paired and authorized.
- The Mac and iPhone meet Apple's Continuity requirements; keep the iPhone nearby and locked.
  Unlocking or using the iPhone directly ends mirroring.
- The user understands that content visible in the mirrored iPhone window is sent to Yutori
  for the run. Ask them to close or remove sensitive widgets before a benchmark.

If setup or authentication is required, stop and ask the user to complete it. Never request
their Mac/iPhone password, passcode, passkey, biometric approval, or verification code.

## Run an iPhone task

Always use window-scoped background capture so the agent sees and drives only iPhone
Mirroring. Never enable foreground fallback for this skill: if an action cannot be delivered
in the background, stop and report the blocked action instead of taking over the Mac desktop.

```json
{
  "task": "You are controlling an iPhone inside Apple iPhone Mirroring. Treat clicks as taps and scrolling or dragging as iPhone swipes. Use Command-3 to open iPhone Spotlight, type an exact app name, and press Return instead of searching across Home screens. Command-1 opens iPhone Home and Command-2 opens the iPhone App Switcher. Do not use the macOS Dock, menus, other apps, or shell commands. Take a fresh screenshot after each visual change. TASK: $ARGUMENTS",
  "app": "iPhone Mirroring",
  "mode": "background",
  "allow_local_shell": false,
  "minutes": 10,
  "max_steps": 100
}
```

Tell the user they may keep using other Mac apps, but must leave iPhone Mirroring and the
iPhone alone.

If the MCP tool is unavailable, use the equivalent CLI invocation and preserve the prompt and
arguments above:

```bash
uvx yutori-mcp computer-use run "<phone-specific prompt and task>" \
  --app "iPhone Mirroring" --mode background --no-local-shell \
  --minutes 10 --max-steps 100
```

Do not switch to foreground mode inside this skill, including for benchmarks. Use the general
computer-use workflow instead if the user explicitly requests a separate foreground-desktop
experiment.

## Navigation and recovery

- Prefer `Command-3`, exact app name, `Return` to launch an app. Avoid visually scanning Home.
- Use `Command-1` for Home and `Command-2` for App Switcher.
- For long pages, use small `scroll` actions and inspect a fresh frame after each one. Do not
  use Page Up, Page Down, or drag as substitutes for an iPhone swipe in background mode.
- If the window shows Resume, click Resume once and take a fresh screenshot. If it asks for
  authentication, stop for the user.
- iPhone Mirroring can pause after inactivity. Treat Resume as a recoverable connection state,
  not an app-task failure.
- Do not repeat an action reported as uncertain. Observe the window and decide from the new
  frame whether it landed.
- Stop after three failed attempts to reach the same UI state and report the blocker.

## Safety and privacy

- Open only apps and data needed for the user's stated task. Home widgets, notification badges,
  notifications, and app switcher previews can reveal private information.
- Do not open Messages, Mail, Photos, Health, password managers, authenticators, banking,
  payments, or the notification shade unless the user explicitly requested that exact app or
  data in the current task.
- Do not approve purchases, money movement, account changes, app installation, permissions,
  security settings, or destructive actions without the user's explicit authorization.
- Stop at sign-in, passcode, passkey, biometric, one-time-code, or other reauthentication UI
  and ask the user to complete it directly.
- Do not use shell commands to inspect iPhone data, backups, containers, or credentials.
- Report the final state, delivery mode, foreground escalations, uncertain actions, elapsed
  time, and whether the task completed.

## Benchmark mode

For a low-risk smoke test, use Calculator and launch it through Spotlight:

```text
Open iPhone Spotlight with Command-3, search for Calculator, and press Return. Clear the
calculator, compute 6*7, verify that 42 is visible, and stop. Do not open any other app.
```

Run one bounded trial at a time in strict background mode. Record task success, model turns,
action outcomes, background refusals, elapsed time, and the final visible state.
