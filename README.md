# patchitRIGHT

**patchitRIGHT** is an AST-bounded code-editing MCP server. It edits functions and classes directly by symbol name, inserts code by line index, and validates syntax before writing.

---

## Quick Setup

Add the following to your MCP client configuration (e.g. `claude_desktop_config.json`):

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

## Tools

| Tool | Purpose | Key Parameters |
| :--- | :--- | :--- |
| `patch_file` | Modify existing files via AST symbol mutation, text replacement, line insertion, or multi-file batches. | `target_file` (or `files`), `symbol_name`, `symbol_scope`, `search_content`/`replace_content`, `replacements`, `insert_line` |
| `write_file` | Create a new file or fully replace file content with syntax validation. | `target_file`, `code_content`, `allow_overwrite` |

---

## AST-Scoped Replacements

Edit functions or classes directly without repeating existing code in `search_content`:

* **Body replacement**: Pass `symbol_name: "my_func"` and `symbol_scope: "body"` with the new body in `replace_content`.
* **Full replacement**: Pass `symbol_scope: "full"` to replace signature, decorators, and body.
* **Auto-indentation**: Target indentation is detected and applied to `replace_content` automatically.
* **Multi-replacement**: Non-contiguous edits in `replacements` are resolved bottom-up to prevent line-drift.

---

## Line-Based Insertion

Insert code above a target line index:

* `insert_line: N`: Inserts code directly above line N. Pass `1` for top of file, `-1` for end of file (EOF).
* **Indentation**: Automatically matches indentation of surrounding context.

---

## Validation & Safety Guardrails

* **Pre-write Syntax Checking**: Validates syntax before writing (Python `ast`, JS/TS via Biome/`tsc`, JSON, TOML, YAML). Blocks write on syntax errors.
* **Pre-existing Error Tolerance**: Skips syntax blocking if the file was already invalid before editing.
* **Atomic Multi-file Writes**: Multi-file edits in `files` apply transactionally (all-or-nothing rollback on failure).

---

## Standalone vs. jCodeMunch Mode

| Feature | jCodeMunch Mode | Standalone Mode |
| :--- | :--- | :--- |
| **Dependencies** | `jcodemunch-mcp` | None (pure Python) |
| **AST Symbol Scoping (`symbol_name`)** | O(1) symbol lookup | Line range (`start_line`/`end_line`) |
| **Search-and-Replace / Line Insertion** | Supported | Supported |
| **Syntax Validation & Rollback** | Supported | Supported |
| **Realtime Index Sync** | Auto-indexes on write | N/A |

---

## Environment Flags

* **`PATCHITRIGHT_IGNORE_WARNINGS`** (`string`, e.g. `"format"`, `"format,lint"`, `"all"`): Filters diagnostics to prevent prompt token pollution:
  * `format` *(Recommended)*: Suppresses formatting diffs and tab vs space warnings.
  * `lint`: Suppresses code smell and linter diagnostics (Ruff / Biome).
  * `insertion`: Suppresses auto-indentation and clamping notices during `insert_line`.
  * `symbol`: Suppresses symbol omission alerts.
  * `all`: Suppresses all warning diagnostics.
* **`PATCHITRIGHT_DEFAULT_TIMEOUT`** (`float`, default: `10.0`): Server execution timeout limit in seconds (`set_timeout: -1` disables timeout).
* **`PATCHITRIGHT_SYNC_JCODEMUNCH`** (`true`/`false`, default: `false`): Triggers background re-indexing in `jCodeMunch` after file writes.
* **`PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION`** (`true`/`false`, default: `false`): Exposes `bypass_validation` parameter in schemas.
* **`PATCHITRIGHT_SHOW_LEGACY`** (`true`/`false`, default: `false`): Exposes legacy `batch_patch_files` tool.

---

## Development & Testing

```bash
# Run test suite
uv run pytest

# Run single-threaded
uv run pytest -n 0
```

---

## License

Distributed under the **MIT License**.
