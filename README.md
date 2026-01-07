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
claude mcp add yutori --env YUTORI_API_KEY=yt-your-api-key -- uvx yutori-mcp
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
        "YUTORI_API_KEY": "yt-your-api-key"
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
        "YUTORI_API_KEY": "yt-your-api-key"
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

<details>
<summary>ChatGPT</summary>

Open ChatGPT Desktop and go to Settings -> Connectors -> MCP Servers -> Add server.

```json
{
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"],
      "env": {
        "YUTORI_API_KEY": "yt-your-api-key"
      }
    }
  }
}
```

For setup details, see the [OpenAI MCP guide](https://platform.openai.com/docs/mcp).
</details>

<details open>
<summary>Codex</summary>

```bash
codex mcp add yutori --env YUTORI_API_KEY=yt-your-api-key -- uvx yutori-mcp
```

Or add to `~/.codex/config.toml`:

```toml
[mcp_servers.yutori]
command = "uvx"
args = ["yutori-mcp"]

[mcp_servers.yutori.env]
YUTORI_API_KEY = "yt-your-api-key"
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
        "YUTORI_API_KEY": "yt-your-api-key"
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
      "id": "9b1f2b2c-7ed5-4c4c-9c2a-2a8f0c7fd312",
      "query": "Monitor Yutori product updates",
      "display_name": "Monitor Yutori product updates",
      "status": "active",
      "output_interval": 86400,
      "created_at": "2026-01-05T18:32:10Z",
      "next_output_timestamp": "2026-01-06T18:32:00Z"
    },
    {
      "id": "1c2a3d4e-5f60-4f7a-9c2d-3e4f5a6b7c8d",
      "query": "H100 pricing per hour drops below $1.50",
      "display_name": "H100 pricing per hour drops below $1.50",
      "status": "paused",
      "output_interval": 43200,
      "created_at": "2026-01-02T08:14:22Z",
      "next_output_timestamp": "2026-01-02T20:14:00Z"
    }
  ]
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
  "id": "9b1f2b2c-7ed5-4c4c-9c2a-2a8f0c7fd312",
  "query": "Monitor Yutori product updates",
  "display_name": "Monitor Yutori product updates",
  "status": "active",
  "created_at": "2026-01-05T18:32:10Z",
  "next_run_timestamp": "2026-01-06T18:32:00Z",
  "next_output_timestamp": "2026-01-06T18:32:00Z",
  "user_timezone": "UTC",
  "output_interval": 86400,
  "completed_at": null,
  "paused_at": null,
  "last_update_timestamp": null,
  "update_count": 0,
  "query_object": {
    "query": "Monitor Yutori product updates",
    "llm_what": "Monitor Yutori product updates",
    "llm_condition": "whenever there is an update",
    "skip_email": true,
    "source_api": "scout_creation"
  },
  "is_public": true
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
  "id": "3d1d5e2a-5b6c-4a9c-8f8c-2f2e3b4a5c6d",
  "query": "Tell me about the latest news, product updates, or announcements about Yutori",
  "query_object": {
    "query": "Tell me about the latest news, product updates, or announcements about Yutori",
    "llm_what": "Tell me about the latest news, product updates, or announcements about Yutori",
    "llm_condition": "whenever there is an update",
    "skip_email": true,
    "source_api": "scout_creation"
  },
  "display_name": "Tell me about the latest news, product updates, or announcements about Yutori",
  "next_run_timestamp": "1970-01-01T00:00:00Z",
  "user_timezone": "UTC",
  "next_output_timestamp": "1970-01-01T00:00:00Z",
  "created_at": "2026-01-06T03:10:45Z",
  "completed_at": null,
  "paused_at": null,
  "is_public": true,
  "webhook_url": null
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
  "id": "3d1d5e2a-5b6c-4a9c-8f8c-2f2e3b4a5c6d",
  "query": "Tell me about the latest news, product updates, or announcements about Yutori",
  "query_object": {
    "query": "Tell me about the latest news, product updates, or announcements about Yutori",
    "llm_what": "Tell me about the latest news, product updates, or announcements about Yutori",
    "llm_condition": "whenever there is an update",
    "skip_email": true,
    "source_api": "scout_creation"
  },
  "display_name": "Tell me about the latest news, product updates, or announcements about Yutori",
  "next_run_timestamp": "1970-01-01T00:00:00Z",
  "user_timezone": "UTC",
  "next_output_timestamp": "1970-01-01T00:00:00Z",
  "created_at": "2026-01-06T03:10:45Z",
  "completed_at": null,
  "paused_at": null,
  "is_public": true,
  "webhook_url": null
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
{}
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
{}
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
{}
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
{}
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
      "id": "upd_9e6f8aac-aa86-4936-b6c3-a25c88493f6c",
      "timestamp": 1767578701819,
      "content": "<h3>Yutori ships a new SDK update</h3><p>Release notes include webhook retries and new filters.</p>",
      "citations": [
        {
          "id": "0",
          "url": "https://example.com/releases/yutori-sdk",
          "preview_data": {
            "title": "Yutori SDK 1.2.3",
            "description": "Release notes for the latest SDK update.",
            "image": "https://example.com/images/sdk.png",
            "url": "https://example.com/releases/yutori-sdk"
          }
        }
      ],
      "stats": {
        "num_tool_calls": 12,
        "num_mcp_tool_calls": 12,
        "num_crawler_calls": 0,
        "num_navigator_steps": 0,
        "num_websites_visited": 0,
        "sec_saved": 240
      },
      "structured_result": {
        "stories": [
          {
            "news": "Yutori SDK 1.2.3 adds webhook retries and filters.",
            "source": "https://example.com/releases/yutori-sdk"
          }
        ]
      }
    }
  ],
  "prev_cursor": null,
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
  "task_id": "11111111-1111-1111-1111-111111111111-1700000000",
  "view_url": "https://scouts.yutori.com/11111111-1111-1111-1111-111111111111",
  "status": "queued"
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
  "task_id": "11111111-1111-1111-1111-111111111111-1700000000",
  "view_url": "https://scouts.yutori.com/11111111-1111-1111-1111-111111111111",
  "status": "succeeded",
  "result": "<pre>H1 Text: Example Domain</pre>",
  "paused": true
}
```

### Research Operations

#### run_research_task

Execute a one-time deep web research task. The research agent searches, reads, and synthesizes information from across the web.

```json
{
  "query": "What are the latest developments in quantum computing from the past week?",
  "user_timezone": "America/New_York"
}
```

Example queries:
- Research competitive landscape for a product
- Summarize recent news about a company
- Find technical documentation or specifications

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Natural language description of what to research |
| `user_timezone` | No | Timezone for context. Default: 'America/Los_Angeles' |
| `user_location` | No | Location for context. Default: 'San Francisco, CA, US' |
| `task_spec` | No | JSON Schema for structured output |
| `webhook_url` | No | URL for completion notification |
| `webhook_format` | No | `scout` (default), `slack`, or `zapier` |

Returns a `task_id` for polling status.

Example response:

```json
{
  "task_id": "22222222-2222-2222-2222-222222222222-1700000000",
  "view_url": "https://scouts.yutori.com/22222222-2222-2222-2222-222222222222",
  "status": "queued"
}
```

#### get_research_task_result

Poll for the status and result of a research task. Call this after `run_research_task` until status is `succeeded` or `failed`.

```json
{
  "task_id": "abc123-456-..."
}
```

Returns:
- `status`: `queued`, `running`, `succeeded`, or `failed`
- `result`: Text result (when complete)
- `structured_result`: JSON result (if `task_spec` was provided)
- `updates`: Array of update objects with timestamps and content

Example response:

```json
{
  "task_id": "22222222-2222-2222-2222-222222222222-1700000000",
  "view_url": "https://scouts.yutori.com/22222222-2222-2222-2222-222222222222",
  "status": "succeeded",
  "result": "## Quantum Computing Developments\n\n### Key Findings...",
  "structured_result": null
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
  "num_scouts": 3,
  "active_scout_ids": [
    "22222222-2222-2222-2222-222222222222",
    "33333333-3333-3333-3333-333333333333"
  ]
}
```

## Tool Annotations

Tools include hints for client behavior:

| Tool | Annotation |
|------|------------|
| `list_scouts`, `get_scout_detail`, `get_scout_updates`, `list_api_usage`, `get_browsing_task_result`, `get_research_task_result` | `readOnlyHint: true` |
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
export YUTORI_API_KEY=yt-your-api-key
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
