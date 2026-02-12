# Yutori MCP Server / Plugin / Skills

An MCP server and skills for [Yutori](https://yutori.com/api) — web monitoring and browsing automation.

You can use it with Claude Code, Cursor, VS Code, ChatGPT, Codex, OpenClaw, and other MCP hosts.

## Features

- **Scouts**: Create agents that monitor the web for anything you care about at a desired frequency
- **Research**: Execute one-time deep web research tasks
- **Browsing**: Execute one-time web browsing tasks using an AI website navigator
- **Skills**: Workflow templates (competitor watch, API/changelog monitoring) for clients that support them

## Installation

### Requirements
If you don't already have `uv` installed, install it (it includes `uvx`):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or with Homebrew:

```bash
brew install uv
```



For the quickstart below, Node.js is also required (for `npx`).

### Quickstart

1. Authenticate using

    ```bash
    uvx yutori-mcp login
    ```
    to get your setup automatically.

    Or

    Go to (https://platform.yutori.com) and manually add your API key to the config file:
   ```bash
   mkdir -p ~/.yutori
   cat > ~/.yutori/config.json << 'EOF'
   {"api_key": "yt-your-api-key"}
   EOF
   ```



2. Install MCP and skills for your tools via [skills.sh](https://skills.sh):
   ```bash
   npx skills add yutori-ai/yutori-mcp
   ```
   When prompted, choose which skills to install and which tools (e.g. OpenClaw, Codex).

3. For Claude Code, run inside Claude:
   ```
   /plugin marketplace add yutori-ai/yutori-mcp
   /plugin install yutori@yutori-plugins
   ```



### Per-client setup

<details>
<summary>Claude Code</summary>

1. **Plugin (Recommended)** - Includes MCP tools + workflow skills

   Run these commands inside Claude Code:
   ```
   /plugin marketplace add yutori-ai/yutori-mcp
   /plugin install yutori@yutori-plugins
   ```

   This installs both the MCP tools and workflow skills:

   | Skill | Description |
   |-------|-------------|
   | `/yutori:scout` | Set up continuous web monitoring with comprehensive queries |
   | `/yutori:research` | Deep web research workflow (async, 5-10 min) |
   | `/yutori:browse` | Browser automation tasks |
   | `/yutori:competitor-watch` | Quick competitor monitoring template |
   | `/yutori:api-monitor` | API/changelog monitoring template |

   > **Already have the MCP server installed?** Remove it first to avoid duplicate configurations:
   > ```bash
   > claude mcp remove yutori -s user   # if installed at user scope
   > claude mcp remove yutori -s local  # if installed at local/project scope
   > ```

2. **MCP Only** (if you prefer not to use the plugin)

   ```bash
   claude mcp add --scope user yutori -- uvx yutori-mcp
   ```

   The server reads your API key from `~/.yutori/config.json` (set up via `uvx yutori-mcp login`).
</details>

<details>
<summary>Claude Desktop</summary>

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"]
    }
  }
}
```

The server reads your API key from `~/.yutori/config.json`.

For setup details, see the [Claude Desktop MCP install guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers).
</details>

<details>
<summary>Cursor</summary>

**Click the button to install:**

[<img src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Install in Cursor">](https://cursor.com/en/install-mcp?name=Yutori&config=eyJjb21tYW5kIjoidXZ4IHl1dG9yaS1tY3AifQ%3D%3D)

**Or install manually:**

Go to Cursor Settings → MCP → Add new MCP Server, then add:

```json
{
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"]
    }
  }
}
```

The server reads your API key from `~/.yutori/config.json`.

See the [Cursor MCP guide](https://cursor.com/docs/context/mcp) for setup details.
</details>

<details>
<summary>VS Code</summary>

**Click the button to install:**

[<img src="https://img.shields.io/badge/VS_Code-VS_Code?style=flat-square&label=Install%20Server&color=0098FF" alt="Install in VS Code">](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522yutori%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522yutori-mcp%2522%255D%257D) [<img alt="Install in VS Code Insiders" src="https://img.shields.io/badge/VS_Code_Insiders-VS_Code_Insiders?style=flat-square&label=Install%20Server&color=24bfa5">](https://insiders.vscode.dev/redirect?url=vscode-insiders%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522yutori%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522yutori-mcp%2522%255D%257D)

**Or install manually:**

```bash
code --add-mcp '{"name":"yutori","command":"uvx","args":["yutori-mcp"]}'
```

The server reads your API key from `~/.yutori/config.json`.
</details>

<details>
<summary>ChatGPT</summary>

Open ChatGPT Desktop and go to Settings -> Connectors -> MCP Servers -> Add server.

```json
{
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"]
    }
  }
}
```

The server reads your API key from `~/.yutori/config.json`.

For setup details, see the [OpenAI MCP guide](https://platform.openai.com/docs/mcp).
</details>

<details>
<summary>Codex</summary>

1. **MCP Server:**

   ```bash
   codex mcp add yutori -- uvx yutori-mcp
   ```

   Or add to `~/.codex/config.toml`:

   ```toml
   [mcp_servers.yutori]
   command = "uvx"
   args = ["yutori-mcp"]
   ```

   The server reads your API key from `~/.yutori/config.json`.

2. **Skills** (optional, for workflow guidance):

   Install skills using `$skill-installer` inside Codex:

   ```
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-scout
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-research
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-browse
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-competitor-watch
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-api-monitor
   ```

   Or manually copy skills to your user directory (use `-L` so symlinks are dereferenced and real files are copied):

   ```bash
   git clone https://github.com/yutori-ai/yutori-mcp /tmp/yutori-mcp
   cp -rL /tmp/yutori-mcp/.agents/skills/* ~/.agents/skills/
   ```

   Restart Codex after installing skills.

   | Skill | Command | Description |
   |-------|---------|-------------|
   | Scout | `$yutori-scout` | Set up continuous web monitoring |
   | Research | `$yutori-research` | Deep web research (async, 5-10 min) |
   | Browse | `$yutori-browse` | Browser automation with AI navigator |
   | Competitor Watch | `$yutori-competitor-watch` | Quick competitor monitoring template |
   | API Monitor | `$yutori-api-monitor` | API/changelog monitoring template |

   See the [Codex Skills docs](https://developers.openai.com/codex/skills/) for more on skills.
</details>

<details>
<summary>OpenClaw</summary>

Follow the **Quickstart** above:

1. Install skills and MCP for OpenClaw (and optionally other tools) via [skills.sh](https://skills.sh):
   ```bash
   npx skills add yutori-ai/yutori-mcp
   ```
   When prompted, choose which Yutori skills to install and select **OpenClaw** as the tool.

</details>

<details>
<summary>Gemini CLI</summary>

Add to `~/.gemini/settings.json`. If you already have `mcp` or `mcpServers`, merge these keys into your existing config:

```json
{
  "mcp": {
    "allowed": ["yutori"]
  },
  "mcpServers": {
    "yutori": {
      "command": "uvx",
      "args": ["yutori-mcp"]
    }
  }
}
```

The server reads your API key from `~/.yutori/config.json`.

Add `"yutori"` to `mcp.allowed` if you already list other MCPs there. For more details, see the [Gemini CLI MCP settings guide](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md#configure-the-mcp-server-in-settingsjson).
</details>

<details>
<summary>Run with pip</summary>

Install the package to run the MCP server (e.g. for custom or self-hosted setups):

```bash
pip install yutori-mcp
```
</details>

## Tools

Parameters, example requests/responses, and annotations for all MCP tools. See **[TOOLS.md](TOOLS.md)** for the full reference (Scout, Research, and Browsing tools).

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
yutori-mcp login    # authenticate (one-time)
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
