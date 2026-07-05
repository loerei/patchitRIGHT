# patchitRIGHT

**patchitRIGHT** is a single-tool AST-bounded secure code-writing MCP server.

It leverages [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) index engines to perform surgical search-and-replace (`patch_file`) operations within precise AST scopes (such as functions or classes), complete with line filters and safe dry-run previews.

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
        "PYTHONPATH": "d:/Projects/patchitRIGHT/src"
      }
    }
  }
}
```

---

## 🛠️ Provided Tools

| Tool | Core Functionality | Key Inputs |
| :--- | :--- | :--- |
| `patch_file` | Performs surgical code replacement on a single target file. | `target_file`, `search_content`/`replace_content` OR `patch_content` OR `replacements` |
| `batch_patch_files` | Performs an atomic, transactional refactoring operation across multiple target files. | `patches` (array of patch objects containing target_file and patch_content) |
| `apply_last_dry_run` | Applies a previously cached dry-run patch. | `run_id` (a cached run ID valid for 300 seconds) |

---

## ⚙️ `patch_file` Parameters

| Parameter | Type | Mode | Description |
| :--- | :--- | :--- | :--- |
| `target_file` | `string` | All | Path to the target file to modify. |
| `search_content` | `string` | Search-and-Replace | The exact code block to search for. Must match uniquely. **Blocks over 50 lines are discouraged** (use `symbol_name` instead). |
| `replace_content` | `string` | Search-and-Replace | The replacement code block. |
| `patch_content` | `string` | Unified Diff | Unified Diff string to apply strictly (`Fuzz = 0`). |
| `replacements` | `array` | Multi-patch | List of replacement objects applied bottom-up to prevent line-drift. |
| `symbol_name` | `string` | Scope | Restricts the search scope to a specific AST symbol (function/class) using jCodeMunch index. |
| `start_line` / `end_line` | `integer` | Scope | Limits the search region to a specific 1-indexed line range. |
| `allow_multiple` | `boolean` | Options | If `true`, replaces all occurrences of the search content within the scope. Defaults to `false`. |
| `did_you_mean` | `boolean` | Options | If `true`, automatically applies the replacement to the closest matching block if similarity is >= 80%. |
| `dry_run` | `boolean` | Options | Returns a unified git-style diff preview of the changes and caches the `run_id` without writing to disk. |

---

## 🔒 Safety & Validation Features

| Feature | Protection Mechanism | Details |
| :--- | :--- | :--- |
| **Path Traversal Protection** | Folder containment | Normalizes paths using `os.path.realpath` and rejects any path containing directory traversal sequences (`..`) outside the active workspace. |
| **Line Ending Normalization** | Format matching | Automatically normalizes CRLF (`\r\n`) and LF (`\n`) line endings during matching and patching. The original file's dominant line ending is preserved. |
| **Python AST Verification** | Syntax checking | Validates modified `.py` files using Python's standard `ast` module. If a patch introduces a syntax error, the edit is aborted. |
| **Linter Integration & Timeouts** | Code quality | Runs `ruff check` on Python files and `biome check` on JS/TS/JSON files. All subprocess runs are capped at a **10-second timeout** to prevent hangs. |
| **Self-Modification Safety** | Connection preservation | Detects writes to the MCP server's own codebase and delays writes by **500ms** using a daemon background thread, allowing the response to be sent before hot-reloads. |
| **Transactional Auto-recovery** | Transaction rollback | Rollback is performed on all files if any file in a `batch_patch_files` transaction fails. Recovery of dirty backups is run on next startup from `.patchitRIGHT/backups/`. |

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

## 📄 License & Terms

* **patchitRIGHT** is distributed under the **MIT License**.
* When configured to dynamically interface with [jCodeMunch-MCP](https://github.com/jgravelle/jcodemunch-mcp) (Copyright © 2024-2026 J. Gravelle), usage must comply with the [jCodeMunch Dual-Use License](https://j.gravelle.us/jCodeMunch/descriptions.php). The verbatim text of this license is included in the [LICENSE](LICENSE) file. Standalone mode or custom AST engine integrations are exempt from jCodeMunch license terms..
