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
        "PATCHITRIGHT_IGNORE_WARNINGS": "format", // Recommended: ignore verbose formatter diffs (tabs vs spaces) by default
        // "PATCHITRIGHT_DEFAULT_TIMEOUT": "10.0", // Execution timeout limit in seconds (-1 to disable)
        // "PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION": "false", // Expose emergency bypass_validation in schemas
        // "PATCHITRIGHT_SHOW_LEGACY": "false" // Expose legacy batch_patch_files tool
      }
    }
  }
}
```

---

## Tools

| Tool | Purpose | Key Parameters |
| :--- | :--- | :--- |
| `patch_file` | Modify existing files via AST symbol mutation, text/guarded-range replacement, line insertion, broadcast (`target_files`/`target_glob`), or mixed atomic `files` batches. | `target_file` / `target_files` / `target_glob` / `files`, `symbol_name`, `search_content`/`replace_content`, `start_anchor`/`end_anchor`, `expected_occurrences`, `skip_unmatched`, `source_file` slice |
| `write_file` | Create or overwrite files from inline text or `source_file` (whole-file copy/move, partial line/symbol slice extraction, binary copy, or copy-then-patch). | `target_file`, `code_content` or `source_file` (`source_start_line`/`source_end_line`, `source_symbol`, `cut_source`, `delete_source`), `allow_overwrite` |

---

## AST-Scoped & Source-Sliced Operations

Edit functions or classes directly without repeating existing code in `search_content`, or transfer code slices directly from another file:

* **Body / Full replacement**: Pass `symbol_name: "my_func"` with `symbol_scope: "body"` or `"full"` and `replace_content` (or a `source_file` slice).
* **Cross-file slice extraction (`write_file` & `patch_file`)**: Pass `source_file` with `source_start_line`/`source_end_line` (plus optional `source_start_anchor`/`source_end_anchor`) or `source_symbol` to create/overwrite a target or supply `replace_content`/`insert_content` without regenerating lines. Use `delete_source: true` to move whole files or `cut_source: true` to atomically cut the extracted lines from the source file.
* **Broadcast & Mixed Batches**: Use `target_files` or `target_glob` (with optional `skip_unmatched: true`) to apply the same change across multiple files, or `files` with per-item `action: "patch" | "write" | "delete"` and shared top-level defaults.
* **Auto-indentation & Guarded Ranges**: Target indentation is applied automatically. Guarded line-range replacement (`start_line` + `end_line` + `start_anchor`/`end_anchor`/`line_filter` without `search_content`) allows targeted replacement or clean deletion (`replace_content: ""`).

---

## Line-Based Insertion

Insert code above a target line index:

* `insert_line: N`: Inserts code directly above line N. Pass `1` for top of file, `-1` for end of file (EOF).
* **Indentation**: Automatically matches indentation of surrounding context.

---

## Validation & Safety Guardrails

* **Pre-write Syntax Checking**: Validates syntax before writing (Python `ast`, JS/TS via Biome/`tsc`, JSON, TOML, YAML) on both destinations and `cut_source` source files.
* **Strict Occurrence Assertions**: Rejects 0 or ambiguous multi-matches by default; use `expected_occurrences: N` to enforce an exact match count (`allow_multiple` implied when omitted).
* **Atomic Multi-file & Copy/Cut Transactions**: `files`, `target_files`, `target_glob`, `cut_source`, and `delete_source` execute transactionally with optimistic locking and full rollback on failure.

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
