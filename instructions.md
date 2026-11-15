# patchitright MCP — Usage Instructions

## Path Handling

`target_file` accepts both **absolute** and **relative** paths, with different security semantics:

| Path type | Constraint | Use when |
|---|---|---|
| **Relative** | Must resolve inside the active workspace (`cwd`) | Editing files within the current project |
| **Absolute** | No workspace constraint — any file on the system | Editing files outside the project (global configs, other repos, etc.) |

The workspace constraint on relative paths protects against cross-repo drift: if the MCP process `cwd` does not match the intended repository, a relative path would silently edit the wrong file. An absolute path makes the target unambiguous, so the constraint does not apply.

### Examples

```
# Relative — constrained to workspace
target_file: "src/main.py"

# Absolute — no constraint, edits exactly this file
target_file: "C:/Users/user/.gemini/GEMINI.md"
```

## Scope & Source-Slice Options

Use `start_line`/`end_line` or `symbol_name` to limit the search scope:

- `symbol_name` — resolves function/class boundaries on-demand via built-in AST parsing (no external index required)
- `start_line` / `end_line` — explicit line range (1-indexed, inclusive). Can omit `search_content` only when guarded by `start_anchor`, `end_anchor`, or `line_filter` (`replace_content: ""` removes those lines cleanly).
- Omit both — entire file is the scope
- `source_file` — copy a whole file (`write_file`), extract a line range (`source_start_line`/`source_end_line` + optional `source_start_anchor`/`source_end_anchor`) or `source_symbol` into a new file (`write_file`) or as replacement/insertion text in `patch_file` / `replacements[]`. Add `delete_source: true` to move whole files, or `cut_source: true` to atomically cut the extracted slice out of `source_file`.

## Safety Checks & Broadcasts

- **Occurrence check**: by default, `search_content` must match exactly once in scope. Use `expected_occurrences: N` (`>= 1`) to assert an exact count, or `allow_multiple: true` to replace all occurrences.
- **Broadcast & Skip**: use `target_files` or `target_glob` to share edit fields across files; set `skip_unmatched: true` to skip non-matching files (still fails if 0 files match overall). Mixed `files[]` batches support `action: "patch" | "write" | "delete"`.
- **`line_filter` / `start_anchor` / `end_anchor`**: assert that the match starts at a specific line number (int), contains a substring (str), or matches explicit boundary anchors.
- **`file_filter`** / **`folder_filter`**: additional guards to ensure the target file is the intended one.

## Error: `fatal_context_mismatch`

Returned when a **relative** `target_file` resolves to a path outside the active workspace. Fix: use an absolute path, or ensure the shell is `cd`-ed to the correct repository before the MCP server starts.
