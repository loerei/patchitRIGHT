"""MCP server for patchitRIGHT."""

import argparse
import asyncio
import json
from pathlib import Path


from mcp.server import Server
from mcp.types import Tool, TextContent

from . import __version__
from .patch_file import patch_file, batch_patch_files, run_startup_recovery, apply_last_dry_run, write_file


# Create the MCP server instance
server = Server("patchitright-mcp")

STORAGE_PATH_DESC = "Optional custom path to the jCodeMunch SQLite index database."
DEFAULT_TIMEOUT = 10.0


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    import os
    expose_bypass = os.environ.get("PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION", "").lower() in ("true", "1", "yes")
    show_legacy = os.environ.get("PATCHITRIGHT_SHOW_LEGACY", os.environ.get("SHOW_LEGACY", "")).lower() in ("true", "1", "yes")

    tools = [
        Tool(
            name="patch_file",
            description=(
                "Edit a file by replacing an exact text block (search_content/replace_content) "
                "or applying a unified diff (patch_content). Optionally scope to a line range or AST symbol. "
                "Keep search_content focused on the minimal unique surrounding context required for matching. "
                "For editing multiple non-contiguous blocks in a single file, use the 'replacements' array in one call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "Absolute (any file) or relative (active workspace) path. Forward slashes (/) recommended to avoid JSON escaping and save tokens."
                    },
                    "search_content": {
                        "type": "string",
                        "description": "The exact string block to search for. Must match uniquely within the scope unless allow_multiple is True. Keep snippets focused on minimal unique context."
                    },
                    "replace_content": {
                        "type": "string",
                        "description": "The string block to replace the search content with."
                    },
                    "patch_content": {
                        "type": "string",
                        "description": "Optional unified diff patch content to apply strictly (Fuzz = 0). If provided, search_content and replace_content are not required."
                    },
                    "folder_filter": {
                        "type": "string",
                        "description": "Optional subdirectory filter. The target file must reside inside this subdirectory."
                    },
                    "file_filter": {
                        "type": "string",
                        "description": "Optional file name substring filter. The target file name must contain this substring."
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional starting line number (1-indexed, inclusive) of the scope to search."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional ending line number (1-indexed, inclusive) of the scope to search."
                    },
                    "insert_line": {
                        "type": "integer",
                        "description": "Optional 1-indexed target line number to insert content above. Use 1 for top of file, or -1 to append at end-of-file."
                    },
                    "insert_content": {
                        "type": "string",
                        "description": "Text content to insert directly above insert_line."
                    },
                    "auto_indent": {
                        "type": "boolean",
                        "default": True,
                        "description": "If True, automatically matches leading indentation of the target line. Defaults to True."
                    },
                    "symbol_name": {
                        "type": "string",
                        "description": "Optional AST symbol name (e.g. function or class name) to scope the search to. Resolves boundaries via jCodeMunch index."
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If True, replaces all occurrences of search_content within the scope. Defaults to False, which raises an error if multiple matches are found.",
                        "default": False
                    },
                    "line_filter": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "integer"}
                        ],
                        "description": "Optional assertion. If an integer, asserts the search content starts exactly at this 1-indexed line. If a string, asserts the resolved scope contains this substring."
                    },
                    "did_you_mean": {
                        "type": "boolean",
                        "description": "If True, automatically applies the replacement to the closest matching block of code if similarity >= 80%. Defaults to False.",
                        "default": False
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If True, returns a unified diff preview of the changes without modifying the file. Defaults to False.",
                        "default": False
                    },
                    "storage_path": {
                        "type": "string",
                        "description": STORAGE_PATH_DESC
                    },
                    "symbol_scope": {
                        "type": "string",
                        "enum": ["boundary", "full", "body"],
                        "default": "boundary",
                        "description": (
                            "Controls how 'symbol_name' is used. "
                            "'boundary' (default): scopes search_content matching to symbol's line range. "
                            "'full': replaces the entire symbol (signature+body) with replace_content, no search_content needed. "
                            "WARNING: 'full' includes decorators, 'export' keywords, and JSDoc — you MUST include these in replace_content. "
                            "'body': replaces only the function body with replace_content, preserving the signature. "
                            "For arrow expression bodies, provide the exact replacement expression — "
                            "if returning an object literal, include the parentheses: ({ key: value })."
                        )
                    },
                    "replacements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "search_content": {"type": "string", "description": "The exact string block to search for."},
                                "replace_content": {"type": "string", "description": "The string block to replace the search content with."},
                                "start_line": {"type": "integer", "description": "Optional starting line number (1-indexed, inclusive) of the scope to search."},
                                "end_line": {"type": "integer", "description": "Optional ending line number (1-indexed, inclusive) of the scope to search."},
                                "symbol_name": {"type": "string", "description": "Optional AST symbol name to scope this replacement to."},
                                "symbol_scope": {
                                    "type": "string",
                                    "enum": ["boundary", "full", "body"],
                                    "default": "boundary",
                                    "description": "Controls how symbol_name is used for this replacement."
                                },
                                "allow_multiple": {"type": "boolean", "description": "If True, replaces all occurrences of search_content within the scope. Defaults to False."},
                                "line_filter": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "integer"}
                                    ],
                                    "description": "Optional assertion (line number or substring check)."
                                },
                                "insert_line": {"type": "integer", "description": "Optional 1-indexed line number to insert content above (-1 for EOF)."},
                                "insert_content": {"type": "string", "description": "Text content to insert."},
                                "auto_indent": {"type": "boolean", "default": True, "description": "Auto-indent matching target line."}
                            }
                        },
                        "description": "Optional list of replacements to apply in a single call to the same file. Applied bottom-up to avoid line-drift."
                    },
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_file": {"type": "string", "description": "Target file path."},
                                "search_content": {"type": "string", "description": "Exact text to search for."},
                                "replace_content": {"type": "string", "description": "Replacement text."},
                                "patch_content": {"type": "string", "description": "Unified diff patch content."},
                                "replacements": {"type": "array", "description": "List of non-contiguous replacements."},
                                "symbol_name": {"type": "string", "description": "AST symbol name for scoped replacement."},
                                "symbol_scope": {"type": "string", "enum": ["boundary", "body", "full"], "description": "Scope mode for symbol replacement."},
                                "start_line": {"type": "integer", "description": "Optional 1-based start line range."},
                                "end_line": {"type": "integer", "description": "Optional 1-based end line range."},
                                "allow_multiple": {"type": "boolean", "description": "If true, replace all occurrences in scope."},
                                "did_you_mean": {"type": "boolean", "description": "If true, apply closest fuzzy match fallback."},
                                "line_filter": {"type": "string", "description": "Optional line filter pattern."},
                                "insert_line": {"type": "integer", "description": "Optional 1-indexed line number to insert content above (-1 for EOF)."},
                                "insert_content": {"type": "string", "description": "Text content to insert."},
                                "auto_indent": {"type": "boolean", "default": True, "description": "Auto-indent matching target line."}
                            },
                            "required": ["target_file"]
                        },
                        "description": "Optional array of file edit objects for multi-file batch patching in a single atomic transaction."
                    }
                },
                "anyOf": [
                    {"required": ["target_file"]},
                    {"required": ["files"]}
                ]
            }
        ),
        Tool(
            name="apply_last_dry_run",
            description=(
                "Apply the patch cached by a previous dry_run=true call. "
                "Requires the run_id from that response. "
                "Fails if the run_id is expired (300 s TTL) or if any target file was modified after the dry-run."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The run_id returned by a previous patch_file or batch_patch_files dry-run call."
                    }
                },
                "required": ["run_id"]
            }
        ),
        Tool(
            name="write_file",
            description=(
                "Create a new file or fully replace file content. Only use overwrite when content needs to be "
                "fully changed by design (e.g. generated output, config regeneration, new file from scratch). "
                "MUST NOT use overwrite to modify existing code files; use patch_file instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "Absolute or relative path to the file to write. Forward slashes (/) recommended."
                    },
                    "code_content": {
                        "type": "string",
                        "description": "The complete content of the file."
                    },
                    "allow_overwrite": {
                        "type": "boolean",
                        "description": "If True, allows overwriting an existing file. Defaults to False, which blocks the write if the file already exists.",
                        "default": False
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If True, runs syntax validation and linting, and shows a preview of the write/diff without writing to disk. Defaults to False.",
                        "default": False
                    },
                    "storage_path": {
                        "type": "string",
                        "description": STORAGE_PATH_DESC
                    }
                },
                "required": ["target_file", "code_content"]
            }
        ),
        Tool(
            name="patchitright_guide",
            description=(
                "Return the version-current AGENTS.md / CLAUDE.md policy snippet for patchitright-mcp, "
                "including best practices, tool descriptions, and size limitations. "
                "Specify file_type list to get target language clean-code style rules."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_type": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["general", "js_ts", "html_css", "python"]
                        },
                        "description": "Optional list of file/language types to retrieve clean-code style rules.",
                        "default": ["general"]
                    }
                }
            }
        )
    ]

    if show_legacy:
        tools.append(
            Tool(
                name="batch_patch_files",
                description=(
                    "Apply unified diffs to multiple files in one call. "
                    "All patches are validated before any file is written; if one fails, none are applied."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "patches": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "target_file": {
                                        "type": "string",
                                        "description": "Absolute (any file) or relative (active workspace) path. Forward slashes (/) recommended to avoid JSON escaping and save tokens."
                                    },
                                    "patch_content": {
                                        "type": "string",
                                        "description": "The exact Git-style Unified Diff hunk(s) to apply to this file."
                                    }
                                },
                                "required": ["target_file", "patch_content"]
                            },
                            "description": "List of target files and their corresponding Unified Diffs."
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "If True, returns a unified diff preview of the changes without modifying the files. Defaults to False.",
                            "default": False
                        },
                        "storage_path": {
                            "type": "string",
                            "description": STORAGE_PATH_DESC
                        }
                    },
                    "required": ["patches"]
                }
            )
        )

    if expose_bypass:
        for tool in tools:
            if tool.name in ("patch_file", "batch_patch_files", "write_file"):
                tool.inputSchema["properties"]["bypass_validation"] = {
                    "type": "boolean",
                    "description": "If True, bypasses syntax validation and linting checks. Use with caution.",
                    "default": False
                }

    for tool in tools:
        tool.inputSchema["properties"]["set_timeout"] = {
            "type": "number",
            "description": "Optional timeout in seconds to override the default limit. Use -1 to disable the timeout completely."
        }

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute the requested tool."""
    if name not in ("patch_file", "batch_patch_files", "apply_last_dry_run", "write_file", "patchitright_guide"):
        raise ValueError(f"Unknown tool: {name}")

    if name == "patchitright_guide":
        file_type = arguments.get("file_type")
        if not isinstance(file_type, list):
            file_type = [file_type] if file_type else ["general"]
        return [TextContent(type="text", text=json.dumps({
            "version": __version__,
            "content": _generate_patchitright_guide(file_type),
        }, indent=2))]

    try:
        set_timeout = arguments.get("set_timeout")
        if set_timeout is not None:
            timeout_val = float(set_timeout)
        else:
            timeout_val = DEFAULT_TIMEOUT
    except (ValueError, TypeError):
        timeout_val = DEFAULT_TIMEOUT

    if timeout_val < 0:
        timeout_val = None

    if name == "apply_last_dry_run":
        func = _execute_apply_last_dry_run
    elif name == "batch_patch_files":
        func = _execute_batch_patch_files
    elif name == "write_file":
        func = _execute_write_file
    else:
        func = _execute_patch_file

    try:
        if timeout_val is not None:
            return await asyncio.wait_for(
                asyncio.to_thread(func, arguments),
                timeout=timeout_val
            )
        else:
            return await asyncio.to_thread(func, arguments)
    except asyncio.TimeoutError:
        error_report = {
            "success": False,
            "error": f"TimeoutError: Tool execution exceeded the limit of {timeout_val} seconds.",
            "details": {
                "tool": name,
                "target_file": arguments.get("target_file"),
                "elapsed_seconds": timeout_val,
                "suggestion": (
                    "The operation timed out during verification. "
                    "Consider increasing the timeout limit by adding 'set_timeout': 30 or 'set_timeout': -1 to your tool arguments. "
                    "If this repeats consider using other patch tools or run scripts, stop and ask your user for permission if needed."
                )
            }
        }
        return [TextContent(type="text", text=json.dumps(error_report, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error executing {name}: {str(e)}")]


def _execute_write_file(arguments: dict) -> list[TextContent]:
    target_file = arguments.get("target_file")
    code_content = arguments.get("code_content")
    allow_overwrite = bool(arguments.get("allow_overwrite", False))
    dry_run = bool(arguments.get("dry_run", False))
    storage_path = arguments.get("storage_path")
    bypass_validation = bool(arguments.get("bypass_validation", False))

    if not target_file:
        return [TextContent(type="text", text="Error: target_file is required.")]
    if code_content is None:
        return [TextContent(type="text", text="Error: code_content is required.")]

    res = write_file(
        target_file=target_file,
        code_content=code_content,
        allow_overwrite=allow_overwrite,
        dry_run=dry_run,
        storage_path=storage_path,
        bypass_validation=bypass_validation,
    )
    return [TextContent(type="text", text=json.dumps(res, indent=2))]


def _execute_apply_last_dry_run(arguments: dict) -> list[TextContent]:
    run_id = arguments.get("run_id")
    if not run_id:
        return [TextContent(type="text", text="Error: run_id is required for apply_last_dry_run.")]
    res = apply_last_dry_run(run_id=run_id)
    return [TextContent(type="text", text=json.dumps(res, indent=2))]


def _execute_batch_patch_files(arguments: dict) -> list[TextContent]:
    patches = arguments.get("patches")
    if not patches:
        return [TextContent(type="text", text="Error: patches array is required for batch_patch_files.")]
    dry_run = bool(arguments.get("dry_run", False))
    storage_path = arguments.get("storage_path")
    bypass_validation = bool(arguments.get("bypass_validation", False))
    
    res = batch_patch_files(
        patches=patches,
        dry_run=dry_run,
        storage_path=storage_path,
        bypass_validation=bypass_validation,
    )
    return [TextContent(type="text", text=json.dumps(res, indent=2))]


def _execute_patch_file(arguments: dict) -> list[TextContent]:
    files = arguments.get("files")
    target_file = arguments.get("target_file")
    search_content = arguments.get("search_content")
    replace_content = arguments.get("replace_content")
    patch_content = arguments.get("patch_content")
    symbol_scope = arguments.get("symbol_scope", "boundary")
    symbol_name = arguments.get("symbol_name")
    replacements = arguments.get("replacements")
    insert_line = arguments.get("insert_line")
    insert_content = arguments.get("insert_content")
    auto_indent = bool(arguments.get("auto_indent", True)) if arguments.get("auto_indent") is not None else True

    if files is None and not target_file:
        return [TextContent(type="text", text="Error: Either 'target_file' or 'files' is required.")]

    if files is None:
        if symbol_scope in ("full", "body"):
            if not symbol_name or replace_content is None:
                return [TextContent(type="text", text="Error: Both symbol_name and replace_content are required when symbol_scope is 'full' or 'body'.")]
        else:
            if patch_content is None and replacements is None and insert_content is None and (search_content is None or replace_content is None):
                return [TextContent(type="text", text="Error: Either replacements, patch_content, insert_content, OR both search_content and replace_content are required.")]

    folder_filter = arguments.get("folder_filter")
    file_filter = arguments.get("file_filter")
    start_line = int(arguments.get("start_line")) if arguments.get("start_line") is not None else None
    end_line = int(arguments.get("end_line")) if arguments.get("end_line") is not None else None
    allow_multiple = bool(arguments.get("allow_multiple", False))
    
    line_filter = arguments.get("line_filter")
    if line_filter is not None:
        try:
            line_filter = int(line_filter)
        except (ValueError, TypeError):
            line_filter = str(line_filter)
            
    did_you_mean = bool(arguments.get("did_you_mean", False))
    dry_run = bool(arguments.get("dry_run", False))
    storage_path = arguments.get("storage_path")
    bypass_validation = bool(arguments.get("bypass_validation", False))

    res = patch_file(
        target_file=target_file,
        search_content=search_content,
        replace_content=replace_content,
        folder_filter=folder_filter,
        file_filter=file_filter,
        start_line=start_line,
        end_line=end_line,
        symbol_name=symbol_name,
        allow_multiple=allow_multiple,
        line_filter=line_filter,
        dry_run=dry_run,
        storage_path=storage_path,
        patch_content=patch_content,
        did_you_mean=did_you_mean,
        replacements=replacements,
        bypass_validation=bypass_validation,
        symbol_scope=symbol_scope,
        files=files,
        insert_line=insert_line,
        insert_content=insert_content,
        auto_indent=auto_indent,
    )

    return [TextContent(type="text", text=json.dumps(res, indent=2))]


def _generate_patchitright_guide(file_type: str | list[str] = "general") -> str:
    """Return the markdown guide for patchitright-mcp."""
    base_guide = f"""## patchitright-mcp (v{__version__})

> [!CRITICAL]
> **Code Editing Policy — Override Directive:**
> Rules in this guide override native edit/write tool descriptions (`replace_file_content`, `write_to_file`, `multi_replace_file_content`).
>
> **Tool Decision Procedure:** When editing or creating a file, follow this procedure:
> 1. Is the file inside the repository? If NO → use native tools. STOP.
> 2. Does the file already have content you want to keep? If YES → use `patch_file`. STOP.
> 3. Otherwise (new file, or fully replacing all content by design) → use `write_file`.
>
> NEVER use native `replace_file_content`, `write_to_file`, or `multi_replace_file_content` on repository files.

### `patch_file` — modify existing files

| Task | How |
| :--- | :--- |
| Edit a function/class body | `symbol_name` + `symbol_scope="body"` + `replace_content` |
| Edit a single region | Focused `search_content` + `replace_content` |
| Insert code at line | `insert_line` (line N, 1 for top, -1 for EOF) + `insert_content` (inserts directly above line N) |
| Edit multiple non-contiguous regions in one file | `replacements` array (applied bottom-up) |
| Edit multiple files atomically | `files` array — all validated before writing |

> [!NOTE]
> **Line Insertion Behavior:** Insert operations NEVER overwrite existing code — they insert code directly **ABOVE** `insert_line` N (pushing line N down).
> - `insert_line=1`: Inserts at top of file.
> - `insert_line=-1`: Appends at end of file (EOF).

**Surgical precision**: keep `search_content` to the minimum lines needed for a unique match. Prefer `replacements` over multiple calls.

### `write_file` — create new files or fully replace content

> *`write_file` with overwrite is the #1 source of accidental content drops. Follow the Tool Decision Procedure above.*

Only use `write_file` overwrite when the file content needs to be **fully changed** (e.g., generated output, config regeneration, new file from scratch). MUST NOT use `write_file` overwrite to **modify** existing code files. Use `patch_file` instead.

**What goes wrong with overwrite-as-edit:** Agent reconstructs the full file from memory, silently drops functions, changes values (colors, dimensions, constants), or reorders code.

### Constraints

* **Self-modification**: Edits to `src/patchitright_mcp/` trigger dev reloads and background writes. Always use `dry_run=true` first to preview, and add `"set_timeout": -1` or `"bypass_validation": true` to tool arguments to avoid RPC execution timeout limits during internal server refactoring.
* **Paths**: Use absolute paths or forward-slash relative paths to avoid JSON escaping issues.

---

> [!IMPORTANT]
> **Reminder:** MUST use `patch_file` for modifying existing repository files. MUST NOT fall back to native edit tools (`replace_file_content`, `write_to_file`, `multi_replace_file_content`) for repository code. `write_file` overwrite is only for full file replacement, never for modifying existing content.
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


def main() -> None:
    """Server entry point."""
    global DEFAULT_TIMEOUT
    parser = argparse.ArgumentParser(description="patchitRIGHT MCP Server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--default-timeout", type=float, default=10.0, help="Default tool execution timeout in seconds (-1 to disable)")
    args, unknown = parser.parse_known_args()

    DEFAULT_TIMEOUT = args.default_timeout

    import os
    env_timeout = os.environ.get("PATCHITRIGHT_DEFAULT_TIMEOUT")
    if env_timeout is not None:
        try:
            DEFAULT_TIMEOUT = float(env_timeout)
        except ValueError:
            pass

    # Trigger transactional auto-recovery of dirty .bak files on startup
    run_startup_recovery(Path.cwd().resolve())

    import mcp.server.stdio
    async def _run():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
