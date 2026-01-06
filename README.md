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

<details>
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

<details>
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

#### get_scout_detail

Get detailed information for a specific scout.

```json
{
  "scout_id": "abc123-..."
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

#### pause_scout

Pause a running scout.

```json
{
  "scout_id": "abc123-..."
}
```

#### resume_scout

Resume a paused scout.

```json
{
  "scout_id": "abc123-..."
}
```

#### complete_scout

Mark a scout as complete (archive).

```json
{
  "scout_id": "abc123-..."
}
```

#### delete_scout

Permanently delete a scout. **This cannot be undone.**

```json
{
  "scout_id": "abc123-..."
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

### Account Operations

#### list_api_usage

Get usage statistics.

```json
{}
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
