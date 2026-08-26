# Yutori MCP

MCP tools and workflow skills for building agents that monitor, research, browse, and operate computers with [Yutori](https://yutori.com/api).

You can use it with Claude Code, Codex, Cursor, VS Code, ChatGPT, OpenClaw, and other MCP hosts.

## Features

**Capabilities:**
- **Scouting** — Monitor the web continuously for anything you care about at a desired frequency
- **Research** — Run one-time deep web research tasks
- **Browsing** — Automate websites with an AI navigator
- **Computer use** — On macOS 15+, run tasks on the visible foreground desktop

### macOS Computer Use

On macOS, Yutori MCP exposes `run_computer_use_task` for foreground desktop automation.
The tool controls the visible desktop, so do not touch the Mac while a task is running.

The runtime is the Python CUA harness from the `yutori` SDK: `yutori-mcp` pins `yutori==0.9.2`
and calls the SDK's `N2ComputerAgent` with `yutori.navigator.macos.MacOSComputer`. There is no
TypeScript bundle, Node executable, or alternate harness. The pinned contract is:

| Component | Value |
|-----------|-------|
| MCP package | `yutori-mcp==0.5.1` |
| Model | Navigator n2 |
| Tool set | `computer_use_tools-20260815` |
| Desktop driver | `cua-driver==0.19.3` |
| SDK runtime | `yutori[macos]==0.9.2` |

Computer-use dependencies:

| Dependency | Required for | Installed by |
|------------|--------------|--------------|
| macOS 15+ | Local desktop capture, input, and native overlay/recording path | You |
| Python 3.10+ | `yutori-mcp` and the SDK-owned CUA harness | `uvx` can manage this |
| `uv` / `uvx` | Running `yutori-mcp` without a manual virtualenv | You |
| Yutori API key with computer-use access | Model calls from the local harness | `uvx yutori-mcp login` stores it |
| `yutori==0.9.2` | `N2ComputerAgent` and `MacOSComputer` runtime | `yutori-mcp` dependency |
| `cua-driver==0.19.3` and `CuaDriver.app` | Native screenshot capture and desktop actions | `uvx yutori-mcp computer-use setup` |
| Screen Recording + Accessibility permissions | Letting CuaDriver see and operate the desktop | Requested by `computer-use setup` |
| Xcode Command Line Tools | Optional reasoning overlay build | You; `doctor` reports if missing |

Authenticate, install the local Mac runtime, and verify it:

```bash
uvx yutori-mcp login
uvx yutori-mcp computer-use setup
uvx yutori-mcp computer-use doctor
uvx yutori-mcp computer-use smoke
```

`setup` downloads the pinned CuaDriver installer, checks its SHA-256, installs and starts
`CuaDriver.app`, requests Screen Recording and Accessibility permissions, and prepares the
optional native reasoning overlay. `doctor` verifies the pinned SDK wheel/provenance, the
driver install, permissions, overlay cache, and Yutori API access before a task can run.

`computer-use run` executes one custom task from the terminal — the same run the MCP tool
performs, with per-action progress printed as it happens:

```bash
uvx yutori-mcp computer-use run "In Calculator, compute 17 * 23 and report the result." --app Calculator
```

To run through an MCP client, use the `run_computer_use_task` tool with a task, optional
target app, optional start URL, time limit, and step limit. Only one task can control a Mac
at a time.

For longer runs, prefer compacting or summarizing older screenshots and tool results so the
conversation stays within `max_context_len`; do not rely on an artificial 100-step cap.

To expose the MCP tool to a client, run the default server:

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

**Workflow skills** (for clients that support slash commands):
- [`/yutori-scout`](skills/01-scout/SKILL.md) — Set up continuous web monitoring
- [`/yutori-research`](skills/02-research/SKILL.md) — Deep web research (async, 5–10 min)
- [`/yutori-browse`](skills/03-browse/SKILL.md) — Browser automation
- [`/yutori-computer-use`](skills/06-computer-use/SKILL.md) — Local Mac desktop automation
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
   | `/yutori-scout` | Set up continuous web monitoring with comprehensive queries |
   | `/yutori-research` | Deep web research workflow (async, 5-10 min) |
   | `/yutori-browse` | Browser automation tasks |
   | `/yutori-computer-use` | Local Mac desktop automation |
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
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-scout
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-research
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-browse
   $skill-installer install https://github.com/yutori-ai/yutori-mcp/tree/main/.agents/skills/yutori-computer-use
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
   | Scout | `$yutori-scout` | Set up continuous web monitoring |
   | Research | `$yutori-research` | Deep web research (async, 5-10 min) |
   | Browse | `$yutori-browse` | Browser automation with AI navigator |
   | Computer Use | `$yutori-computer-use` | Local Mac desktop automation |
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

See [TOOLS.md](TOOLS.md) for the full tool reference — Scout, Research, and Browsing tools with parameters, examples, and response formats.

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

### Debugging with MCP Inspector

```bash
npx @modelcontextprotocol/inspector yutori-mcp
```

## API Documentation

For full API documentation, visit [docs.yutori.com](https://docs.yutori.com).

## License

Apache 2.0

## Computer-use SDK harness and runtime dependency

The SDK-owned `yutori.navigator.N2ComputerAgent` and `MacOSComputer` provide the complete
runtime. The MCP package pins SDK 0.9.2 and verifies the installed files against the immutable
published wheel plus its packaged provenance during `computer-use doctor`. There is no
TypeScript bundle, Node executable, alternate harness, or private dependency access.

SDK contributors may deliberately test an editable 0.9.2 checkout by setting
`YUTORI_MCP_ALLOW_EDITABLE_SDK=1`; without that explicit override, doctor rejects editable or
modified SDK installations.
