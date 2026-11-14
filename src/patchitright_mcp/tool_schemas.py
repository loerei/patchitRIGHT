"""MCP tool schema definitions and enrichment utilities for patchitRIGHT."""

from __future__ import annotations

import copy
from typing import Any

from mcp.types import Tool

STORAGE_PATH_DESC = "Optional custom path to the jCodeMunch SQLite index database."

_SOURCE_SLICE_PROPERTIES: dict[str, Any] = {
    "source_file": {
        "type": "string",
        "description": (
            "Optional workspace source file path to read content or extract a slice from instead of regenerating text inline. "
            "Forward slashes (/) recommended."
        ),
    },
    "source_start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "Optional 1-indexed inclusive start line to extract from source_file (requires source_end_line; mutually exclusive with source_symbol).",
    },
    "source_end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "Optional 1-indexed inclusive end line to extract from source_file (must be >= source_start_line).",
    },
    "source_symbol": {
        "type": "string",
        "description": "Optional AST symbol name in source_file to extract completely (mutually exclusive with source_start_line/source_end_line).",
    },
    "source_start_anchor": {
        "type": "string",
        "description": "Optional substring guard asserted against the first extracted line of source_file.",
    },
    "source_end_anchor": {
        "type": "string",
        "description": "Optional substring guard asserted against the last extracted line of source_file.",
    },
    "delete_source": {
        "type": "boolean",
        "default": False,
        "description": "If True with an unsliced whole-file source_file copy, atomically unlinks source_file (move semantics). Rejected on partial slices.",
    },
    "cut_source": {
        "type": "boolean",
        "default": False,
        "description": "If True with a partial source_file slice, atomically removes extracted lines from source_file and validates it in the same transaction.",
    },
}

_PATCH_OP_PROPERTIES: dict[str, Any] = {
    "search_content": {
        "type": "string",
        "description": "The exact string block to search for. Must match uniquely within the scope unless allow_multiple or expected_occurrences > 1 is set.",
    },
    "replace_content": {
        "type": "string",
        "description": "The replacement string block. May be empty ('') for deletions or guarded start_line/end_line range removals.",
    },
    "start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "Optional 1-indexed inclusive starting line number of the scope to search or guarded line-range replacement.",
    },
    "end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "Optional 1-indexed inclusive ending line number of the scope to search or guarded line-range replacement (must be >= start_line).",
    },
    "start_anchor": {
        "type": "string",
        "description": "Optional substring guard asserted on start_line (required with start_line/end_line when search_content is omitted, unless end_anchor or line_filter is given).",
    },
    "end_anchor": {
        "type": "string",
        "description": "Optional substring guard asserted on end_line.",
    },
    "insert_line": {
        "type": "integer",
        "description": "Optional 1-indexed target line number to insert content above. Use 1 for top of file, or -1 to append at end-of-file.",
    },
    "insert_content": {
        "type": "string",
        "description": "Text content to insert directly above insert_line.",
    },
    "auto_indent": {
        "type": "boolean",
        "default": True,
        "description": "If True, automatically matches leading indentation of the target line. Defaults to True.",
    },
    "symbol_name": {
        "type": "string",
        "description": "Optional AST symbol name (e.g. function or class name) to scope the search or replacement to. Resolves boundaries via jCodeMunch index.",
    },
    "symbol_scope": {
        "type": "string",
        "enum": ["boundary", "full", "body"],
        "default": "boundary",
        "description": (
            "Controls how 'symbol_name' is used. "
            "'boundary' (default): scopes search_content matching to symbol's line range. "
            "'full': replaces the entire symbol (signature+body) with replace_content or sliced source_file, no search_content needed. "
            "WARNING: 'full' includes decorators, 'export' keywords, and JSDoc — include these in replace_content. "
            "'body': replaces only the function body, preserving the signature. For arrow expressions returning an object literal, wrap in parentheses: ({ key: value })."
        ),
    },
    "allow_multiple": {
        "type": "boolean",
        "description": "If True, replaces all occurrences of search_content within the scope. When omitted and expected_occurrences > 1, multi-match is enabled automatically; explicitly passing False with expected_occurrences > 1 fails.",
    },
    "expected_occurrences": {
        "type": "integer",
        "minimum": 1,
        "description": "Exact match count assertion (>= 1). Fails with expected vs actual counts and line numbers if the match count differs.",
    },
    "line_filter": {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
        ],
        "description": "Optional assertion. If an integer, asserts search_content starts at this 1-indexed line. If a string, asserts the resolved scope or line range contains this substring.",
    },
    **_SOURCE_SLICE_PROPERTIES,
}

_REPLACEMENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": copy.deepcopy(_PATCH_OP_PROPERTIES),
}

_FILE_LEVEL_EXTRAS: dict[str, Any] = {
    "patch_content": {
        "type": "string",
        "description": "Optional strict unified diff patch content (Fuzz = 0). If provided, search_content and replace_content are not required.",
    },
    "folder_filter": {
        "type": "string",
        "description": "Optional subdirectory filter. The target file must reside inside this subdirectory.",
    },
    "file_filter": {
        "type": "string",
        "description": "Optional file name substring filter. The target file name must contain this substring.",
    },
    "did_you_mean": {
        "type": "boolean",
        "default": False,
        "description": "If True, automatically applies the replacement to the closest matching block if similarity >= 80%. Defaults to False.",
    },
    "skip_unmatched": {
        "type": "boolean",
        "default": False,
        "description": "If True in multi-file mode, skips files where search_content has 0 matches (reported in skipped_files). Still fails if 0 files match overall.",
    },
    "replacements": {
        "type": "array",
        "items": copy.deepcopy(_REPLACEMENT_ITEM_SCHEMA),
        "description": "Optional list of non-contiguous replacements to apply bottom-up in a single file.",
    },
}

_BATCH_FILE_ITEM_PROPERTIES: dict[str, Any] = {
    "target_file": {
        "type": "string",
        "description": "Target file path (absolute or relative to workspace).",
    },
    "action": {
        "type": "string",
        "enum": ["patch", "write", "delete"],
        "default": "patch",
        "description": "Operation for this item: 'patch' (default, edit existing file), 'write' (create/overwrite from code_content or source_file), or 'delete' (unlink atomically).",
    },
    "code_content": {
        "type": "string",
        "description": "File content when action='write' without source_file.",
    },
    "allow_overwrite": {
        "type": "boolean",
        "default": False,
        "description": "If True, allows overwriting an existing file when action='write'.",
    },
    **copy.deepcopy(_PATCH_OP_PROPERTIES),
    **copy.deepcopy(_FILE_LEVEL_EXTRAS),
}


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
    patch_file_properties: dict[str, Any] = {
        "target_file": {
            "type": "string",
            "description": "Absolute (any file) or relative (active workspace) single target file path. Forward slashes (/) recommended.",
        },
        "target_files": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Optional list of target file paths sharing top-level edit fields (broadcast mode). Mutually exclusive with target_file, target_glob, and files.",
        },
        "target_glob": {
            "type": "string",
            "description": "Optional workspace-relative glob pattern (e.g. 'src/**/*.ts') expanded deterministically across non-ignored files sharing top-level edit fields.",
        },
        **copy.deepcopy(_PATCH_OP_PROPERTIES),
        **copy.deepcopy(_FILE_LEVEL_EXTRAS),
        "dry_run": {
            "type": "boolean",
            "description": "If True, returns a unified diff preview of the changes without modifying disk. Defaults to False.",
            "default": False,
        },
        "storage_path": {
            "type": "string",
            "description": STORAGE_PATH_DESC,
        },
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _BATCH_FILE_ITEM_PROPERTIES,
                "required": ["target_file"],
            },
            "description": "Optional array of per-file operations ('patch', 'write', or 'delete') executed in one atomic transaction. Omitted edit fields inherit top-level defaults.",
        },
    }

    write_file_properties: dict[str, Any] = {
        "target_file": {
            "type": "string",
            "description": "Absolute or relative path to the file to create or overwrite. Forward slashes (/) recommended.",
        },
        "code_content": {
            "type": "string",
            "description": "The complete content of the file (required unless source_file is provided).",
        },
        "allow_overwrite": {
            "type": "boolean",
            "description": "If True, allows overwriting an existing file. Defaults to False, which blocks the write if the file already exists.",
            "default": False,
        },
        **copy.deepcopy(_PATCH_OP_PROPERTIES),
        "patch_content": copy.deepcopy(_FILE_LEVEL_EXTRAS["patch_content"]),
        "replacements": copy.deepcopy(_FILE_LEVEL_EXTRAS["replacements"]),
        "did_you_mean": copy.deepcopy(_FILE_LEVEL_EXTRAS["did_you_mean"]),
        "dry_run": {
            "type": "boolean",
            "description": "If True, runs syntax validation and linting and caches a preview without writing to disk. Defaults to False.",
            "default": False,
        },
        "storage_path": {
            "type": "string",
            "description": STORAGE_PATH_DESC,
        },
    }

    tools = [
        Tool(
            name="patch_file",
            description=(
                "Edit one file, broadcast edits across target_files/target_glob, or run mixed atomic patch/write/delete "
                "batches via 'files'. Supports exact text blocks, AST symbol replacement, guarded line-range replacement, "
                "strict unified diffs, expected_occurrences assertions, and cross-file source_file slice payloads."
            ),
            inputSchema={
                "type": "object",
                "properties": patch_file_properties,
                "anyOf": [
                    {"required": ["target_file"]},
                    {"required": ["target_files"]},
                    {"required": ["target_glob"]},
                    {"required": ["files"]},
                ],
            },
        ),
        Tool(
            name="apply_last_dry_run",
            description=(
                "Apply the patch, copy/move, or batch cached by a previous dry_run=true call (patch_file, write_file, or batch_patch_files). "
                "Requires the run_id from that response. Fails if expired (300 s TTL) or if any involved file changed on disk."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The run_id returned by a previous dry_run=true call.",
                    }
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="write_file",
            description=(
                "Create or overwrite a file from 'code_content' or a workspace 'source_file' (whole-file copy/move with delete_source, "
                "partial line/symbol slice with optional cut_source, binary pure copy, or copy-then-patch inline transforms). "
                "MUST NOT overwrite existing code files just to make localized edits; use patch_file instead."
            ),
            inputSchema={
                "type": "object",
                "properties": write_file_properties,
                "required": ["target_file"],
                "anyOf": [
                    {"required": ["code_content"]},
                    {"required": ["source_file"]},
                ],
            },
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
                            "enum": ["general", "js_ts", "html_css", "python"],
                        },
                        "description": "Optional list of file/language types to retrieve clean-code style rules.",
                        "default": ["general"],
                    }
                },
            },
        ),
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
                                        "description": "Absolute (any file) or relative (active workspace) path. Forward slashes (/) recommended.",
                                    },
                                    "patch_content": {
                                        "type": "string",
                                        "description": "The exact Git-style Unified Diff hunk(s) to apply to this file.",
                                    },
                                },
                                "required": ["target_file", "patch_content"],
                            },
                            "description": "List of target files and their corresponding Unified Diffs.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "If True, returns a unified diff preview of the changes without modifying the files. Defaults to False.",
                            "default": False,
                        },
                        "storage_path": {
                            "type": "string",
                            "description": STORAGE_PATH_DESC,
                        },
                    },
                    "required": ["patches"],
                },
            )
        )

    return [_enrich_tool_schema(tool, expose_bypass) for tool in tools]
