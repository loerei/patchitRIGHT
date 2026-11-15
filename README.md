# patchitRIGHT

**patchitRIGHT** is a transactional file-editing MCP server for coding agents. It edits files by function/class name, line numbers, or text block, copies or moves slices across files on disk without re-typing code, and checks syntax before saving with automatic rollback on failure.

---

## How It Compares

Since native `edit_file` tools are already pretty mature, editing files is one of the last things people talk about. This repo, sure, sounds weird.

But there is still an awkward gap that forces agents to either:
- do things manually and be wasteful, or
- run scripts and be reckless.

The idea was simple: keep the wins from editing via scripts, without breaking files. This MCP is how we do it.

| Capability | Native Agent Tools (`edit_file`, `write_file`) | Ad-hoc Shell Scripts (`python -c`, `sed`, `node -e`) | `patchitRIGHT` |
| :--- | :--- | :--- | :--- |
| **How it edits** | Replaces an exact contiguous text block in one file, or writes a full file from scratch. | Runs custom one-off regexes, string splits, or line slices directly from the terminal. | Replaces by function/class name, line numbers, or text block across one or multiple files. |
| **Ways to save output tokens** | Limited. Can replace small blocks in place, but moving or copying code requires the model to re-type every line into the tool call. | High. Can copy, slice, or generate repetitive blocks programmatically without the model re-typing code. | High. Copies or moves whole files, line ranges, or specific functions/classes directly on disk without re-typing code. |
| **Editing flexibility** | Rigid on large edits. If a search block appears twice or indentation drifts slightly, the edit fails or replaces every match blindly. | Unlimited, at the cost of writing and debugging custom script logic on the fly. | Targeted. Supports replacing just a function body, guarding line numbers with text anchors, or enforcing exact match counts. |
| **Safety against broken files** | Writes directly to disk. No check on whether the edit broke syntax; multi-file changes happen one by one with no rollback if a later edit fails. | None to unlimited. Writing safer scripts requires deeper, more complex logic, which directly diminishes the token win of a one-off patch (instead of writing code, the agent is now writing a complex script). When a script fails, the agent must either rewrite the script itself (if it failed safely) or also mend the broken source files (if it ruined them). | Checks that the code still parses cleanly before saving. Multi-file edits run as a single all-or-nothing update that rolls back if any file fails. |

---

## Benchmark

Tested on an identical feature implementation task (extracting save-file schemas across game engines and writing a 22-case unit test suite in a TypeScript repository):

| Approach | Output Tokens Generated | vs. Native Tools | Peak Context | Tool / Script Errors | Test Suite Result |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Native Agent Tools Only** | 95,801 | Baseline | 118,208 | 0 | 100% pass |
| **Native Tools + Ad-hoc Scripts** | 70,013 | -26.9% | 113,249 | 0 | 100% pass |
| **`patchitRIGHT`** | **74,801** | **-21.9%** | **115,837** | **0 / 27 calls** | **100% pass** |

`patchitRIGHT` cuts output token generation by ~1/5 compared to native edit tools by avoiding full-file rewrites and oversized context blocks. It usually uses a little more tokens than unrestricted shell scripting in the happy path, a small fee to keep edits structured, syntax-checked, and protected from broken one-off patching scripts.

---

## Quick Setup

### `uvx` (Recommended, zero install)

**Claude Code:**

```bash
claude mcp add patchitright -- uvx patchitright-mcp
```

**MCP client JSON** (Claude Desktop, Cursor, Windsurf, VS Code):

```jsonc
{
  "mcpServers": {
    "patchitright": {
      "command": "uvx",
      "args": ["patchitright-mcp"],
      "env": {
        "PATCHITRIGHT_IGNORE_WARNINGS": "format" // Recommended: ignore verbose formatter diffs (tabs vs spaces)
      }
    }
  }
}
```

### `pipx` or `pip`

```bash
pipx install patchitright-mcp
# or
pip install patchitright-mcp
```

Then point `"command"` to `"patchitright-mcp"` (with `"args": []`).

### From source (local checkout)

```jsonc
{
  "mcpServers": {
    "patchitright": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/patchitRIGHT", "patchitright-mcp"],
      "env": {
        "PATCHITRIGHT_IGNORE_WARNINGS": "format" // Recommended: ignore verbose formatter diffs (tabs vs spaces) by default
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
| `patch_file` | Edit existing files by symbol name, text block, guarded line range, line insertion, multi-file broadcast, or mixed atomic batch. | `target_file` / `target_files` / `target_glob` / `files`, `symbol_name`, `search_content`/`replace_content`, `start_line`/`end_line`, `expected_occurrences`, `source_file` |
| `write_file` | Create or overwrite files from inline text, atomic multi-file batches, or `source_file` (whole-file copy/move, partial slice extraction, or binary copy). | `target_file` (or `path`) / `files`, `code_content` (or `content`) or `source_file`, `source_start_line`/`source_end_line`, `source_symbol`, `cut_source`/`delete_source`, `allow_overwrite` (or `overwrite`) |
| `apply_last_dry_run` | Apply changes cached by a prior `dry_run: true` call if involved files are unchanged on disk. | `run_id` |
| `patchitright_guide` | Return current usage recipes and language style rules. | `file_type` |

---

## Symbol-Scoped & Source-Sliced Operations

Edit functions and classes by name or extract code directly from another workspace file without regenerating text:

* **Symbol replacement**: Pass `symbol_name` with `symbol_scope: "body"` or `"full"` and `replace_content` (or a `source_file` slice).
* **Cross-file copy, move, and slice extraction**: Pass `source_file` to `write_file` or `patch_file` with `source_start_line`/`source_end_line` (plus optional anchor guards) or `source_symbol`. Add `delete_source: true` to move a whole file, or `cut_source: true` to remove extracted lines from the source atomically.
* **Broadcasts & mixed batches**: Use `target_files` or `target_glob` (with optional `skip_unmatched: true`) to apply one edit across many files, or `files` for batch operations in one transaction.
* **Guarded line ranges**: Pass `start_line` and `end_line` with `start_anchor`, `end_anchor`, or `line_filter` (omitting `search_content`) for targeted replacement or line removal (`replace_content: ""`).

---

## Line-Based Insertion

* `insert_line: N`: Inserts code directly above line `N` (`1` for top of file, `-1` for end of file).
* **Indentation**: Automatically matches indentation of surrounding context.

---

## Validation & Safety Guardrails

* **Pre-write Syntax Checking**: Validates syntax before writing (Python `ast` and bundled `ruff`, JSON, TOML, YAML; optional JS/TS checking when `biome` or `tsc` is on `PATH`) on both destinations and `cut_source` source files.
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
