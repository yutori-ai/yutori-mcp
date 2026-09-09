# Plan 001: Benchmark iPhone Mirroring, then prove native mobile computer use on Android

> **Executor instructions**: Treat this as a cross-repository program plan,
> not permission to improvise inside this repository. Follow the gates in
> order. The first experiment uses iPhone Mirroring with the current Mac stack;
> the first native shippable target is a private Android alpha. Run every
> verification command and confirm the expected result before advancing. If a
> STOP condition occurs, stop and report it. When done, update this plan's row
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat cf6e91c..HEAD -- src/yutori_mcp/server.py src/yutori_mcp/schemas.py src/yutori_mcp/computer_use tests/test_computer_use.py README.md TOOLS.md skills/06-computer-use/SKILL.md`
> If any listed path changed, re-check the current-state references and adapt
> the MCP integration without weakening the protocol or safety boundaries.

## Status

- **Priority**: P1
- **Effort**: L — 2–3 days for the iPhone Mirroring benchmark, then approximately
  6–8 engineer-weeks to a private Android alpha; direct iOS and store
  distribution are separate follow-ups
- **Risk**: HIGH — this grants an agent broad control over a personal device
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `cf6e91c`, 2026-09-04

## Outcome and product decision

Immediately benchmark iPhone tasks through Apple's iPhone Mirroring app and
the existing macOS CUA stack. This route requires no phone-side code and tests
the most important unknown first: whether n2 can reliably understand and act
on real mobile UIs. Treat it as a valuable local mode — especially if strict
background window delivery works — but not as the final phone driver because
the iPhone must remain near a paired Mac and signed into the same Apple Account.

Build an Android-first mobile computer-use driver that lets the existing n2
agent see and operate one explicitly paired, unlocked physical phone. The
first alpha is privately distributed and requires the user to start every run
on the phone. It does not target Google Play because current Play policy
prohibits non-accessibility tools from using AccessibilityService for an app
that autonomously initiates, plans, and executes actions.

Run direct iOS/XCTest as a separate feasibility track after the Android protocol works. The
supported route for broad iPhone UI automation is XCTest/XCUIAutomation via a
Mac-hosted test runner such as WebDriverAgent. It requires Xcode, device trust,
Developer Mode, UI Automation, signing, and provisioning. Do not promise a
standalone App Store iPhone driver unless Apple exposes a new supported API or
grants a relevant entitlement.

## Why this matters

The current product can operate the user's Mac, but the highest-value personal
workflows increasingly cross into mobile-only apps, authenticated sessions,
cameras, messages, and device settings. A phone-side driver turns mobile from
a disconnected endpoint into another n2 execution surface. The key is to
preserve the existing system's strong lifecycle properties — bounded sessions,
observable actions, uncertain-action handling, hard Stop, and pinned contracts
— while acknowledging that Android and iOS expose fundamentally different
automation authority.

## Current state

This repository owns the MCP-facing orchestration around an SDK-owned agent and
an external driver; it does not implement the macOS driver itself.

- `src/yutori_mcp/server.py:624-654` — validates the computer-use request,
  holds the machine lock across preflight and execution, and streams progress.
- `src/yutori_mcp/server.py:695-721` — exposes computer use only when the MCP
  process itself is running on macOS. A paired phone must not inherit this host
  OS gate.
- `src/yutori_mcp/schemas.py:406-455` — the public request is desktop-shaped:
  `app`, `start_url`, `foreground/background`, and foreground fallback.
- `src/yutori_mcp/computer_use/supervisor.py:276-390` — the parent/child JSONL
  protocol validates event order and shape, keeps action history, enforces an
  absolute deadline, and kills the runner process group on timeout/cancel.
- `src/yutori_mcp/computer_use/runner.py:603-651` — constructs a macOS-specific
  computer but passes it to the generic `N2ComputerAgent`.
- `src/yutori_mcp/computer_use/runner.py:764-875` — one computer lifecycle,
  one n2 agent lifecycle, action callbacks, cancellation, and final telemetry.
- `src/yutori_mcp/computer_use/constants.py:7-35` — pins protocol, tool-set,
  SDK, and driver versions so releases cannot silently change behavior.
- Installed SDK `yutori.navigator.n2.N2Computer` — already defines a structural
  adapter interface (`screenshot`, click, drag, scroll, type, keypress, wait).
  This is the main reuse seam; do not fork the agent loop.
- `tests/test_computer_use.py:286-325` — reusable ready/result event fixtures
  and supervisor harness.
- `tests/test_computer_use.py:762-799` — verifies that the MCP runner delegates
  to the SDK-owned runtime and preserves the run link.
- `README.md:338-371` and `TOOLS.md:7-77` — document the current macOS-only,
  one-run-at-a-time contract.

Current local `cua-driver 0.23.2` demonstrates useful protocol semantics to
carry forward: explicit sessions, capability manifests, revoke, screenshot and
UI-tree observations, action outcome/refusal envelopes, and no automatic retry
when a mutating action loses its acknowledgement.

### Local iPhone Mirroring probe on 2026-09-04

- `/System/Applications/iPhone Mirroring.app` is installed.
- CUA reports the running app as bundle ID `com.apple.ScreenContinuity` and can
  resolve its visible window (`477x1037` points at probe time).
- `get_window_state` returned a valid `721x1567` screenshot for that exact
  window, so the existing capture path can observe the mirrored phone without
  full-desktop screenshots.
- The AX tree exposes the macOS window and a `Resume` button, but not semantic
  elements inside the mirrored iPhone display. Expect iPhone actions to be
  vision/pixel-first.
- CUA advertises accessibility, exact-window pointer, and PID-keyboard routes
  for the window. This is capability discovery, not proof that background taps,
  swipes, and typing land correctly; Step 0 measures that.

## Target architecture

```text
MCP / CLI
   │ starts task, streams progress, stops task
   ▼
n2 runner (local for prototype; Yutori-hosted later)
   │ generic N2Computer calls
   ▼
MobileComputer adapter (Python SDK)
   │ versioned WSS protocol; short-lived session capability
   ▼
Device relay (outbound connections only; no public phone port)
   │
   ├── Android companion
   │     AccessibilityService: screenshot/tree/gesture/text/global actions
   │
   └── iOS Mac bridge (later)
         XCTest/WebDriverAgent -> provisioned iPhone or Simulator
```

The immediate iPhone Mirroring path is shorter:

```text
existing run_computer_use_task -> MacOSComputer -> CuaDriver
    -> exact iPhone Mirroring window -> nearby locked iPhone
```

For the lab prototype, the adapter may connect to Android over ADB port
forwarding or a trusted LAN socket. The production design must use an outbound
phone-to-relay connection so it works behind NAT and does not expose a listening
port on the phone.

## Capability tiers

| Target | What is feasible | Distribution | Recommendation |
|---|---|---|---|
| iPhone through iPhone Mirroring | Pixel screenshot plus Mac click/swipe/scroll/keyboard forwarding; phone stays locked | Built into macOS 15+, requires nearby paired devices on the same Apple Account | Benchmark immediately; keep as local mode if reliable |
| Physical Android | Screenshot, accessibility tree, tap/swipe, text, Back/Home/Recents, app launch while unlocked | Private APK, enterprise, or carefully reviewed distribution | Build first |
| Android emulator | Same core contract with easier reset and deterministic fixtures | CI/lab | Use for conformance and regression tests |
| iOS Simulator | Broad XCTest UI automation | Mac/Xcode lab | Second platform prototype |
| Provisioned iPhone | Broad XCTest UI automation through a Mac-hosted signed test runner | Developer/internal only; setup-heavy | Time-boxed feasibility track |
| Normal App Store iPhone app | App-owned UI, App Intents/Shortcuts, screen broadcast; not general touch injection into other apps | App Store | Offer later as an assisted/narrow capability, not “full CUA” |

## Protocol contract

Define `mobile-driver-protocol-v1` before coupling any implementation to a
particular Android or iOS library. Start with JSON over WebSocket for
inspectability. Every message has `protocol_version`, `session_id`, `message_id`,
`sent_at`, and a bounded `deadline_ms`.

Required message families:

1. `device.hello`: opaque device ID, platform, OS/app versions, screen state,
   and a capabilities object. Never include advertising ID, phone number, or
   account identifiers.
2. `session.start`: expiry, allowed package/bundle IDs, allowed action classes,
   and a human-readable task summary shown on the phone.
3. `observe`: `frame_id`, image media type/data, native width/height,
   orientation, safe-area/system-bar insets, current package/bundle ID,
   optional bounded accessibility tree, and monotonic capture time.
4. `action`: `action_id`, `based_on_frame_id`, one of the allowed actions, and
   validated arguments. Initial actions are `tap`, `double_tap`, `long_press`,
   `swipe`, `set_text`, `back`, `home`, `recents`, `launch_app`, `open_url`,
   `wait`, and `screenshot`.
5. `action.result`: exactly one of `executed`, `refused`, `uncertain`, or
   `failed`, plus machine-readable reason, route (`node`, `gesture`, `system`),
   timing, and optional post-action `frame_id`.
6. `session.end`, `session.revoke`, and heartbeat messages.

Protocol invariants:

- Coordinates are integers in the exact screenshot pixel space. The adapter
  scales n2's normalized 0–1000 coordinates once and accounts for orientation
  and insets exactly once.
- A coordinate action against a non-current `frame_id` is refused as
  `stale_frame`; the model receives a fresh observation.
- A mutating action with a lost acknowledgement returns `uncertain` and is not
  automatically retried. Read-only observation may reconnect and retry.
- Duplicate `action_id` values are idempotently answered from a short result
  cache; they are never executed twice.
- Payload and tree-size limits are explicit. Start with a 1920-pixel maximum
  long side, WebP/JPEG frames, a 5 MiB frame ceiling, and a 2,000-node tree.
- Unknown fields and unknown actions fail closed. Major protocol mismatch
  blocks the run with actionable upgrade guidance.

## Commands you will need

Commands differ by repository. Record the actual commands in each repository's
README before declaring that component complete.

| Purpose | Command | Expected on success |
|---|---|---|
| Install this repo | `python -m pip install -e ".[dev]"` | exit 0 |
| Test this repo | `pytest -q` | exit 0, all tests pass |
| Current computer-use tests | `pytest -q tests/test_computer_use.py` | exit 0 |
| Android unit tests | `./gradlew test` | exit 0 |
| Android lint | `./gradlew lint` | exit 0 |
| Android instrumented tests | `./gradlew connectedCheck` | exit 0 on the pinned emulator image |
| Protocol conformance | `<sdk-venv>/bin/pytest -q tests/mobile_driver` | exit 0 for fake, Android emulator, and recorded-fixture suites |

The current shell's global Python environment is not a valid verification
baseline: `pytest -q` fails collection because it resolves an older installed
`yutori` package without `yutori.navigator.macos`. Install this project's dev
environment before judging new failures.

## Repository and scope boundaries

### Existing `yutori-mcp` repository

**In scope after the SDK adapter is released:**

- `src/yutori_mcp/mobile_use/__init__.py` — new package
- `src/yutori_mcp/mobile_use/constants.py` — pinned protocol/SDK/tool-set versions
- `src/yutori_mcp/mobile_use/preflight.py` — account, pairing, device-online,
  protocol, unlocked-state, and permission checks
- `src/yutori_mcp/mobile_use/runner.py` — mobile system prompt and SDK adapter lifecycle
- `src/yutori_mcp/mobile_use/supervisor.py` — initially reuse/extract the safe
  child lifecycle; do not weaken existing desktop behavior
- `src/yutori_mcp/mobile_use/result.py` — mobile action/device telemetry formatting
- `src/yutori_mcp/schemas.py` — `MobileUseTaskInput`
- `src/yutori_mcp/server.py` — `list_mobile_devices`,
  `run_mobile_use_task`, and handler registration
- `src/yutori_mcp/cli.py` plus a small mobile-use CLI module — doctor/run/stop
- `tests/test_mobile_use.py` — protocol, handler, lifecycle, and formatter tests
- `README.md`, `TOOLS.md`, and a new mobile-use workflow skill

**Out of scope:**

- Regressing or renaming `run_computer_use_task`
- Putting Android Gradle or Xcode projects in this Python package
- Replacing the proven macOS runtime before the mobile alpha succeeds

### Yutori Python SDK

**In scope:** a `MobileComputer` implementation of the existing structural
`N2Computer` contract, the relay/local transports, protocol types, error
taxonomy, frame polling, and conformance tests. Extract platform-neutral
observation/action types from macOS only when a real second implementation
needs them.

### New mobile-driver repository

Create a dedicated native repository with `android/`, `ios/` (only after the
spike), `protocol/` (schema and fixtures), and `docs/threat-model.md`. Do not
share business logic through a cross-platform UI framework until both native
drivers exist; the privileged platform services are the hard part.

## Steps

### Step 0: Benchmark iPhone Mirroring through the current CUA stack

Do this before writing mobile-driver code. Apple's documented interaction
surface maps Mac click to tap, click-and-hold to touch-and-hold, mouse/trackpad
scroll to swipe, and Mac keyboard input to iPhone typing. It also provides Mac
shortcuts for Home (`Command-1`), App Switcher (`Command-2`), and Spotlight
(`Command-3`). The iPhone must be nearby and locked; unlocking or using it
directly stops mirroring.

Use `run_computer_use_task` with `app="iPhone Mirroring"`. Start with a clean,
non-sensitive fixture state and run each task in three delivery modes:

1. foreground baseline;
2. strict background (`mode="background"`, no fallback);
3. background with foreground fallback.

Before each run, manually confirm on the phone/Mac that mirroring is authorized
and that the test may send the visible mirrored screen to Yutori. During a
foreground run, do not touch the Mac. During a background run, the user may
keep working on the Mac but must leave the iPhone Mirroring window and the
iPhone alone. Never ask the agent to enter a password, passkey, one-time code,
or payment data.

First smoke task:

```text
In iPhone Mirroring, resume the connection if needed. Go to the iPhone Home
Screen, open Calculator, compute 6*7, verify that 42 is visible, and stop. Do
not open any other app or change any setting.
```

Then use a purpose-built fixture app to test tap, long-press, horizontal and
vertical swipe, text entry, scrolling, keyboard dismissal, Home, App Switcher,
Spotlight, app relaunch, portrait/landscape rotation, idle pause/Resume, and
Stop. Add two harmless real-app tasks only after the fixture passes: Calculator
and public-web navigation in Safari. Do not use Messages, Mail, Photos,
password managers, authenticators, banking, health, or settings during this
benchmark.

Capture these metrics per mode and task:

- task success and number of model turns;
- capture, action, and post-action-observation latency;
- tap/long-press/swipe/type landing rate;
- recovery from a missed action and from the `Resume` screen;
- whether an action was pixel, accessibility, exact-window pointer, PID
  keyboard, or foreground fallback;
- coordinate accuracy at Actual, Larger, and Smaller mirroring window sizes;
- any screenshot cropping, scaling, rotation, or stale-frame mismatch;
- whether strict background mode ever steals focus or affects the wrong app.

Pass gate: 8/10 fixture tasks succeed in foreground, Calculator succeeds 10/10,
all three navigation shortcuts work, Stop prevents the next action 10/10, and
there are zero wrong-window actions. Keep background mode only if it reaches
at least 80% of foreground success with zero focus theft; otherwise ship this
interim as foreground-only.

The output is a short report with trajectories, aggregate metrics, failure
taxonomy, and one of three decisions: `ship local preview`, `pixel reliability
work needed`, or `mirroring not viable`. Feed its failures into the mobile
system prompt and the Android evaluation suite.

**Verify**: complete 10 repeated Calculator runs plus the 10-task fixture suite
in foreground; complete the same fixture suite in both background variants;
publish raw aggregate metrics and confirm no sensitive app or credential screen
was captured.

### Step 1: Freeze the alpha contract and threat model

Write the protocol schema, capability matrix, data-flow diagram, and threat
model before native implementation. The alpha contract is:

- one explicitly selected, unlocked device;
- one foreground user session at a time;
- user starts the run from a full-screen confirmation sheet and can stop from
  a persistent system surface;
- default-deny app allowlist, initially excluding Settings, package installers,
  password managers, authenticators, banking, payments, calls, SMS, and
  notification shade;
- no lock-screen interaction, permission-dialog approval, biometric flows,
  passwords, passkeys, one-time codes, purchases, money movement, account
  changes, app install/uninstall, or shell/file-system access;
- screenshots exist only for the live run unless the user explicitly opts into
  run history.

Model-side prompt injection is in scope for the threat model: content displayed
inside apps is untrusted and cannot expand the session capability manifest.

**Verify**: protocol examples validate against the schema; the threat-model
test matrix has a deny/refuse expectation for every excluded action class.

### Step 2: Build the conformance harness before the phone app

Implement a fake device server and table-driven protocol tests. Include normal
session lifecycle, rotation/inset mapping, stale frames, duplicate action IDs,
lost acknowledgements, reconnects, expired sessions, oversize frames/trees,
offline/locked transitions, package switches, and revoke during an action.

Ship golden recordings for portrait and landscape without real personal data.
Both the Python adapter and native app must run against the same fixtures.

**Verify**: the protocol suite passes with a fake server and deliberately
rejects one fixture for every invariant above.

### Step 3: Implement the Android on-device vertical slice

Use native Kotlin. Implement an `AccessibilityService` with only the declared
capabilities needed for:

- observation via `takeScreenshot` on API 30+ and a bounded
  `AccessibilityNodeInfo` tree;
- node-first action (`ACTION_CLICK`, `ACTION_SET_TEXT`, scroll actions) when a
  stable semantic target is available;
- pixel fallback via `dispatchGesture` for tap, long-press, and swipe;
- `performGlobalAction` for Back, Home, and Recents;
- app launch/deep link restricted to the session allowlist.

Node-first execution improves reliability but never silently changes the
meaning of a pixel request. The result must report which route ran. Stop/refuse
when the device locks, the service is disabled, the current package leaves the
allowlist, Android returns a secure-window screenshot failure, or a permission
surface appears.

The app UI needs Pair/Unpair, permission status, the exact task and app scope,
Start, Stop, current action, connection state, and a persistent running
notification. Store the long-term device key in Android Keystore; store no
Yutori API key on the phone.

Prototype transport may use ADB port forwarding. Keep transport behind an
interface so Step 7 can replace it with WSS without touching action execution.

**Verify**: `./gradlew test lint connectedCheck`; then pass a mechanical test on
a physical device: launch Calculator, enter `6*7`, observe `42`, return Home,
and stop. Record every action outcome and confirm the service performs nothing
after Stop.

### Step 4: Add the SDK `MobileComputer` adapter and run n2 end to end

Implement the existing `N2Computer` structural interface; do not fork
`N2ComputerAgent`.

For the first model-connected experiment, use the existing GUI-only batch tool
set (`computer_use_tools-20260716`) so no shell or file tools are offered. Map:

- click -> tap;
- double click -> double tap;
- drag/scroll -> swipe;
- type -> semantic `set_text` with a controlled input fallback;
- supported keypress values -> Android global actions;
- move/hover, mouse buttons, desktop modifiers, shell, and files -> explicit
  unsupported/refused results.

Use a phone-specific system prompt: Android navigation, touch rather than
mouse, portrait/landscape coordinates, screenshots after visual changes, no
desktop shortcuts, and the safety exclusions from Step 1.

This legacy GUI tool set is a prototype bridge, not the production contract.
In parallel, define and evaluate a dated server-side mobile tool set with
`tap`, `long_press`, `swipe`, `type`, `back`, `home`, `recents`, `launch_app`,
`open_url`, `wait`, and `screenshot`, with no shell/file tools.

**Verify**: annotate the adapter as `N2Computer` in type checking; pass the
protocol suite; complete at least 8 of the 10 alpha evaluation tasks below on a
physical Android device with no out-of-scope action.

### Step 5: Expose mobile use through MCP without disturbing Mac use

Add two tools:

- `list_mobile_devices`: paired device ID/display name, platform, online,
  locked/unlocked, permission readiness, app/OS/protocol versions, and current
  run state. Do not return serial numbers or account identifiers.
- `run_mobile_use_task`: `task`, optional `device_id`, optional `app`, optional
  `start_url`, `minutes`, and `max_steps`. If exactly one ready device exists it
  may be selected automatically; otherwise require `device_id`.

Do not add `mode=background`; mobile alpha is visible foreground takeover. Do
not gate tool registration on the MCP host's `sys.platform`. Use a per-device
lease rather than the current machine-wide `DesktopLock`. Keep the child runner,
absolute deadline, event validation, action history, redaction, progress
callbacks, and hard-stop behavior consistent with the existing supervisor.

Add `mobile-use doctor`, `mobile-use run`, and `mobile-use stop --device <id>`.
The MCP tool should be marked destructive/open-world and its description must
state that the phone must remain unlocked and untouched during the run.

**Verify**: `pytest -q`; schema tests cover unknown fields, ambiguous/no device,
offline/locked devices, bounds, URL/app relationship, per-device concurrency,
deadline, cancellation, malformed protocol events, uncertain actions, and
redaction. Existing `tests/test_computer_use.py` remains green unchanged.

### Step 6: Run a two-week direct iOS feasibility spike

Start only after the Android protocol and evaluation gate pass. Use the same
protocol but implement the bridge on a paired Mac using XCTest/WebDriverAgent.
Test Simulator first, then one provisioned physical iPhone.

Use the Step 0 iPhone Mirroring results as the baseline. A direct XCTest route
must justify its setup cost with better reliability, semantic UI access,
latency, remote reach, or test-device scalability. If it does not materially
beat mirroring for the target user segment, retain mirroring as the iPhone
local mode and stop the direct-driver track.

Measure setup time, session startup, screenshot/action latency, installed-app
coverage, system UI coverage, reconnection, signing/provisioning renewal, and
behavior while the device locks. Do not build a consumer companion app during
the spike; it cannot supply general cross-app input authority.

Exit with one decision:

1. **Developer preview:** worthwhile for developers/test labs despite Xcode and
   provisioning requirements.
2. **Managed-device product:** only if enterprise device ownership and setup
   are acceptable.
3. **No full iPhone driver:** offer narrow App Intents/Shortcuts and assisted
   user-tap workflows instead.

**Verify**: complete the same Calculator and 10-task suite on Simulator and a
real device, and publish a measured go/no-go memo. Do not proceed on enthusiasm
alone.

### Step 7: Replace prototype transport with secure pairing and relay

The phone opens an outbound WSS connection to a Yutori relay. Pair by scanning
a QR code containing a single-use, short-lived challenge — never an API key.
Bind the resulting device public key to the account. Use hardware-backed key
storage where available, TLS plus application-layer signed/encrypted session
messages, monotonic sequence numbers, expiries, and replay rejection.

The relay authenticates and routes but cannot invent authority: every run has
a short-lived capability document signed by the control plane and confirmed on
the phone. Revoke must work from the phone, MCP/CLI, and account dashboard.
Rate-limit session starts and actions, and retain metadata-only audit records by
default. Raw screenshots and accessibility text must not enter ordinary server
logs.

Move the n2 runner to Yutori-hosted infrastructure only after the relay is
stable. Until then, keep the existing local runner and route its device calls
through the relay; this isolates transport risk from agent-loop risk.

**Verify**: independent security review plus automated tests for stolen QR,
expired token, replayed action, duplicate action, cross-account device access,
revocation during action, relay reconnect, and log scanning for screenshot/UI
text leakage.

### Step 8: Evaluate, dogfood, and decide distribution

Create a resettable 10-task alpha suite across at least three Android models and
two OS generations:

1. Calculator arithmetic.
2. Create and rename a note in an allowlisted test app.
3. Search Maps without starting navigation.
4. Find a setting in a purpose-built fixture app (not Android Settings).
5. Fill a multi-field form with scrolling.
6. Switch between two allowlisted fixture apps.
7. Rotate during a task and continue accurately.
8. Recover from an app restart.
9. Refuse an action after the package leaves the allowlist.
10. Stop during a gesture and prove no later action executes.

Release gate for a private alpha:

- at least 85% task success across 100 total runs;
- at least 99% action acknowledgements classified (executed/refused/uncertain),
  with zero silent retries of uncertain mutations;
- Stop/revoke prevents the next action in 100% of tests;
- zero actions outside the session allowlist;
- median post-action observation under 1 second on Wi-Fi and p95 under 2.5 seconds;
- no screenshots, UI text, credentials, or identifiers in normal logs;
- threat-model review and privacy copy approved.

Only after this gate decide whether to pursue enterprise distribution, a
policy-compliant deterministic Play offering, or private/developer distribution.

**Verify**: publish a versioned evaluation report with raw aggregate metrics,
failure taxonomy, tested device matrix, and an explicit ship/no-ship decision.

## Test plan

- **Protocol unit tests:** schema validation, version negotiation, size limits,
  frame/action identity, idempotency, deadlines, refusal codes, reconnect, and
  revocation.
- **SDK adapter tests:** coordinate/orientation/inset transforms; node vs gesture
  route; unsupported desktop actions; screenshot encoding; uncertain mutation
  behavior; cancellation and polling.
- **Android unit tests:** tree bounding/redaction, action routing, package
  allowlist, lock/permission transitions, key storage facade, and session state
  machine.
- **Android instrumentation:** real screenshot dimensions, node action, gesture,
  text entry, global Back/Home, rotation, secure-window refusal, service disable,
  and persistent Stop.
- **MCP tests:** model/schema defaults, device selection, per-device lease,
  preflight, subprocess provenance, event shape/order, progress, result
  formatting, secrets redaction, deadline, and Stop.
- **End-to-end:** fake transport in CI, pinned emulator nightly, physical-device
  matrix before release, and the 10-task alpha suite.

## Done criteria

- [ ] iPhone Mirroring benchmark records foreground and background reliability and a ship/no-ship decision.
- [ ] Protocol v1 schema, examples, conformance suite, and threat model are versioned.
- [ ] Android companion passes unit/lint/instrumented tests and its mechanical smoke test.
- [ ] SDK `MobileComputer` passes the shared conformance suite and drives n2 without forking its loop.
- [ ] `list_mobile_devices` and `run_mobile_use_task` pass all MCP tests without changing Mac behavior.
- [ ] Stop/revoke, stale-frame rejection, duplicate-action idempotency, and uncertain-action handling pass fault-injection tests.
- [ ] The 100-run alpha evaluation meets every Step 8 gate.
- [ ] iOS feasibility memo records Simulator and physical-device measurements and a go/no-go decision.
- [ ] No source file outside the explicitly approved repository scopes is modified.
- [ ] `plans/README.md` status is updated.

## STOP conditions

Stop and report rather than improvising if:

- Product requires a normal App Store iPhone app to inject arbitrary touches
  into other apps; the supported platform route does not provide that authority.
- Google Play distribution is a hard alpha requirement while the product uses
  AccessibilityService for autonomous agent behavior.
- The model/backend cannot serve either the existing GUI-only tool set for the
  prototype or a dated mobile-specific tool set.
- The Android app needs root, device-owner status, hidden APIs, security-control
  bypasses, or automatic approval of permission/biometric/system dialogs.
- A proposed optimization retries an action whose acknowledgement was lost.
- Pairing exposes a long-lived credential in a QR code, URL, log, or app storage.
- Completing a step requires weakening the current Mac computer-use tests,
  version pins, redaction, deadline, process isolation, or Stop semantics.
- The Android vertical slice scores below 80% after two focused reliability
  iterations; investigate model/tool semantics before adding more platforms.

## Maintenance notes

- Treat protocol versions and model tool-set identifiers as immutable. Add a
  new dated version; never silently change an existing one.
- Keep platform capability negotiation explicit. Do not paper over iOS/Android
  differences with actions that sometimes no-op.
- Reviewers should focus on authority boundaries, logs, stale-frame math,
  idempotency, uncertain mutations, and behavior when lock/permission/network
  state changes mid-action.
- Android API and store-policy changes are ongoing release risks. Revalidate
  both before every public distribution decision.
- A consumer-safe iPhone experience based on App Intents/Shortcuts is a
  different product tier and should get its own plan after the XCTest spike.

## Authoritative feasibility references

- Apple iPhone Mirroring setup, requirements, and interaction controls:
  https://support.apple.com/en-us/120421
- Apple XCUIAutomation: https://developer.apple.com/documentation/xcuiautomation
- Apple recording UI automation (including multi-app UI tests):
  https://developer.apple.com/documentation/XCUIAutomation/recording-ui-automation-for-testing
- Appium XCUITest real-device setup:
  https://github.com/appium/appium-xcuitest-driver/blob/master/docs/getting-started/device-setup.md
- Android AccessibilityService API:
  https://developer.android.com/reference/android/accessibilityservice/AccessibilityService
- Android AccessibilityNodeInfo actions:
  https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo.AccessibilityAction
- Google Play AccessibilityService policy:
  https://support.google.com/googleplay/android-developer/answer/10964491
