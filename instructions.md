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

## Scope Options

Use `start_line`/`end_line` or `symbol_name` to limit the search scope:

- `symbol_name` — resolves function/class boundaries via jCodeMunch index (requires the file to be indexed)
- `start_line` / `end_line` — explicit line range (1-indexed, inclusive)
- Omit both — entire file is the scope

## Safety Checks

- **Occurrence check**: by default, `search_content` must match exactly once in scope. Set `allow_multiple: true` to replace all occurrences.
- **`line_filter`**: assert that the match starts at a specific line number (int) or that the scope contains a specific substring (str) — useful for verifying context before patching.
- **`dry_run`**: returns a unified diff without writing. Always use this to preview large or risky changes.
- **`file_filter`** / **`folder_filter`**: additional guards to ensure the target file is the intended one.

## Error: `fatal_context_mismatch`

Returned when a **relative** `target_file` resolves to a path outside the active workspace. Fix: use an absolute path, or ensure the shell is `cd`-ed to the correct repository before the MCP server starts.

## Dry-Run → Apply Workflow (run_id)

To avoid resending the full payload twice, use the two-step flow:

**Step 1 — preview:**
```json
patch_file(target_file="src/foo.py", search_content="...", replace_content="...", dry_run=true)
// Response includes: { "dryRun": true, "run_id": "a1b2c3", "expires_in": 300, "message": "<diff>" }
```

**Step 2 — apply (no payload resend):**
```json
apply_last_dry_run(run_id="a1b2c3")
// Response: { "success": true, "dryRun": false, "message": "Applied cached patch..." }
```

The same flow works for `batch_patch_files`.

### Guards
- `run_id` is **single-use** and expires after **300 seconds**.
- If any target file was modified between the dry-run and the apply call, `apply_last_dry_run` returns an error and leaves all files untouched. Re-run with `dry_run=true` to get a fresh preview.
