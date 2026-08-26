# Yutori MCP

MCP tools and workflow skills for building agents that operate computers, browse, research, and monitor the web with [Yutori](https://yutori.com/api).

You can use it with Claude Code, Codex, Cursor, VS Code, ChatGPT, OpenClaw, and other MCP hosts.

## Features

**Capabilities:**
- **Computer use** — Operate apps on your Mac (macOS 15+)
- **Browsing** — Automate websites with an AI navigator
- **Research** — Run one-time deep web research tasks
- **Scouting** — Monitor the web continuously for anything you care about at a desired frequency

**Workflow skills** (for clients that support slash commands):
- [`/yutori-computer-use`](skills/06-computer-use/SKILL.md) — Local Mac desktop automation
- [`/yutori-browse`](skills/03-browse/SKILL.md) — Browser automation
- [`/yutori-research`](skills/02-research/SKILL.md) — Deep web research (async, 5–10 min)
- [`/yutori-scout`](skills/01-scout/SKILL.md) — Set up continuous web monitoring
- [`/yutori-competitor-watch`](skills/04-competitor-watch/SKILL.md) — Competitor monitoring template
- [`/yutori-api-monitor`](skills/05-api-monitor/SKILL.md) — API/changelog monitoring template

## Installation

<details>
<summary>Requirements</summary>

If you don't already have `uv` installed, install it (it includes `uvx`):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or with Homebrew:

```bash
brew install uv
```

Python 3.10 or higher is required (`uv` manages this automatically for most installs).

For the quickstart below, Node.js is also required (for `npx`).
</details>

### AI agent install (recommended)

Paste this into Claude Code, Codex, Cursor, Windsurf, or another coding agent:

```text
Use https://yutori.com/api/llms.txt and set up Yutori for me.
```

### Manual quick install

![MCP server installation](assets/mcp-server-install.gif)

1. Run in terminal:

    ```bash
    uvx yutori-mcp login
    ```
    This will open Yutori Platform in your browser and save your API key locally.

    <details>
    <summary>Or, manually add your API key</summary>

    Go to (https://platform.yutori.com) and add your key to the config file:
    ```bash
    mkdir -p ~/.yutori
    cat > ~/.yutori/config.json << 'EOF'
    {"api_key": "yt-your-api-key"}
    EOF
    ```
    </details>




2. Install MCP using [add-mcp](https://neon.com/blog/add-mcp) (requires Node.js):
   ```
   npx add-mcp "uvx yutori-mcp"
   ```

    Pick the clients you want to configure.

3. Install workflow skills using [skills.sh](https://skills.sh) (requires Node.js):
   ```
   npx skills add yutori-ai/yutori-mcp -g
   ```

    Adds slash-command shortcuts like `/yutori-scout`, `/yutori-research`, and more.

    `-g` installs them at user scope. Omit `-g` if you want a project-local install instead.

   <details>
   <summary>To list or remove skills later:</summary>

   ```bash
   npx skills ls -g
   npx skills remove -g yutori-login
   ```
   </details>

4. Restart the tool you are using.


### Manual per-client install

<details>
<summary>Claude Code</summary>

1. **Plugin (Recommended)** - Includes MCP tools + workflow skills

   Type these commands in Claude Code's input (not in a terminal):
   ```
   /plugin marketplace add yutori-ai/yutori-mcp
   /plugin install yutori@yutori-plugins
   ```

   This installs both the MCP tools and workflow skills:

   | Skill | Description |
   |-------|-------------|
   | `/yutori-computer-use` | Local Mac desktop automation |
   | `/yutori-browse` | Browser automation tasks |
   | `/yutori-research` | Deep web research workflow (async, 5-10 min) |
   | `/yutori-scout` | Set up continuous web monitoring with comprehensive queries |
   | `/yutori-competitor-watch` | Quick competitor monitoring template |
   | `/yutori-api-monitor` | API/changelog monitoring template |

   > **Already have the MCP server installed?** Remove it first to avoid duplicate configurations:
   > ```bash
   > claude mcp remove yutori -s user   # if installed at user scope
   > claude mcp remove yutori -s local  # if installed at local/project scope
   > ```

   To uninstall the plugin later:
   ```
   /plugin uninstall yutori@yutori-plugins -s user
   ```

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
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-computer-use
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-browse
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-research
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-scout
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-competitor-watch
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-api-monitor
   ```

   Or manually copy skills to your user directory (use `-L` so symlinks are dereferenced and real files are copied):

   ```bash
   git clone https://github.com/yutori-ai/yutori-mcp /tmp/yutori-mcp
   cp -rL /tmp/yutori-mcp/.agents/skills/* ~/.agents/skills/
   ```

   To uninstall manually copied skills, delete the matching directories from `~/.agents/skills/`. When updating this way, remove old Yutori skill directories first, since `cp -rL` will not delete renamed or removed skills.

   Restart Codex after installing skills.

   | Skill | Command | Description |
   |-------|---------|-------------|
   | Computer Use | `$yutori-computer-use` | Local Mac desktop automation |
   | Browse | `$yutori-browse` | Browser automation with AI navigator |
   | Research | `$yutori-research` | Deep web research (async, 5-10 min) |
   | Scout | `$yutori-scout` | Set up continuous web monitoring |
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

### macOS computer use

Optional, macOS 15+ only. Computer use operates the visible desktop, so it needs a local driver
and system permissions on top of the install above:

```bash
uvx yutori-mcp computer-use setup    # installs CuaDriver.app, requests Screen Recording + Accessibility
```

`setup` finishes by running the readiness checks and reports anything still missing. Re-run
those checks any time with `uvx yutori-mcp computer-use doctor`.

<details>
<summary>Run a task from the terminal</summary>

```bash
uvx yutori-mcp computer-use smoke
uvx yutori-mcp computer-use run "In Calculator, compute 17 * 23 and report the result." --app Calculator
```

`smoke` is an end-to-end check: it types into Calculator to confirm the permissions took
effect, then has the agent compute 9 * 9 in Calculator. `run` does whatever task you
give it, printing each action as the agent takes it.
</details>

The harness in this repository is minimal: it drives the foreground desktop one task at a time,
no background runs, no multiplexing. For scalable sandbox runs, see
[n2 on Daytona](https://docs.yutori.com/reference/n2-daytona).

## Tools

See [TOOLS.md](TOOLS.md) for the full tool reference — computer use, Browsing, Research, and Scout tools with parameters, examples, and response formats.

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
yutori-mcp          # run the server (or: python -m yutori_mcp.server)
```

### Computer-use runtime

`computer-use doctor` verifies the pinned `yutori` SDK install against the published wheel.
SDK contributors testing an editable checkout can override that with
`YUTORI_MCP_ALLOW_EDITABLE_SDK=1`.

### Debugging with MCP Inspector

```bash
npx @modelcontextprotocol/inspector yutori-mcp
```

## API Documentation

For full API documentation, visit [docs.yutori.com](https://docs.yutori.com).

## License

Apache 2.0
