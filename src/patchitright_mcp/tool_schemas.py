"""MCP tool schema definitions and enrichment utilities for patchitRIGHT."""

from __future__ import annotations

import copy
from typing import Any

from mcp.types import Tool

_SOURCE_SLICE_PROPERTIES: dict[str, Any] = {
    "source_file": {
        "type": "string",
        "description": "Workspace source file path to read or slice instead of writing text inline.",
    },
    "source_start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed inclusive start line from source_file (requires source_end_line; mutually exclusive with source_symbol).",
    },
    "source_end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed inclusive end line from source_file (>= source_start_line).",
    },
    "source_symbol": {
        "type": "string",
        "description": "AST symbol name in source_file to extract completely (mutually exclusive with source_start_line/source_end_line).",
    },
    "source_start_anchor": {
        "type": "string",
        "description": "Substring guard asserted on first extracted line of source_file.",
    },
    "source_end_anchor": {
        "type": "string",
        "description": "Substring guard asserted on last extracted line of source_file.",
    },
    "delete_source": {
        "type": "boolean",
        "default": False,
        "description": "Unlink source_file atomically on unsliced copy (move). Fails on partial slices.",
    },
    "cut_source": {
        "type": "boolean",
        "default": False,
        "description": "Atomically remove extracted lines from source_file and validate it (requires partial slice).",
    },
}

_PATCH_OP_PROPERTIES: dict[str, Any] = {
    "search_content": {
        "type": "string",
        "description": "Exact block to match uniquely within scope unless allow_multiple=true or expected_occurrences > 1.",
    },
    "replace_content": {
        "type": "string",
        "description": "Replacement text. Use '' to delete match or guarded start_line/end_line range.",
    },
    "start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed inclusive start line of search scope or guarded range replacement.",
    },
    "end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "1-indexed inclusive end line of search scope or guarded range replacement (>= start_line).",
    },
    "start_anchor": {
        "type": "string",
        "description": "Substring guard on start_line (required with start_line/end_line when search_content omitted, unless end_anchor or line_filter given).",
    },
    "end_anchor": {
        "type": "string",
        "description": "Substring guard on end_line.",
    },
    "insert_line": {
        "type": "integer",
        "description": "1-indexed line to insert above (1=top, -1=EOF).",
    },
    "insert_content": {
        "type": "string",
        "description": "Text to insert above insert_line.",
    },
    "auto_indent": {
        "type": "boolean",
        "default": True,
        "description": "Match leading indentation of target line.",
    },
    "symbol_name": {
        "type": "string",
        "description": "AST symbol name (function/class) resolved on-demand to scope or replace.",
    },
    "symbol_scope": {
        "type": "string",
        "enum": ["boundary", "full", "body"],
        "default": "boundary",
        "description": (
            "How symbol_name applies: 'boundary' scopes search_content; "
            "'full' replaces entire symbol incl. decorators/export/JSDoc (no search_content); "
            "'body' replaces body only (wrap arrow object returns in parens)."
        ),
    },
    "allow_multiple": {
        "type": "boolean",
        "description": "Replace all matches in scope. Auto-enabled when omitted and expected_occurrences > 1; False with expected_occurrences > 1 fails.",
    },
    "expected_occurrences": {
        "type": "integer",
        "minimum": 1,
        "description": "Exact match count assertion; fails with expected vs actual counts and line numbers on mismatch.",
    },
    "line_filter": {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
        ],
        "description": "Int asserts search_content starts at that 1-indexed line; string asserts scope/range contains substring.",
    },
    **_SOURCE_SLICE_PROPERTIES,
}

_PATCH_OP_DEF: dict[str, Any] = {
    "type": "object",
    "properties": copy.deepcopy(_PATCH_OP_PROPERTIES),
}

_FILE_LEVEL_EXTRAS: dict[str, Any] = {
    "patch_content": {
        "type": "string",
        "description": "Strict unified diff (Fuzz=0); replaces search_content/replace_content.",
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
        "description": "In multi-file mode, skip files with 0 matches (listed in skipped_files); still fails if 0 files match overall.",
    },
    "replacements": {
        "type": "array",
        "items": {"$ref": "#/$defs/PatchOp"},
        "description": "Non-contiguous replacements applied bottom-up in one file.",
    },
}

_BATCH_FILE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "allOf": [{"$ref": "#/$defs/PatchOp"}],
    "properties": {
        "target_file": {
            "type": "string",
            "description": "Target file path (absolute or workspace-relative).",
        },
        "path": {
            "type": "string",
            "description": "Alias for target_file. Target file path.",
        },
        "action": {
            "type": "string",
            "enum": ["patch", "write", "delete"],
            "default": "patch",
            "description": "'patch' (edit existing), 'write' (create/overwrite via code_content or source_file), or 'delete' (unlink atomically).",
        },
        "code_content": {
            "type": "string",
            "description": "File content when action='write' without source_file.",
        },
        "content": {
            "type": "string",
            "description": "Alias for code_content. File content when action='write'.",
        },
        "allow_overwrite": {
            "type": "boolean",
            "default": False,
            "description": "Allow overwriting existing file when action='write'.",
        },
        "overwrite": {
            "type": "boolean",
            "default": False,
            "description": "Alias for allow_overwrite.",
        },
        **copy.deepcopy(_FILE_LEVEL_EXTRAS),
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
            "description": "Target file path (absolute or workspace-relative).",
        },
        "target_files": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Target file paths sharing top-level edit fields (mutually exclusive with target_file, target_glob, files).",
        },
        "target_glob": {
            "type": "string",
            "description": "Workspace-relative glob pattern (e.g. 'src/**/*.ts') expanded across non-ignored files sharing top-level edit fields.",
        },
        **copy.deepcopy(_PATCH_OP_PROPERTIES),
        **copy.deepcopy(_FILE_LEVEL_EXTRAS),
        "dry_run": {
            "type": "boolean",
            "description": "Return unified diff preview without writing to disk.",
            "default": False,
        },
        "files": {
            "type": "array",
            "items": copy.deepcopy(_BATCH_FILE_ITEM_SCHEMA),
            "description": "Per-file operations ('patch', 'write', or 'delete') executed in one atomic transaction; omitted fields inherit top-level defaults.",
        },
    }

    write_file_properties: dict[str, Any] = {
        "target_file": {
            "type": "string",
            "description": "Target file path to create or overwrite.",
        },
        "path": {
            "type": "string",
            "description": "Alias for target_file. Target file path to create or overwrite.",
        },
        "code_content": {
            "type": "string",
            "description": "Complete file content (required unless source_file or content is provided).",
        },
        "content": {
            "type": "string",
            "description": "Alias for code_content. Complete file content.",
        },
        "allow_overwrite": {
            "type": "boolean",
            "description": "Allow overwriting an existing file.",
            "default": False,
        },
        "overwrite": {
            "type": "boolean",
            "description": "Alias for allow_overwrite. Allow overwriting an existing file.",
            "default": False,
        },
        "files": {
            "type": "array",
            "items": copy.deepcopy(_BATCH_FILE_ITEM_SCHEMA),
            "description": (
                "Batch of file write operations executed in one atomic transaction; "
                "each item defaults to action='write'."
            ),
        },
        **copy.deepcopy(_PATCH_OP_PROPERTIES),
        "patch_content": copy.deepcopy(_FILE_LEVEL_EXTRAS["patch_content"]),
        "replacements": copy.deepcopy(_FILE_LEVEL_EXTRAS["replacements"]),
        "did_you_mean": copy.deepcopy(_FILE_LEVEL_EXTRAS["did_you_mean"]),
        "dry_run": {
            "type": "boolean",
            "description": "Validate and cache preview without writing to disk.",
            "default": False,
        },
    }

    tools = [
        Tool(
            name="patch_file",
            description=(
                "Edit one file, broadcast across target_files/target_glob, or run mixed atomic patch/write/delete "
                "batches via 'files'. Supports exact match, AST symbol, guarded line range, unified diff, "
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
            name="apply_last_dry_run",
            description=(
                "Apply changes cached by a prior dry_run=true call (patch_file, write_file, or batch_patch_files). "
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
        ),
        Tool(
            name="write_file",
            description=(
                "Create or overwrite a file from 'code_content' or workspace 'source_file' (whole-file copy/move with delete_source, "
                "line/symbol slice with cut_source, binary copy, or copy-then-patch), or create batches of files via 'files'. "
                "MUST NOT overwrite existing code files for localized edits; use patch_file instead."
            ),
            inputSchema={
                "type": "object",
                "$defs": {"PatchOp": copy.deepcopy(_PATCH_OP_DEF)},
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
