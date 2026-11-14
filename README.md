# patchitRIGHT

**patchitRIGHT** is a transactional code-editing MCP server. It edits files by AST symbol, line range, or exact match, transfers or slices content across workspace files, and validates syntax with automatic rollback on failure.

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
        "PYTHONPATH": "/path/to/patchitRIGHT/src",
        // "PATCHITRIGHT_SYNC_JCODEMUNCH": "false", // Optional: invoke jcodemunch-mcp CLI index-file if on PATH
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
| `patch_file` | Edit existing files by AST symbol, text block, guarded line range, line insertion, multi-file broadcast, or mixed atomic batch. | `target_file` / `target_files` / `target_glob` / `files`, `symbol_name`, `search_content`/`replace_content`, `start_line`/`end_line`, `expected_occurrences`, `source_file` |
| `write_file` | Create or overwrite files from inline text or `source_file` (whole-file copy/move, partial slice extraction, or binary copy). | `target_file`, `code_content` or `source_file`, `source_start_line`/`source_end_line`, `source_symbol`, `cut_source`/`delete_source`, `allow_overwrite` |
| `apply_last_dry_run` | Apply changes cached by a prior `dry_run: true` call if involved files are unchanged on disk. | `run_id` |
| `patchitright_guide` | Return current usage recipes and language style rules. | `file_type` |

---

## AST-Scoped & Source-Sliced Operations

Edit functions and classes by symbol name or extract code directly from another workspace file without regenerating text:

* **Symbol replacement**: Pass `symbol_name` with `symbol_scope: "body"` or `"full"` and `replace_content` (or a `source_file` slice).
* **Cross-file copy, move, and slice extraction**: Pass `source_file` to `write_file` or `patch_file` with `source_start_line`/`source_end_line` (plus optional anchor guards) or `source_symbol`. Add `delete_source: true` to move a whole file, or `cut_source: true` to remove extracted lines from the source atomically.
* **Broadcasts & mixed batches**: Use `target_files` or `target_glob` (with optional `skip_unmatched: true`) to apply one edit across many files, or `files` for mixed `patch`, `write`, and `delete` operations in one transaction.
* **Guarded line ranges**: Pass `start_line` and `end_line` with `start_anchor`, `end_anchor`, or `line_filter` (omitting `search_content`) for targeted replacement or line removal (`replace_content: ""`).

---

## Line-Based Insertion

* `insert_line: N`: Inserts code directly above line `N` (`1` for top of file, `-1` for end of file).
* **Indentation**: Automatically matches indentation of surrounding context.

---

## Validation & Safety Guardrails

* **Pre-write Syntax Checking**: Validates syntax before writing (Python `ast`, JS/TS via Biome/`tsc`, JSON, TOML, YAML) on both destinations and `cut_source` source files.
* **Strict Occurrence Assertions**: Rejects 0 or ambiguous multi-matches by default; use `expected_occurrences: N` to enforce an exact match count (`allow_multiple` implied when omitted).
* **Atomic Multi-file & Copy/Cut Transactions**: `files`, `target_files`, `target_glob`, `cut_source`, and `delete_source` execute transactionally with optimistic locking and full rollback on failure.

---

## Environment Flags

* **`PATCHITRIGHT_IGNORE_WARNINGS`** (`string`, e.g. `"format"`, `"format,lint"`, `"all"`): Filters diagnostics to prevent prompt token pollution:
  * `format` *(Recommended)*: Suppresses formatting diffs and tab vs space warnings.
  * `lint`: Suppresses code smell and linter diagnostics (Ruff / Biome).
  * `insertion`: Suppresses auto-indentation and clamping notices during `insert_line`.
  * `symbol`: Suppresses symbol omission alerts.
  * `all`: Suppresses all warning diagnostics.
* **`PATCHITRIGHT_DEFAULT_TIMEOUT`** (`float`, default: `10.0`): Server execution timeout limit in seconds (`set_timeout: -1` disables timeout).
* **`PATCHITRIGHT_SYNC_JCODEMUNCH`** (`true`/`false`, default: `false`): Optional CLI hook that runs `jcodemunch-mcp index-file` in the background after file writes when the `jcodemunch-mcp` executable is installed on `PATH` (silent no-op otherwise).
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
