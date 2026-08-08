# Brave MCP & Registry Setup Guide

> Auxiliary domain sub-document for [brave-browsing](SKILL.md). Read via `view_file` when configuring new MCP environments or persistent Registry launch parameters.

## Quick Start Configuration

Ensure `mcp_config.json` (located at `~/.gemini/config/mcp_config.json`) contains the `--browserUrl` parameter:

```json
{
  "mcpServers": {
    "chrome-devtools-mcp": {
      "command": "npx",
      "args": [
        "-y",
        "chrome-devtools-mcp@latest",
        "--browserUrl",
        "http://127.0.0.1:9222"
      ]
    }
  }
}
```

## Setup Modes

### Mode 1: Remote Debugging Port (--browserUrl) - Recommended
- **Command:** `brave.exe --remote-debugging-port=9222 --remote-allow-origins=http://127.0.0.1:9222,http://localhost:9222`
- **MCP Config:** `--browserUrl http://127.0.0.1:9222`

### Mode 2: System-Wide Registry Automation (Persistent)
> [!CAUTION]
> **Tier 3 Execution Gate:** Modifying Registry keys is a system-wide modification. Agents MUST present the exact plan and obtain EXPLICIT USER APPROVAL before executing any Registry commands.

- **Target Registry Keys:**
  1. `HKCU:\Software\Classes\BraveHTML\shell\open\command`
  2. `HKCU:\Software\Classes\http\shell\open\command`
  3. `HKCU:\Software\Classes\https\shell\open\command`
- **Value:** `"C:\Users\<username>\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222 --remote-allow-origins=http://127.0.0.1:9222,http://localhost:9222 -- "%1"`

#### Restore Default:
- **Value:** `"C:\Users\<username>\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe" -- "%1"`
