"""MCP tool schema definitions and enrichment utilities for patchitRIGHT."""

from __future__ import annotations

import copy
from typing import Any

from mcp.types import Tool

_PATCH_SLICE_PROPERTIES: dict[str, Any] = {
    "source_file": {
        "type": "string",
        "description": "Workspace file path to read or slice instead of inline text.",
    },
    "source_start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed start line from source_file (requires source_end_line).",
    },
    "source_end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed inclusive end line from source_file (>= source_start_line).",
    },
    "source_symbol": {
        "type": "string",
        "description": "Symbol name in source_file to extract completely.",
    },
    "source_start_anchor": {
        "type": "string",
        "description": "Substring guard on first extracted line of source_file.",
    },
    "source_end_anchor": {
        "type": "string",
        "description": "Substring guard on last extracted line of source_file.",
    },
    "cut_source": {
        "type": "boolean",
        "default": False,
        "description": "Remove extracted slice from source_file atomically.",
    },
}

_WRITE_SLICE_PROPERTIES: dict[str, Any] = {
    **_PATCH_SLICE_PROPERTIES,
    "delete_source": {
        "type": "boolean",
        "default": False,
        "description": "Unlink source_file atomically on whole-file move.",
    },
}

_PATCH_OP_PROPERTIES: dict[str, Any] = {
    "search_content": {
        "type": "string",
        "description": "Exact block to match uniquely in scope unless allow_multiple=true or expected_occurrences > 1.",
    },
    "replace_content": {
        "type": "string",
        "description": "Replacement text ('' deletes match or guarded line range).",
    },
    "start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed start line of search scope or guarded range.",
    },
    "end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed inclusive end line (>= start_line).",
    },
    "start_anchor": {
        "type": "string",
        "description": "Substring guard on start_line.",
    },
    "end_anchor": {
        "type": "string",
        "description": "Substring guard on end_line.",
    },
    "insert_line": {
        "type": "integer",
        "description": "1-indexed line to insert above (1=top of file, -1=append at EOF).",
    },
    "insert_content": {
        "type": "string",
        "description": "Text to insert at insert_line.",
    },
    "auto_indent": {
        "type": "boolean",
        "default": True,
        "description": "Match indentation of target line.",
    },
    "symbol_name": {
        "type": "string",
        "description": "Symbol name (function/class) resolved on-demand to scope or replace.",
    },
    "symbol_scope": {
        "type": "string",
        "enum": ["boundary", "full", "body"],
        "default": "boundary",
        "description": "'boundary' scopes search_content; 'full' replaces symbol incl. decorators/export; 'body' replaces body only.",
    },
    "allow_multiple": {
        "type": "boolean",
        "description": "Replace all matches in scope.",
    },
    "expected_occurrences": {
        "type": "integer",
        "minimum": 1,
        "description": "Exact match count assertion.",
    },
    "line_filter": {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
        ],
        "description": "Int asserts match starts at that line; string asserts scope contains substring.",
    },
}

_PATCH_OP_DEF: dict[str, Any] = {
    "type": "object",
    "properties": copy.deepcopy(_PATCH_OP_PROPERTIES),
}

_FILE_LEVEL_EXTRAS: dict[str, Any] = {
    "patch_content": {
        "type": "string",
        "description": "Strict unified diff (Fuzz=0); mutually exclusive with search_content, replacements, and insert_line.",
    },
    "folder_filter": {
        "type": "string",
        "description": "Assert target file resides in this subdirectory.",
    },
    "file_filter": {
        "type": "string",
        "description": "Assert target file name contains this substring.",
    },
    "did_you_mean": {
        "type": "boolean",
        "default": False,
        "description": "Apply closest match when similarity >= 80%.",
    },
    "skip_unmatched": {
        "type": "boolean",
        "default": False,
        "description": "In multi-file mode, skip files with 0 matches.",
    },
    "replacements": {
        "type": "array",
        "items": {"$ref": "#/$defs/PatchOp"},
        "description": "Non-contiguous replacements applied bottom-up in one file.",
    },
}

_WRITE_CORE_PROPERTIES: dict[str, Any] = {
    "target_file": {
        "type": "string",
        "description": "Target file path to create or overwrite.",
    },
    "path": {
        "type": "string",
        "description": "Alias for target_file.",
    },
    "code_content": {
        "type": "string",
        "description": "Complete file content (required unless source_file or content is provided).",
    },
    "content": {
        "type": "string",
        "description": "Alias for code_content.",
    },
    "allow_overwrite": {
        "type": "boolean",
        "description": "Allow overwriting an existing file.",
        "default": False,
    },
    "overwrite": {
        "type": "boolean",
        "description": "Alias for allow_overwrite.",
        "default": False,
    },
    **copy.deepcopy(_WRITE_SLICE_PROPERTIES),
}

_WRITE_BATCH_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Accepts any top-level write_file property per file.",
    "properties": {
        "target_file": {"type": "string"},
        "path": {"type": "string"},
        "code_content": {"type": "string"},
        "content": {"type": "string"},
        "source_file": {"type": "string"},
        "allow_overwrite": {"type": "boolean", "default": False},
        "overwrite": {"type": "boolean", "default": False},
    },
    "anyOf": [
        {"required": ["target_file"]},
        {"required": ["path"]},
    ],
}

_PATCH_BATCH_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Accepts any top-level patch_file or write_file property per file; omitted fields inherit top-level defaults.",
    "allOf": [{"$ref": "#/$defs/PatchOp"}],
    "properties": {
        "target_file": {"type": "string"},
        "path": {"type": "string"},
        "action": {
            "type": "string",
            "enum": ["patch", "write", "delete"],
            "default": "patch",
        },
        "code_content": {"type": "string"},
        "content": {"type": "string"},
        "allow_overwrite": {"type": "boolean", "default": False},
        "overwrite": {"type": "boolean", "default": False},
        "replacements": {
            "type": "array",
            "items": {"$ref": "#/$defs/PatchOp"},
        },
    },
    "anyOf": [
        {"required": ["target_file"]},
        {"required": ["path"]},
    ],
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
        "description": "Optional timeout in seconds (-1 disables timeout).",
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
            "description": "Target file path (absolute or workspace-relative).",
        },
        "target_files": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Target file paths sharing top-level edit fields.",
        },
        "target_glob": {
            "type": "string",
            "description": "Workspace-relative glob pattern (e.g. 'src/**/*.ts') sharing top-level edit fields.",
        },
        **copy.deepcopy(_PATCH_OP_PROPERTIES),
        **copy.deepcopy(_PATCH_SLICE_PROPERTIES),
        **copy.deepcopy(_FILE_LEVEL_EXTRAS),
        "files": {
            "type": "array",
            "items": copy.deepcopy(_PATCH_BATCH_ITEM_SCHEMA),
            "description": "Per-file operations ('patch', 'write', or 'delete') executed in one atomic transaction.",
        },
    }

    write_file_properties: dict[str, Any] = {
        **copy.deepcopy(_WRITE_CORE_PROPERTIES),
        "files": {
            "type": "array",
            "items": copy.deepcopy(_WRITE_BATCH_ITEM_SCHEMA),
            "description": "Batch of file write/copy/slice operations executed in one atomic transaction.",
        },
    }

    if show_legacy:
        patch_file_properties["dry_run"] = {
            "type": "boolean",
            "description": "Return unified diff preview without writing to disk.",
            "default": False,
        }
        write_file_properties["dry_run"] = {
            "type": "boolean",
            "description": "Validate and cache preview without writing to disk.",
            "default": False,
        }

    tools = [
        Tool(
            name="patch_file",
            description=(
                "Edit one file, broadcast across target_files/target_glob, or run mixed atomic patch/write/delete "
                "batches via 'files'. Supports exact match, symbol_name, guarded line range, unified diff, "
                "expected_occurrences, and source_file slice payloads."
            ),
            inputSchema={
                "type": "object",
                "$defs": {"PatchOp": copy.deepcopy(_PATCH_OP_DEF)},
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
            name="write_file",
            description=(
                "Create or overwrite a file from 'code_content' ('content') or workspace 'source_file' (whole-file copy/move with delete_source, "
                "line/symbol slice with cut_source, or binary copy), or create atomic batches of files via 'files'. "
                "For localized edits to existing code files, use patch_file instead."
            ),
            inputSchema={
                "type": "object",
                "properties": write_file_properties,
                "anyOf": [
                    {"required": ["code_content"]},
                    {"required": ["content"]},
                    {"required": ["source_file"]},
                    {"required": ["files"]},
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
                name="apply_last_dry_run",
                description=(
                    "Apply changes cached by a prior dry_run=true call (patch_file or write_file). "
                    "Fails if expired (300 s TTL) or any involved file changed on disk."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "run_id from the prior dry_run=true response.",
                        }
                    },
                    "required": ["run_id"],
                },
            )
        )
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
                    },
                    "required": ["patches"],
                },
            )
        )

    return [_enrich_tool_schema(tool, expose_bypass) for tool in tools]
