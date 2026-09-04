# Tools

All tool outputs are formatted as human-readable text optimized for LLM consumption.

All tool inputs enforce validation: webhook URLs must use HTTPS, and `output_fields` (where supported) must contain at least one entry. Unknown/extra fields are rejected.

## Computer Use Tools

### run_computer_use_task

Operate a Mac with the computer-use agent - clicking, typing, and driving apps like a person.
Listed only on macOS, and only once the setup in [README](README.md#macos-computer-use) is done.
One task controls the Mac at a time, in one of two modes:

- `mode: "foreground"` (default) drives the whole visible desktop. Do not touch the Mac during
  the run; visible desktop content is sent to Yutori.
- `mode: "background"` drives only the target `app`'s window without taking focus, so you can
  keep working on the Mac (leave that one window alone). Only that window's content is captured
  and sent to Yutori. A menu bar item (the Yutori mark) stays up for the whole run; its menu
  shows the latest frame the agent saw, its latest action, Show activity, and Stop (also ⇧⌘Esc).
  Show activity opens a window with the live frame above the run's conversation with the model
  -- its thinking, every action, and every shell command; foreground runs offer the same window
  from the same menu, without the frame. Actions the driver cannot deliver in the background come back to the agent
  as refusals; `allow_foreground_fallback: true` lets it retry such an action once with the
  window fronted briefly and the prior app restored. Background keyboard delivery is
  app-dependent (clicks reach more apps than typed keys), so typing-heavy tasks usually want it.

**Basic example:**

```json
{
  "task": "In Calculator, compute 17 * 23 and report the result.",
  "app": "Calculator"
}
```

**Advanced example (target app, start URL, longer budget):**

```json
{
  "task": "Open the Yutori company page and list the founders.",
  "app": "Safari",
  "start_url": "https://yutori.com",
  "minutes": 5,
  "max_steps": 80
}
```

**Background example (drive one window while you keep working):**

```json
{
  "task": "Add a note titled 'Standup' with today's three agenda items.",
  "app": "Notes",
  "mode": "background"
}
```

Example response:

```
Outcome: completed
Delivery mode: foreground
Run: https://platform.yutori.com/navigator/chats/5d90f532-6c0f-4159-8e46-2ce55e7084c9
Final text: 17 * 23 = 391
Elapsed: 18452 ms
Perf: total 18.5s over 6 model turns (3.1s/turn)
Actions:
- #1 computer_batch: executed (raw: confirmed; mode: foreground; route: pixel; refusal: None) took 412 ms
- #2 left_click: executed (raw: confirmed; mode: foreground; route: pixel; refusal: None) took 138 ms
```

Example background response:

```
Outcome: completed
Delivery mode: background
Window target: Notes (pid 4242, window 71)
Final text: Created the Standup note with the three agenda items.
Elapsed: 24107 ms
Delivery: 0 foreground escalation(s), 1 background refusal(s)
Perf: total 24.1s over 8 model turns (3.0s/turn)
Actions:
- #1 left_click: executed (raw: confirmed; mode: background; route: accessibility; refusal: None); effect: unverifiable took 233 ms
- #2 type: uncertain (raw: unverifiable; mode: background; route: synthetic_events; refusal: None); effect: unverifiable took 610 ms
```

`Outcome` is `completed`, `limit` (hit `minutes` or `max_steps`), `aborted`, or `failed`.
`Run` links to the run's page on the Yutori platform (platform.yutori.com, or
platform.dev.yutori.com when `--env dev` is selected); it is omitted when the run ended
before the model was called.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `task` | Yes | Natural language instruction for the desktop agent |
| `app` | No | Application to target; omit for cross-app tasks |
| `start_url` | No | URL to open before the task starts; requires `app` |
| `minutes` | No | Wall-clock deadline in minutes (1-60). Default: 30 |
| `max_steps` | No | Max model turns, 1 or more. A turn may contain multiple desktop actions. Default: 60 |
| `mode` | No | `foreground` (default) drives the visible desktop; `background` drives only `app`'s window without taking focus. Background requires `app` |
| `allow_foreground_fallback` | No | Background only. Retry an action that did not land with the window fronted briefly. Default: false |

`max_steps` has no upper bound. The SDK automatically compacts older screenshots and tool
results on long runs; each step is one model turn, not an individual desktop action.

## Browsing Tools

### list_browsing_tasks

List one-time browsing tasks for the user with optional filtering and cursor pagination.

```json
{
  "limit": 10,
  "status": "succeeded"
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `limit` | No | Max tasks to return (1-100). Default: 10 |
| `status` | No | Filter by `running`, `succeeded`, or `failed` |
| `cursor` | No | Cursor from a previous response |

Example response:

```
Found 42 browsing tasks: 1 running, 39 succeeded, 2 failed.

Showing 10 of 39 matching tasks (42 total):

1. Give me a list of all employees of Yutori. (succeeded)
   ID: 54fb19fd-277e-4098-ab72-5a9f8a4347fc
   URL: https://platform.yutori.com/browsing/tasks/54fb19fd-277e-4098-ab72-5a9f8a4347fc
   Created: 2026-06-25

More tasks available. Use list_browsing_tasks(cursor="eyJjcmVhdGVkX2F0...") to load more.
Use list_browsing_tasks(status="succeeded") to list tasks with retrievable results.
Use get_browsing_task_result(task_id) for full details.
```

### run_browsing_task

Execute a one-time web browsing task using the navigator agent. The agent runs either a cloud browser or Yutori Local on the desktop and operates it like a person - clicking, typing, scrolling, and navigating for you.

**Basic example:**

```json
{
  "task": "Give me a list of all employees (names and titles) of Yutori.",
  "start_url": "https://yutori.com"
}
```

**Advanced example (webhooks, structured output):**

```json
{
  "task": "Log in and export the latest invoice.",
  "start_url": "https://example.com/login",
  "max_steps": 75,
  "require_auth": true,
  "webhook_url": "https://example.com/webhook",
  "output_fields": ["name", "title"]
}
```

Example response:

```
Browsing task started.

Task ID: 54fb19fd-277e-4098-ab72-5a9f8a4347fc-1768848396
Status: queued
View progress: https://platform.yutori.com/browsing/tasks/54fb19fd-277e-4098-ab72-5a9f8a4347fc

Poll with get_browsing_task_result(task_id="54fb19fd-277e-4098-ab72-5a9f8a4347fc-1768848396") to check status.
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `task` | Yes | Natural language instruction for the navigator |
| `start_url` | Yes | URL where browsing begins |
| `max_steps` | No | Max browser actions (1-100). Default: 25 |
| `require_auth` | No | If true, use an auth-optimized cloud browser provider for login flows. Only applies when browser is `cloud` (default) |
| `browser` | No | `cloud` (default) or `local` to use Yutori Local with the user's logged-in desktop browser |
| `output_fields` | No | List of field names for structured output as array of objects |
| `webhook_url` | No | HTTPS URL for completion notification |
| `webhook_format` | No | `scout` (default), `slack`, or `zapier` |

### get_browsing_task_result

Poll for the status and result of a browsing task. Call this after `run_browsing_task` until status is `succeeded` or `failed`.

```json
{
  "task_id": "54fb19fd-277e-4098-ab72-5a9f8a4347fc-1768848396"
}
```

Example response (running):

```
Task in progress.

Task ID: 54fb19fd-277e-4098-ab72-5a9f8a4347fc-1768848396
Status: running

Poll again in a few seconds.
```

Example response (succeeded):

```
Task completed.

Task ID: 54fb19fd-277e-4098-ab72-5a9f8a4347fc-1768848396
Status: succeeded

Result:
Summary of All Yutori Employees

I have successfully located all employees of Yutori on their company page.
Here is the complete list of 17 employees with their names and titles:

Founders & Leadership:
1. Abhishek Das - Co-founder and Co-CEO
2. Devi Parikh - Co-founder and Co-CEO
3. Dhruv Batra - Co-founder and Chief Scientist

Executive:
4. Kristi Edleson - Chief of Staff

Technical Staff:
5. Rui Wang - Member of Technical Staff
... (17 employees total)

Source Page: https://yutori.com/company#team
```

## Research Tools

### list_research_tasks

List one-time research tasks for the user with optional filtering and cursor pagination.

```json
{
  "limit": 10,
  "status": "succeeded"
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `limit` | No | Max tasks to return (1-100). Default: 10 |
| `status` | No | Filter by `running`, `succeeded`, or `failed` |
| `cursor` | No | Cursor from a previous response |

Example response:

```
Found 248 research tasks: 0 running, 245 succeeded, 3 failed.

Showing 10 of 245 matching tasks (248 total):

1. Competitive landscape for AI code assistants (succeeded)
   ID: ae27a17c-a4ed-4c69-8b2a-4bec330fc935
   URL: https://platform.yutori.com/research/tasks/ae27a17c-a4ed-4c69-8b2a-4bec330fc935
   Created: 2026-06-25

More tasks available. Use list_research_tasks(cursor="eyJjcmVhdGVkX2F0...") to load more.
Use list_research_tasks(status="succeeded") to list tasks with retrievable results.
Use get_research_task_result(task_id) for full details.
```

### run_research_task

Execute a one-time deep web research task. The research agent searches, reads, and synthesizes information from across the web.

**Basic example:**

```json
{
  "query": "What are the latest developments in quantum computing from the past week? Include company announcements, research papers, and product releases."
}
```

**Advanced example (webhooks, structured output):**

```json
{
  "query": "What are the latest developments in quantum computing from the past week? Include company announcements, research papers, and product releases.",
  "user_timezone": "America/Los_Angeles",
  "webhook_url": "https://example.com/webhook",
  "output_fields": ["title", "summary", "source_url", "category"]
}
```

Example response:

```
Research task started.

Task ID: ae27a17c-a4ed-4c69-8b2a-4bec330fc935-1768848395
Status: queued
View progress: https://platform.yutori.com/research/tasks/ae27a17c-a4ed-4c69-8b2a-4bec330fc935

Poll with get_research_task_result(task_id="ae27a17c-a4ed-4c69-8b2a-4bec330fc935-1768848395") to check status.
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Natural language description of what to research |
| `user_timezone` | No | Timezone for context. Default: 'America/Los_Angeles' |
| `user_location` | No | Location for context. Default: 'San Francisco, CA, US' |
| `output_fields` | No | List of field names for structured output as array of objects |
| `webhook_url` | No | HTTPS URL for completion notification |
| `webhook_format` | No | `scout` (default), `slack`, or `zapier` |

### get_research_task_result

Poll for the status and result of a research task. Call this after `run_research_task` until status is `succeeded` or `failed`.

```json
{
  "task_id": "ae27a17c-a4ed-4c69-8b2a-4bec330fc935-1768848395"
}
```

Example response (running):

```
Task in progress.

Task ID: ae27a17c-a4ed-4c69-8b2a-4bec330fc935-1768848395
Status: running

Poll again in a few seconds.
```

Example response (succeeded):

```
Task completed.

Task ID: ae27a17c-a4ed-4c69-8b2a-4bec330fc935-1768848395
Status: succeeded

Result:
Hardware strides and strategic moves this week

I focused on notable hardware breakthroughs, leadership changes, applied research,
and an industry appearance from January 12–19, 2026.

• MIT demonstrated chip-based cooling for trapped-ion qubits
• EeroQ unveiled a scalable quantum control chip
• IonQ appointed Katie Arrington as Chief Information Officer
• Researchers introduced QUPID, a quantum neural network
```

## Scout Tools

### list_scouts

List scouts for the user with optional filtering.

```json
{
  "limit": 10,
  "status": "active"
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `limit` | No | Max scouts to return (1-100). Default: 10 |
| `status` | No | Filter by `active`, `paused`, or `done` |
| `cursor` | No | Pagination cursor from a previous response's `next_cursor` |

Example response:

```
Found 87 scouts: 72 active, 12 paused, 3 done.

Showing 10 of 87:

1. Yutori news and updates (active)
   Query: "Tell me about the latest news, product updates, or..."
   ID: 690bd26c-0ef8-42f4-99e4-8fca6ea20e6f
   URL: https://platform.yutori.com/scouting/tasks/690bd26c-0ef8-42f4-99e4-8fca6ea20e6f
   Runs daily | Next: 2026-01-16

2. Yutori API changelog (paused)
   Query: "Monitor Yutori API changelog for breaking changes"
   ID: 36d178a0-591f-4567-8019-32d24f9e55ba
   URL: https://platform.yutori.com/scouting/tasks/36d178a0-591f-4567-8019-32d24f9e55ba
   Runs every 12 hours | Next: 2026-01-10

... (8 more)

Use list_scouts(status="active") to filter by status.
Use list_scouts(limit=50) to see more.
Use get_scout_detail(scout_id) for full details.
```

### get_scout_detail

Get detailed information for a specific scout.

```json
{
  "scout_id": "690bd26c-0ef8-42f4-99e4-8fca6ea20e6f"
}
```

Example response:

```
Scout: Yutori news and updates
ID: 690bd26c-0ef8-42f4-99e4-8fca6ea20e6f
URL: https://platform.yutori.com/scouting/tasks/690bd26c-0ef8-42f4-99e4-8fca6ea20e6f
Status: active

Query: "Tell me about the latest news, product updates, or announcements about Yutori"

Schedule:
  Interval: daily
  Next run: 2026-01-16 18:32 UTC
  Timezone: America/Los_Angeles

Configuration:
  Webhook: not configured
  Email notifications: enabled
  Public: yes

Created: 2026-01-15
```

### create_scout

Create a new monitoring scout for continuous web monitoring. Scouts track changes relevant to a query at a configurable schedule and alert you with structured data.

**Basic example:**

```json
{
  "query": "Tell me about the latest news, product updates, press releases, social media announcements, investments into, or other relevant information about Yutori"
}
```

**Advanced example (scheduling, webhooks, structured output):**

```json
{
  "query": "Tell me about the latest news, product updates, press releases, social media announcements, investments into, or other relevant information about Yutori",
  "output_interval": 86400,
  "user_timezone": "America/Los_Angeles",
  "skip_email": true,
  "webhook_url": "https://example.com/webhook",
  "output_fields": ["headline", "summary", "source_url"]
}
```

Example response:

```
Scout created successfully.

Name: Yutori news and updates
ID: 3d1d5e2a-5b6c-4a9c-8f8c-2f2e3b4a5c6d
URL: https://platform.yutori.com/scouting/tasks/3d1d5e2a-5b6c-4a9c-8f8c-2f2e3b4a5c6d
Status: active

Query: "Tell me about the latest news, product updates, press releases, social..."
Schedule: runs daily
First run: 2026-01-07 03:10 UTC
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Natural language description of what to monitor |
| `output_interval` | No | Seconds between runs (min: 1800). Default: 86400 |
| `webhook_url` | No | HTTPS URL for webhook notifications |
| `webhook_format` | No | `scout` (default), `slack`, or `zapier` |
| `output_fields` | No | List of field names for structured output as array of objects |
| `user_timezone` | No | Timezone for scheduling |
| `skip_email` | No | Skip email notifications |
| `start_timestamp` | No | Unix timestamp for when monitoring should start (0 = immediately) |
| `user_location` | No | Location for geo-relevant searches |
| `is_public` | No | Whether scout results are publicly accessible |

### edit_scout

Update an existing scout's query, schedule, webhook configuration, or status.

**Change status only (pause a scout):**

```json
{
  "scout_id": "abc123-...",
  "status": "paused"
}
```

**Update configuration:**

```json
{
  "scout_id": "abc123-...",
  "output_interval": 43200,
  "user_timezone": "America/New_York"
}
```

**Update configuration and resume:**

```json
{
  "scout_id": "abc123-...",
  "query": "updated monitoring query",
  "status": "active"
}
```

Example response:

```
Scout updated successfully.

Name: Yutori API changelog
ID: 7c8692c3-c637-4302-a982-b9f4f7b49407
URL: https://platform.yutori.com/scouting/tasks/7c8692c3-c637-4302-a982-b9f4f7b49407

Changes applied:
  • Status: paused → active
  • Query: "Monitor Yutori API changelog..." → "updated monitoring query"
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `scout_id` | Yes | Scout UUID |
| `status` | No | `active` (resume), `paused` (pause), or `done` (archive) |
| `query` | No | Updated monitoring query |
| `output_interval` | No | Seconds between runs (min 1800) |
| `webhook_url` | No | HTTPS URL for webhook notifications |
| `webhook_format` | No | `scout`, `slack`, or `zapier` |
| `output_fields` | No | List of field names for structured output |
| `user_timezone` | No | Timezone for scheduling |
| `user_location` | No | Location for geo-relevant searches |
| `is_public` | No | Whether scout results are publicly accessible |
| `skip_email` | No | Skip email notifications |

### delete_scout

Permanently delete a scout. **This cannot be undone.**

```json
{
  "scout_id": "abc123-..."
}
```

Example response:

```
Scout deleted.

ID: abc123-...

This action cannot be undone.
```

### get_scout_updates

Get paginated updates from a scout.

```json
{
  "scout_id": "690bd26c-0ef8-42f4-99e4-8fca6ea20e6f",
  "limit": 2
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `scout_id` | Yes | The scout's unique identifier (UUID) |
| `cursor` | No | Pagination cursor from a previous response |
| `limit` | No | Maximum number of updates to return (1-100) |

Example response:

```
Found 2 update(s):

--- Update #1 —
Date: 2026-01-16 05:45 UTC

Yutori Product Updates

Yutori has released new MCP server tools for web monitoring and browsing automation...

--- Update #2 —
Date: 2026-01-15 05:45 UTC

No new findings since last update.
```

## Usage

### list_api_usage

Get API usage statistics including active scout counts, rate limits, and activity metrics.

```json
{
  "period": "7d"
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `period` | No | Activity time range: `24h` (default), `7d`, `30d`, or `90d` |

Example response:

```
Active Scouts: 5
  - a1b2c3d4-e5f6-7890-abcd-ef1234567890
  - b2c3d4e5-f6a7-8901-bcde-f12345678901
  ... and 3 more

API Rate Limits (available):
  Requests today: 1250
  Daily limit: 10000
  Remaining: 8750
  Resets at: 2026-03-04T00:00:00+00:00

Navigator API Rate Limits:
  Requests today: 342
  Daily limit: 50000
  Remaining: 49658
  Per-second limit: 20
  Resets at: 2026-03-04T00:00:00+00:00

Activity (7d):
  Scout runs: 47
  Browsing tasks: 12
  Research tasks: 8
  Navigator API calls: 1523
```

## Tool Annotations

Tools include hints for client behavior:

| Tool | Annotation |
|------|------------|
| `run_computer_use_task` | `destructiveHint: true`, `openWorldHint: true` |
| `list_browsing_tasks`, `get_browsing_task_result`, `list_research_tasks`, `get_research_task_result`, `list_scouts`, `get_scout_detail`, `get_scout_updates`, `list_api_usage` | `readOnlyHint: true` |
| `edit_scout` | `idempotentHint: true` |
| `delete_scout` | `destructiveHint: true` |
