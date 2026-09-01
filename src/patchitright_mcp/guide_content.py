"""Markdown documentation and guidance content for patchitRIGHT."""

from __future__ import annotations


def generate_patchitright_guide(version: str, file_type: str | list[str] = "general") -> str:
    """Return the markdown guide for patchitright-mcp."""
    base_guide = f"""## patchitright-mcp (v{version})

## Guides

1. Use this MCP's tools `patch_file` or `write_file` when touching codes that you don't want to break. It warns or stops you when your edit introduces a syntax error.
2. Only use `write_file` when you want to create new files or literally rewrite the whole existing file. For patch(es), use `patch_file` so you don't accidentally drop or change something.
3. Insert code at line: Inserts directly **ABOVE** `insert_line` N (`1` = top of file, `-1` = EOF).
4. If you don't have a clear context of the `target_file` (e.g., you haven't read it, you just hit a checkpoint/context compression), read the file again to make sure you're not hallucinating on your patches.
5. Include `"did_you_mean": true` if you want to apply fuzzy search for `search_content`. When `did_you_mean` is triggered, it means you hallucinated somewhere, so better re-read the file.
6. If output is redirected to `output.txt`, inspect the log file.

## Parameters

| Task | Key Arguments |
| :--- | :--- |
| **Single patch** | `search_content`, `replace_content` |
| **Multiple patches in one file** | `replacements: [{{ search_content, replace_content }}, ...]` |
| **Insert code at line N** | `insert_line` (`1`=top, `-1`=EOF), `insert_content` |
| **Edit function / class body** | `symbol_name`, `symbol_scope="body"`, `replace_content` |
| **Edit entire symbol** | `symbol_name`, `symbol_scope="full"`, `replace_content` |
| **Fuzzy match for `search_content`** | `did_you_mean=true` |
| **Replace all occurrences** | `allow_multiple=true` |

---

## Recipes

### 1. Multiple changes on one file, do this instead of multiple calls on the same file
```json
{{
  "target_file": "src/user.ts",
  "replacements": [
    {{ "search_content": "import {{ getA }} from './a.js';", "replace_content": "import {{ getA, getB }} from './a.js';" }},
    {{ "search_content": "export function run() {{\\n  return getA();\\n}}", "replace_content": "export function run() {{\\n  return getB();\\n}}" }}
  ]
}}
```

### 2. Search with symbol instead of `search_content`
```json
{{
  "target_file": "src/auth.ts",
  "symbol_name": "validateSession",
  "symbol_scope": "body",
  "replace_content": "  const session = await auth.get(id);\\n  if (!session) throw new Error('Unauthorized');\\n  return session.user;"
}}
```

### 3. Insert something
```json
{{
  "target_file": "src/index.ts",
  "insert_line": 1,
  "insert_content": "import 'reflect-metadata';\\n"
}}
```
"""

    file_types = file_type if isinstance(file_type, list) else [file_type]

    for ft in file_types:
        if ft == "js_ts":
            base_guide += """
### JavaScript / TypeScript Clean-Code Guidelines
- **Native Imports**: MUST prefix built-in Node modules with `node:` (e.g., `node:fs`).
- **Null Safety**: MUST USE optional chaining (`item?.id`) over logical AND (`item && item.id`).
- **Equality**: MUST USE strict equality (`===` / `!==`). NEVER use loose equality (`==` / `!=`).
- **Lookups**: MUST USE `Set.has()` over `Array.includes()` for searching large collections.
- **Types**: NEVER use `any` in TypeScript unless explicitly migrating legacy code. MUST define interfaces/types.
- **Dead Code**: NEVER delete unused functions/methods without running impact analysis (`gitnexus_impact` or `find_references`). Dynamic callers (e.g., IPC events) may break.
"""
        elif ft == "html_css":
            base_guide += """
### HTML / CSS Accessibility & Standards Guidelines
- **A11y Labels**: MUST link every `<label>` to its input via matching `for` and `id` attributes.
- **A11y Media**: MUST include meaningful `alt` attributes on all `<img>` tags.
- **Semantic Tags**: NEVER use empty heading tags (e.g., `<h2></h2>`) for spacing.
- **Buttons**: MUST explicitly define `type="button"`, `type="submit"`, or `type="reset"` on `<button>` elements.
- **Word Break**: NEVER use deprecated `word-break: break-word`. MUST USE `overflow-wrap: break-word`.
"""
        elif ft == "python":
            base_guide += """
### Python Security Guidelines
- **File Handling**: MUST USE context managers (`with open(...) as f:`) for file operations. NEVER leave files manually unclosed.
- **Default Args**: NEVER use mutable default arguments (e.g., `def func(items=[])`). MUST USE `None` and initialize inside the function (`items = items or []`).
- **None Checks**: MUST USE `is` / `is not` when checking against `None` (e.g., `if x is None:`). NEVER use `== None`.
- **Path Security**: MUST validate user-controlled paths against the expected working directory before opening/writing to prevent directory traversal.
"""

    return base_guide
