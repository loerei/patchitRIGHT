# patchitRIGHT

**patchitRIGHT** is a single-tool AST-bounded secure code-writing MCP server.

It leverages [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) index engines to perform surgical search-and-replace (`patch_file`) operations within precise AST scopes (such as functions or classes), complete with line filters and safe dry-run previews.

---

## 🚀 Quick Setup (MCP Client)

Add the following configuration to your MCP client configuration file (e.g., `claude_desktop_config.json` or Antigravity settings):

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

### `patch_file`
Performs surgical code replacement on a single target file. Supports three operation modes:
1. **Search-and-Replace Mode:**
   * `target_file` (required): Path to the file to modify.
   * `search_content` (required): The exact code block to search for.
   * `replace_content` (required): The replacement code block.
2. **Unified Diff Mode:**
   * `patch_content` (required): Unified Diff string to apply strictly (`Fuzz = 0`).
3. **Multi-patch Mode:**
   * `replacements` (required): Array of replacement objects, where each object contains:
     * `search_content` (required): The exact code block to search for.
     * `replace_content` (required): The replacement code block.
     * `start_line` / `end_line` / `symbol_name` / `allow_multiple` / `line_filter` (optional): Scope parameters specific to this replacement.
     * *Note: Replacements are sorted and applied bottom-up to prevent line-drift.*

**Optional parameters (common):**
* `symbol_name`: Restricts the search scope to a specific AST symbol (function/class) using jCodeMunch index.
* `start_line` / `end_line`: Limits the search region to a specific 1-indexed line range.
* `allow_multiple`: If `true`, replaces all occurrences of the search content within the scope. Defaults to `false`.
* `dry_run`: Returns a unified git-style diff preview of the changes without writing to disk.
* `did_you_mean`: If `true`, applies the replacement to the closest matching block of code if similarity is >= 80%.

---

## 🔒 Safety & Validation Features

* **Path Traversal Protection**: Normalizes paths using `os.path.realpath` and rejects any path containing directory traversal sequences (`..`) to restrict relative files to the active workspace.
* **Line Ending Normalization**: Automatically normalizes CRLF (`\r\n`) and LF (`\n`) line endings internally during matching and patching. The original file's dominant line ending style is preserved when writing back to disk.
* **Python AST Verification**: Validates modified `.py` files using Python's standard `ast` module. If a patch introduces a syntax error, the edit is aborted before writing to disk.
* **Ruff Linter Integration**: Runs `ruff check` internally on patched Python files to return inline warnings (such as unused imports or variables) in the tool's message response.

---

### `batch_patch_files`
Performs an atomic, transactional refactoring operation across multiple target files.
* `patches` (required): Array of patch objects, where each object contains:
  * `target_file`: Path to the target file.
  * `patch_content`: Git-style Unified Diff hunk(s) to apply to this file (`Fuzz = 0`).
* `dry_run` (optional): Returns a unified git-style diff preview of all changes without writing to disk.

#### 🛡️ Transaction Security & Auto-recovery
* **Atomic Rollback:** If any patch fails validation or writing, the entire transaction is rolled back, restoring the original state of all files.
* **Optimistic Locking:** Checks the raw byte hash of files before and after the backup phase to prevent conflicts with concurrent modifications.
* **Crash Resilience:** Ephemeral backup files are written to `.patchitRIGHT/backups/` within the active repository root. If the process is killed midway, `run_startup_recovery` automatically restores the backups on the next MCP server startup.

---

## 🔌 Standalone Mode & Custom AST Engines

**patchitRIGHT** is decoupled from **jCodeMunch**. If you do not wish to use jCodeMunch in a commercial/business environment:

1. **Running Standalone (No-Dependency Mode):**
   You can run `patchitRIGHT` without installing or indexing via `jCodeMunch`. All core features — including exact search-and-replace, line-range scopes, transactional batch patching, and Strict Unified Diffs — will function out-of-the-box. The only limitation is that resolving scoping via the `symbol_name` parameter will not be available.

2. **Swapping the AST/Repository Resolver:**
   If you wish to plug in a different AST parsing engine (such as `tree-sitter`, `libcst`, or a custom LSP client), you only need to modify two decoupled helper functions in [patch_file.py](file:///d:/Projects/patchitRIGHT/src/patchitright_mcp/patch_file.py):
   * `_resolve_allowed_base_dir`: Resolves the absolute project root.
   * `_resolve_ast_boundaries`: Scopes edits by looking up symbol definitions (returning `start_line` and `end_line`).

---

## 📄 License & Terms

* **patchitRIGHT** is distributed under the **MIT License**.
* When configured to dynamically interface with [jCodeMunch-MCP](https://github.com/jgravelle/jcodemunch-mcp) (Copyright © 2024-2026 J. Gravelle), usage must comply with the [jCodeMunch Dual-Use License](https://j.gravelle.us/jCodeMunch/descriptions.php). The verbatim text of this license is included in the [LICENSE](LICENSE) file. Standalone mode or custom AST engine integrations are exempt from jCodeMunch license terms.
