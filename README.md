# Yutori MCP Server

An MCP server for [Yutori](https://yutori.com) - web monitoring and browsing automation.

## Features

- **Scouts**: Create agents that that monitor the web for anything you care about at a desired frequency 
- **Browsing**: Execute one-time web browsing tasks using an AI website navigator (or a browser-use AI agent)
- **Structured Output**: Optionally enforce JSON schemas on results

## Installation

Install `uvx` (via uv) first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or with Homebrew:

```bash
brew install uv
```

Get your API key from [yutori.com/api](https://yutori.com/api).

<details open>
<summary>Claude Code</summary>

```bash
claude mcp add yutori --env YUTORI_API_KEY=sk-your-api-key -- uvx yutori-mcp
```
</details>

<details>
<summary>Claude Desktop</summary>

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"],
      "env": {
        "YUTORI_API_KEY": "sk-your-api-key"
      }
    }
  }
}
```

For setup details, see the [Claude Desktop MCP install guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers).
</details>

<details>
<summary>Cursor</summary>

**Click the button to install:**

[<img src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Install in Cursor">](https://cursor.com/en/install-mcp?name=Yutori&config=eyJjb21tYW5kIjoidXZ4IHl1dG9yaS1tY3AifQ%3D%3D)

The Cursor install button does not support env vars, so you must set `YUTORI_API_KEY` manually after install.

Set `YUTORI_API_KEY` in the server env settings (Cursor Settings → MCP), then restart the server.

![Cursor MCP server env settings](images/cursor-mcp-settings.png)

**Or install manually:**

Go to Cursor Settings → MCP → Add new MCP Server, then add:

```json
{
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"],
      "env": {
        "YUTORI_API_KEY": "sk-your-api-key"
      }
    }
  }
}
```

See the [Cursor MCP guide](https://cursor.com/docs/context/mcp) for setup details.
</details>

<details>
<summary>VS Code</summary>

**Click the button to install:**

[<img src="https://img.shields.io/badge/VS_Code-VS_Code?style=flat-square&label=Install%20Server&color=0098FF" alt="Install in VS Code">](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522yutori%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522yutori-mcp%2522%255D%257D) [<img alt="Install in VS Code Insiders" src="https://img.shields.io/badge/VS_Code_Insiders-VS_Code_Insiders?style=flat-square&label=Install%20Server&color=24bfa5">](https://insiders.vscode.dev/redirect?url=vscode-insiders%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522yutori%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522yutori-mcp%2522%255D%257D)

Set `YUTORI_API_KEY` in your environment before first use.

**Or install manually:**

```bash
code --add-mcp '{"name":"yutori","command":"uvx","args":["yutori-mcp"],"envFile":"path/to/.env"}'
```
</details>

<details open>
<summary>Codex</summary>

```bash
codex mcp add yutori --env YUTORI_API_KEY=sk-your-api-key -- uvx yutori-mcp
```

Or add to `~/.codex/config.toml`:

```toml
[mcp_servers.yutori]
command = "uvx"
args = ["yutori-mcp"]

[mcp_servers.yutori.env]
YUTORI_API_KEY = "sk-your-api-key"
```
</details>

<details>
<summary>Gemini CLI</summary>

Add to `~/.gemini/settings.json`:

```json
{ ...file contains other config objects
  "mcp": {
    "allowed": ["yutori", "...other MCPs you already allow..."]
  },
  ...file contains other config objects
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"],
      "env": {
        "YUTORI_API_KEY": "sk-your-api-key"
      }
    }
  }
}
```

For more details, see the [Gemini CLI MCP settings guide](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md#configure-the-mcp-server-in-settingsjson).
</details>

<details>
<summary>Using pip</summary>

```bash
pip install yutori-mcp
```
</details>

## Tools

### Scout Operations

#### list_scouts

List all scouts for the user (basic metadata only). 

```json
{}
```

Example response:

```json
{
  "scouts": [
    {
      "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
      "query": "latest news and product updates about Yutori",
      "status": "active",
      "output_interval": 86400,
      "last_run_at": "2025-02-12T16:10:24Z",
      "next_run_at": "2025-02-13T16:10:24Z",
      "created_at": "2025-02-01T19:02:11Z"
    },
    {
      "id": "8a1c9d02-7c0d-4d27-8a1e-3bf7d2ed9a3c",
      "query": "H100 pricing per hour drops below $1.50",
      "status": "paused",
      "output_interval": 43200,
      "last_run_at": "2025-02-10T05:34:10Z",
      "next_run_at": null,
      "created_at": "2025-01-17T03:41:55Z"
    }
  ],
  "next_cursor": null
}
```

#### get_scout_detail

Get detailed information for a specific scout.

```json
{
  "scout_id": "abc123-..."
}
```

Example response:

```json
{
  "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
  "query": "latest news and product updates about Yutori",
  "status": "active",
  "output_interval": 86400,
  "webhook_url": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
  "webhook_format": "slack",
  "task_spec": {
    "type": "object",
    "properties": {
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "title": { "type": "string" },
            "url": { "type": "string" }
          },
          "required": ["title", "url"]
        }
      }
    },
    "required": ["items"]
  },
  "user_timezone": "America/Los_Angeles",
  "skip_email": false,
  "created_at": "2025-02-01T19:02:11Z",
  "updated_at": "2025-02-12T16:10:24Z",
  "last_run_at": "2025-02-12T16:10:24Z",
  "next_run_at": "2025-02-13T16:10:24Z"
}
```

#### create_scout

Create a new monitoring scout for continuous web monitoring. Scouts track changes relevant to a query at a configurable schedule and alert you with structured data.

```json
{
  "query": "Tell me about the latest news, product updates, or announcements about Yutori",
  "output_interval": 86400,
  "webhook_url": "https://hooks.slack.com/...",
  "webhook_format": "slack"
}
```

Example response:

```json
{
  "id": "c3b2e9a1-4e9b-4d87-9a5c-45e0f6f7a8c9",
  "query": "Tell me about the latest news, product updates, or announcements about Yutori",
  "status": "active",
  "output_interval": 86400,
  "webhook_url": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
  "webhook_format": "slack",
  "created_at": "2025-02-12T18:01:07Z",
  "next_run_at": "2025-02-13T18:01:07Z"
}
```

Example queries:
- "anytime a startup in SF announces seed funding"
- "when H100 pricing per hour drops below $1.50"
- "latest news and product updates about Yutori"

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Natural language description of what to monitor |
| `output_interval` | No | Seconds between runs (min: 1800). Default: 86400 |
| `webhook_url` | No | URL for webhook notifications |
| `webhook_format` | No | `scout` (default) or `slack` |
| `task_spec` | No | JSON Schema for structured output |
| `user_timezone` | No | Timezone for scheduling |
| `skip_email` | No | Skip email notifications |

#### edit_scout

Update an existing scout.

```json
{
  "scout_id": "abc123-...",
  "output_interval": 43200
}
```

Example response:

```json
{
  "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
  "query": "latest news and product updates about Yutori",
  "status": "active",
  "output_interval": 43200,
  "updated_at": "2025-02-12T18:05:22Z"
}
```

#### pause_scout

Pause a running scout.

```json
{
  "scout_id": "abc123-..."
}
```

Example response:

```json
{
  "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
  "status": "paused",
  "paused_at": "2025-02-12T18:06:10Z"
}
```

#### resume_scout

Resume a paused scout.

```json
{
  "scout_id": "abc123-..."
}
```

Example response:

```json
{
  "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
  "status": "active",
  "resumed_at": "2025-02-12T18:07:02Z"
}
```

#### complete_scout

Mark a scout as complete (archive).

```json
{
  "scout_id": "abc123-..."
}
```

Example response:

```json
{
  "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
  "status": "completed",
  "completed_at": "2025-02-12T18:07:55Z"
}
```

#### delete_scout

Permanently delete a scout. **This cannot be undone.**

```json
{
  "scout_id": "abc123-..."
}
```

Example response:

```json
{
  "id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
  "deleted": true
}
```

#### get_scout_updates

Get paginated updates from a scout.

```json
{
  "scout_id": "abc123-...",
  "limit": 10
}
```

Example response:

```json
{
  "updates": [
    {
      "id": "upd_3b6a5f7c-0f2a-4f48-9b21-0b6f5e3c2d1a",
      "scout_id": "2f6d7c34-9db8-4a1d-99c2-8b5f1c3b0e55",
      "status": "succeeded",
      "created_at": "2025-02-12T16:10:24Z",
      "summary": "Found 2 new mentions of Yutori product updates.",
      "result": "- Yutori launches new monitoring UI\\n- Yutori adds webhook retries",
      "structured_result": {
        "items": [
          {
            "title": "Yutori launches new monitoring UI",
            "url": "https://yutori.com/blog/monitoring-ui",
            "source": "yutori.com"
          },
          {
            "title": "Yutori adds webhook retries",
            "url": "https://yutori.com/blog/webhook-retries",
            "source": "yutori.com"
          }
        ]
      }
    }
  ],
  "next_cursor": null
}
```

### Browsing Operations

#### run_browsing_task

Execute a one-time web browsing task using the navigator agent. The agent runs a cloud browser and operates it like a person - clicking, typing, scrolling, and navigating for you.

```json
{
  "task": "Give me a list of all employees (names and titles) of Yutori",
  "start_url": "https://yutori.com",
  "max_steps": 20
}
```

Example tasks:
- Fill forms on websites
- Extract structured data from complex web pages
- Automate multi-step workflows that require authentication

| Parameter | Required | Description |
|-----------|----------|-------------|
| `task` | Yes | Natural language instruction for the navigator |
| `start_url` | Yes | URL where browsing begins |
| `max_steps` | No | Max browser actions (1-100). Default: 25 |
| `task_spec` | No | JSON Schema for structured output |
| `webhook_url` | No | URL for completion notification |
| `webhook_format` | No | `scout` (default) or `slack` |

Returns a `task_id` for polling status.

Example response:

```json
{
  "task_id": "browse_5a6c9d3e-8c2f-4e2f-9a2a-7c8f9e1d2c3b",
  "status": "queued",
  "created_at": "2025-02-12T18:02:31Z"
}
```

#### get_browsing_task_result

Poll for the status and result of a browsing task. Call this after `run_browsing_task` until status is `succeeded` or `failed`.

```json
{
  "task_id": "abc123-456-..."
}
```

Returns:
- `status`: `queued`, `running`, `succeeded`, or `failed`
- `result`: Text result (when complete)
- `structured_result`: JSON result (if `task_spec` was provided)

Example response:

```json
{
  "task_id": "browse_5a6c9d3e-8c2f-4e2f-9a2a-7c8f9e1d2c3b",
  "status": "succeeded",
  "result": "Found 2 employees on the team page.",
  "structured_result": {
    "employees": [
      { "name": "Jane Doe", "title": "CEO" },
      { "name": "Alex Kim", "title": "CTO" }
    ]
  }
}
```

### Account Operations

#### list_api_usage

Get usage statistics.

```json
{}
```

Example response:

```json
{
  "period_start": "2025-02-01",
  "period_end": "2025-02-29",
  "requests": 128,
  "scout_runs": 42,
  "browsing_tasks": 7,
  "credits_used": 93.5
}
```

## Tool Annotations

Tools include hints for client behavior:

| Tool | Annotation |
|------|------------|
| `list_scouts`, `get_scout_detail`, `get_scout_updates`, `list_api_usage`, `get_browsing_task_result` | `readOnlyHint: true` |
| `pause_scout`, `resume_scout`, `complete_scout` | `idempotentHint: true` |
| `delete_scout` | `destructiveHint: true` |

## Development

### Setup

```bash
git clone https://github.com/yutori-ai/yutori-mcp
cd yutori-mcp
pip install -e ".[dev]"
```

### Testing

```bash
pytest
```

### Running locally

```bash
export YUTORI_API_KEY=sk-your-api-key
python -m yutori_mcp.server
```

### Debugging with MCP Inspector

```bash
npx @modelcontextprotocol/inspector yutori-mcp
```

## API Documentation

For full API documentation, visit [docs.yutori.com](https://docs.yutori.com).

## License

Apache 2.0
