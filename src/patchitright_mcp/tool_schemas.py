"""MCP tool schema definitions and enrichment utilities for patchitRIGHT."""

from __future__ import annotations

import copy
from mcp.types import Tool

STORAGE_PATH_DESC = "Optional custom path to the jCodeMunch SQLite index database."


def _enrich_tool_schema(tool: Tool, expose_bypass: bool) -> Tool:
    """Return an enriched Tool instance without dropping non-schema model attributes."""
    props = dict(tool.inputSchema.get("properties", {}))
    if expose_bypass and tool.name in ("patch_file", "batch_patch_files", "write_file"):
        props["bypass_validation"] = {
            "type": "boolean",
            "description": "If True, bypasses syntax validation and linting checks. Use with caution.",
            "default": False,
        }
    props["set_timeout"] = {
        "type": "number",
        "description": "Optional timeout in seconds to override the default limit. Use -1 to disable the timeout completely.",
    }
    new_schema = dict(tool.inputSchema)
    new_schema["properties"] = props

    if hasattr(tool, "model_copy"):
        return tool.model_copy(update={"inputSchema": new_schema})

    new_tool = copy.copy(tool)
    new_tool.inputSchema = new_schema
    return new_tool


def get_tool_schemas(expose_bypass: bool = False, show_legacy: bool = False) -> list[Tool]:
    """List all available tools with enriched schemas."""
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

    return [_enrich_tool_schema(tool, expose_bypass) for tool in tools]
