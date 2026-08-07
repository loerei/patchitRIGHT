# patchitRIGHT

**patchitRIGHT** is a high-performance, single-tool, AST-bounded secure code-writing and file-manipulation MCP server. 

It acts as a safe, surgical companion for AI coding agents and developers. It leverages [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) index engines to resolve symbol ranges, validates syntax dynamically, and runs integrated linting prior to writing changes to disk.

---

## 🚀 Quick Setup (MCP Client)

Add the following configuration to your MCP client configuration file (e.g., `claude_desktop_config.json`):

```json
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
        "PATCHITRIGHT_SYNC_JCODEMUNCH": "true"
      }
    }
  }
}
```

---

## ⚙️ Advanced Configuration & Integration

### jCodeMunch Index Synchronization
* **`PATCHITRIGHT_SYNC_JCODEMUNCH`** (`true` / `false`, default: `false`):
  When enabled, `patchitRIGHT` automatically triggers a background thread to call `jCodeMunch`'s file indexer (`index-file`) immediately after writing a change. This keeps the AST index in sync in real-time, making subsequent search or impact queries instantly consistent.

### Exposing Legacy Tools
* **`PATCHITRIGHT_SHOW_LEGACY`** (`true` / `false`, default: `false`):
  Exposes the legacy `batch_patch_files` tool in the MCP schema for backward compatibility. Multi-file edits are now natively handled inside `patch_file` via the `files` array.

### Exposing the Bypass Validation Flag
* **`PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION`** (`true` / `false`, default: `false`):
  By default, the `bypass_validation` tool parameter is hidden from the MCP schema to prevent AI agents from bypassing syntax/lint checks to escape code-correctness guards. 
  If set to `true`, the `bypass_validation` parameter will be exposed in the schemas for `patch_file` and `write_file`.
  
  *Note:* The underlying server always accepts `bypass_validation: true` in the API arguments regardless of whether it is exposed in the schema, allowing programmatic override when needed.

### Tool Call Timeout System
* **`PATCHITRIGHT_DEFAULT_TIMEOUT`** (env variable) / **`--default-timeout`** (CLI flag):
  Configures the default execution time limit in seconds for all tool calls (default: `10.0`). Set to `-1` to disable the timeout completely.
* **`set_timeout`** (tool parameter):
  Available on all tools to override the default timeout limit for a specific execution. Set to `-1` to disable the timeout.


---

## 🛠️ Provided Tools

| Tool | Core Functionality | Key Inputs |
| :--- | :--- | :--- |
| `patch_file` | Performs surgical code edits (replacements, line insertions, unified diffs) across single or multiple files. | `target_file` (or `files`), `search_content`/`replace_content` OR `insert_content` OR `replacements` |
| `write_file` | Create a new file or fully overwrite an existing file with syntax validation. | `target_file`, `code_content`, `allow_overwrite` |
| `apply_last_dry_run` | *(Optional / Advanced)* Applies a previously cached dry-run patch. | `run_id` (a cached run ID valid for 300 seconds) |

---

## ⚙️ `patch_file` & `write_file` Parameters

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `target_file` | `string` | Path to the target file to modify. Contains path traversal guards. |
- `search_content` *(string)*: Exact string block to find. Keep blocks focused on minimal unique surrounding context.
- `replace_content` *(string)*: Replacement text.
| `patch_content` | `string` | Strict Unified Diff string to apply (`Fuzz = 0`). |
| `replacements` | `array` | List of replacement objects applied bottom-up to prevent line-drift. |
| `insert_line` | `integer` | 1-indexed target line number (`1` for top of file, `-1` for EOF, or positive line index). |
| `insert_content` | `string` | Text content to insert. |
| `insert_position` | `string` | Relative insertion position: `"before"` (default), `"after"`, `"start"`, or `"end"`. (`"start"` and `"end"` require `symbol_name`). |
| `auto_indent` | `boolean` | Infers and prepends reference line indentation (spaces or tabs). Defaults to `true`. |
| `symbol_name` | `string` | Scopes search matching to a specific class/function AST boundary using jCodeMunch. |
| `start_line` / `end_line`| `integer` | Scopes search matching to a specific 1-indexed line range. |
| `symbol_scope` | `string` | Scopes the mutation style of the target symbol. Supports: `"boundary"` (default: classic search-and-replace), `"full"` (replaces the entire function/class signature + body), or `"body"` (replaces *only* the inner content between `{` and `}`). |
| `allow_multiple` | `boolean` | Replaces all occurrences of the search content within the scope. Defaults to `false`. |
| `did_you_mean` | `boolean` | Automatically matches and replaces the closest block of code if similarity is >= 80%. |
| `dry_run` | `boolean` | Optional preview flag (Default: `false` for direct patches). Set to `true` to preview diffs without writing to disk or during self-modification. |
| `allow_overwrite` | `boolean` | Allows overwriting an existing file in `write_file`. Defaults to `false`. |
| `set_timeout` | `number` | Optional timeout override in seconds. Set to `-1` to disable the timeout completely. |

---


## 💡 AST-Scoped Replacements (No `search_content` required!)

With the introduction of `symbol_scope: "body"` and `symbol_scope: "full"`, `patchitRIGHT` allows AI agents to replace entire functions or just function bodies simply by targetting their `symbol_name` (resolved via jCodeMunch).

* **Eliminate Token Waste**: Agents no longer need to pass large, 100+ line original code blocks into `search_content`. They only pass the *new* body code to `replace_content`.
* **Smart Indentation Normalization**: `patchitRIGHT` automatically detects the function's base indentation and normalizes/re-indents your `replace_content` to match perfectly.
* **Auto Newline Padding**: Intelligently pads multiline brace blocks with line endings (`\n` or `\r\n` matching the file format) while preserving compact formatting for single-line arrow functions.
* **Multibyte Character Safety**: Maps tree-sitter's byte-level coordinates to Python character positions, ensuring multibyte characters (like emojis `🐛` or accented letters `é`) are never corrupted during column-level splicing.
* **Upfront Resolution**: Resolves all boundaries upfront and mutates from bottom to top, preventing offset-drift errors in multi-patch `replacements` queues.

---

## 📌 Line-Based & Symbol-Relative Insertion

Inserts code at specified line numbers or relative to AST symbols without requiring `search_content`:

> [!NOTE]
> **Line Insertion Behavior:** Insert operations NEVER overwrite existing code — they push existing lines **DOWN**.
> - `insert_position="before"` on line N: Inserts code ABOVE line N (line N shifts down).
> - `insert_position="after"` on line N: Inserts code BELOW line N (line N+1 shifts down).
> - `symbol_name` with `"start"` / `"end"`: Inserts at top or bottom inside symbol body (shifting inner lines down).

* **Line Indexing**: Pass `insert_line: 1` to insert at the top of a file, or `insert_line: -1` to append at end of file (EOF).
* **Symbol Relative Positioning**: Combine `symbol_name` with `insert_position`:
  - `"before"`: Inserts above the symbol (above decorator stacks if present).
  - `"after"`: Inserts below the symbol's end boundary.
  - `"start"` / `"end"`: Inserts at the entry or exit line inside the symbol body.
* **Auto-Indentation**: Detects reference line indentation (spaces or tabs) and scans adjacent non-blank lines if the target line is empty. Normalizes pre-indented blocks via `textwrap.dedent`.
* **Warning System**: Returns diagnostic warnings in the response object for out-of-bounds line clamping, tab/space mismatches (`auto_indent=false`), and indentation fallbacks.

---

## 🔒 Safety, Validation, & Recovery Features

### 1. Multi-Language Syntax Validation
Every write or patch is validated before committing. If syntax validation fails, the write is aborted, returning detailed diagnostic line/column coordinates:
- **Python**: Parses code using Python's native `ast` parser.
- **JavaScript & TypeScript (JS/TS/JSX/TSX)**: Runs validation using **Biome**. If Biome is missing, it falls back to a non-emitting **TypeScript compiler check** (`tsc --noEmit --skipLibCheck`) to detect TypeScript errors.
- **JSON & JSONC**: Validates JSON content after stripping single-line (`//`) and multi-line (`/* */`) comments.
- **TOML**: Parses content using Python 3.11's built-in `tomllib` (or `tomli` fallback).
- **YAML**: Parses content using `PyYAML`'s `safe_load` parser.

> [!TIP]
> **Original-Content Syntax Guard**: Before validating a modified file, `patchitRIGHT` checks if the original file was already syntactically invalid. If the original code was already broken, the syntax check is skipped. This prevents legacy syntax errors in existing files from blocking your edits!

### 2. Integrated Linting
After syntax validation, `patchitRIGHT` runs integrated linters and attaches clean, noise-filtered warning diagnostics to the tool response:
- **Python**: Lints using the fast Python linter **Ruff** (`ruff check - --no-cache`).
- **JS/TS/JSX/TSX/JSON**: Lints using **Biome** (`biome check`).
- **Timeout Protection**: All linter subprocesses are capped at a **10-second timeout** to ensure the server never hangs.

### 3. Transactional Safety & Startup Recovery
- **Atomic Commits**: For `batch_patch_files`, if any write fails, a full transaction rollback is executed, restoring all files to their original state.
- **File Backups**: Before writing, backups are stored in a hidden `.patchitRIGHT/backups` folder.
- **Startup Recovery**: If the MCP process crashes mid-write, `patchitRIGHT` automatically scans for dirty backups on next startup and recovers them safely to prevent code loss.

### 4. Self-Modification Protection
To prevent process watchers or bundlers from killing the MCP process before the JSON-RPC response is delivered, writes targeting the server's own codebase (`src/patchitright_mcp/`) are run with a **500ms delay** on a daemon thread.

### 5. Path Traversal Containment
Paths are normalized and checked to ensure no operations escape the active workspace root.

---

## 🔌 Standalone Mode & Custom AST Engines

**patchitRIGHT** is decoupled from **jCodeMunch** and can be run without dependencies in commercial/business environments:

1. **Running Standalone (No-Dependency Mode):**
   You can run `patchitRIGHT` without installing or indexing via `jCodeMunch`. All core features — including exact search-and-replace, line-range scopes, transactional batch patching, and Strict Unified Diffs — will function out-of-the-box. The only limitation is that resolving scoping via the `symbol_name` parameter will not be available.
2. **Swapping the AST/Repository Resolver:**
   If you wish to plug in a different AST parsing engine (such as `tree-sitter`, `libcst`, or a custom LSP client), you only need to modify two decoupled helper functions in [patch_file.py](file:///d:/Projects/patchitRIGHT/src/patchitright_mcp/patch_file.py):
   * `_resolve_allowed_base_dir`: Resolves the absolute project root.
   * `_resolve_ast_boundaries`: Scopes edits by looking up symbol definitions (returning `start_line` and `end_line`).

### 📊 Comparison Matrix: jCodeMunch Mode vs. Standalone Mode

| Feature / Aspect | jCodeMunch Mode (Indexed) | Standalone Mode (No-Dependency) |
| :--- | :--- | :--- |
| **Dependencies** | Requires `jcodemunch-mcp` and an indexed repository SQLite database | **None** (zero-dependency Python package) |
| **AST Symbol Scoping (`symbol_name`)** | **Supported** (O(1) lookup of class/function boundaries) | Unsupported (resolves to whole file scope unless custom parser is plugged in) |
| **Exact Search & Replace** | Supported | Supported |
| **Line-Range Scoping (`start_line`/`end_line`)** | Supported | Supported |
| **Unified Diff Patches (`patch_content`)** | Supported | Supported |
| **Multi-patch Queueing (`replacements`)** | Supported | Supported |
| **Transactional Batch Patches** | Supported | Supported |
| **Safety & Syntax Verification** | Supported | Supported |
| **Subprocess Timeouts & Delayed Writes** | Supported | Supported |
| **Setup Overhead** | Requires indexing repository metadata upfront | **Zero setup** (instant plug-and-play) |
| **Ideal For** | Large projects, complex AI coding agents requiring symbol understanding | CI/CD pipelines, lightweight integrations, local dry-run validation |

---

## 🧪 Testing & Development

Run tests in parallel using `pytest-xdist`:

```bash
uv run pytest
```

When debugging a specific failing test with breakpoints (`breakpoint()`) or verbose logging (`print`), run in single-threaded mode:

```bash
uv run pytest -n 0
```

---

## 📄 License & Terms

* **patchitRIGHT** is distributed under the **MIT License**.
* When configured to dynamically interface with [jCodeMunch-MCP](https://github.com/jgravelle/jcodemunch-mcp) (Copyright © 2024-2026 J. Gravelle), usage must comply with the [jCodeMunch Dual-Use License](https://j.gravelle.us/jCodeMunch/descriptions.php). The verbatim text of this license is included in the [LICENSE](LICENSE) file. Standalone mode or custom AST engine integrations are exempt from jCodeMunch license terms.
