# patchitRIGHT

**patchitRIGHT** is a high-performance, single-tool, AST-bounded secure code-writing and file-manipulation MCP server. 

It acts as a safe, surgical companion for AI coding agents and developers. By pairing with [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) AST engines, it allows agents to mutate functions/classes directly without re-passing large code blocks, saving substantial prompt tokens while enforcing syntax correctness.

---

## Quick Setup (MCP Client)

Add the following configuration to your MCP client configuration file (e.g., `claude_desktop_config.json`):

```jsonc
{
  "mcpServers": {
    "patchitright": {
      "command": "python",
      "args": [
        "-m",
        "patchitright_mcp.server"
      ],
      "env": {
        "PYTHONPATH": "d:/Projects/patchitRIGHT/src",
        "PATCHITRIGHT_SYNC_JCODEMUNCH": "true",
        "PATCHITRIGHT_IGNORE_WARNINGS": "format" // Recommended: ignore verbose formatter diffs (tabs vs spaces) by default
      }
    }
  }
}
```

---

## Provided Tools

| Tool | Core Functionality | Key Inputs |
| :--- | :--- | :--- |
| `patch_file` | Surgical code edits (AST symbol mutation, replacements, line insertions, unified diffs) across single or multiple files. | `target_file` (or `files`), `symbol_name`, `symbol_scope`, `search_content`/`replace_content`, `replacements` |
| `write_file` | Create a new file or overwrite an existing file with automatic syntax validation. | `target_file`, `code_content`, `allow_overwrite` |

---

## AST-Scoped Replacements (Zero Token Waste)

Unlike traditional search-and-replace tools that force AI agents to re-send entire 100+ line functions inside `search_content`, `patchitRIGHT` leverages AST bounds (`symbol_scope: "body"` or `"full"`) via `symbol_name`:

* **Eliminate Token Waste**: Pass **only the new body code** in `replace_content`. No need to repeat existing target code.
* **Smart Indentation Normalization**: Automatically detects the target function's indentation and re-indents `replace_content` to match perfectly.
* **Multibyte Character Safety**: Accurately maps tree-sitter byte-offsets to Python characters, preventing encoding corruption on files with emojis (`🐛`) or UTF-8 characters.
* **Upfront Resolution**: Resolves multi-patch queues bottom-up to prevent line-drift errors during multi-symbol edits.

---

## Line-Based Insertion

Insert code cleanly directly above a target line without requiring exact text matching:

* **Line Indexing**: Pass `insert_line: N` to insert directly above line N (pushing line N down). Pass `1` for top of file, or `-1` for end of file (EOF).
* **Auto-Indentation**: Automatically scans reference lines and normalizes pre-indented blocks.

---

## Practical Safeguards & Integrity Guards

* **Integrated Syntax & Lint Checks**: Validates syntax before writing (Python `ast`, JS/TS via Biome/`tsc`, JSON, TOML, YAML) and attaches noise-filtered linter warnings (Ruff / Biome).
* **Original-Content Syntax Guard**: Skips validation if the target file was *already broken before editing*, preventing legacy pre-existing syntax errors from blocking your patches.
* **Atomic Rollback & Recovery**: Multi-file patches operate transactionally (all-or-nothing). Automatic backup files guard against unexpected crashes mid-write.

---

## Standalone vs. jCodeMunch Mode

`patchitRIGHT` works out-of-the-box in standalone mode or enriched with `jCodeMunch`:

| Feature | jCodeMunch Mode (Indexed) | Standalone Mode (Zero-Dependency) |
| :--- | :--- | :--- |
| **Dependencies** | Requires `jcodemunch-mcp` | **None** (pure Python package) |
| **AST Symbol Scoping (`symbol_name`)** | **Supported** (O(1) lookup of class/function bounds) | Manual line-range (`start_line`/`end_line`) |
| **Exact Search & Replace / Multi-patch** | Supported | Supported |
| **Syntax Validation & Rollbacks** | Supported | Supported |
| **Realtime Index Sync (`PATCHITRIGHT_SYNC_JCODEMUNCH`)** | Auto-indexes `jCodeMunch` on write | N/A |
| **Ideal For** | Complex agentic workflows & symbol awareness | Lightweight CI/CD & zero-setup environments |

---

## Environment Flags & Configuration

* **`set_timeout`** (`number`, default: `10.0`): Overrides tool execution timeout in seconds. Set to `-1` to disable timeout completely (recommended during internal server self-modifications).
* **`PATCHITRIGHT_DEFAULT_TIMEOUT`** (`float`, default: `10.0`): Configures server-wide default execution timeout limit.
* **`PATCHITRIGHT_IGNORE_WARNINGS`** (`string`, e.g. `"format"`, `"format,lint"`, `"all"`): Filters diagnostics by category to prevent prompt token pollution:
  * `format` *(Recommended default)*: Suppresses formatting/whitespace diffs and tab vs space warnings.
  * `lint`: Suppresses code smell and linter diagnostics from Ruff / Biome.
  * `insertion`: Suppresses auto-indentation and clamping notices during `insert_line`.
  * `symbol`: Suppresses symbol omission alerts.
  * `all` (or `*`, `1`, `true`): Suppresses all warning diagnostics.
* **`PATCHITRIGHT_SYNC_JCODEMUNCH`** (`true` / `false`, default: `false`): Triggers immediate background indexing in `jCodeMunch` after file modifications.
* **`PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION`** (`true` / `false`, default: `false`): Exposes `bypass_validation` parameter in schemas for emergency override.
* **`PATCHITRIGHT_SHOW_LEGACY`** (`true` / `false`, default: `false`): Exposes legacy `batch_patch_files` tool.

---

## Development & Testing

```bash
# Run tests in parallel
uv run pytest

# Debug single-threaded
uv run pytest -n 0
```

---

## License

Distributed under the **MIT License**. Complies with jCodeMunch dual-use terms when integrated with jCodeMunch-MCP.
