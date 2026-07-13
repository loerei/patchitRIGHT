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


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    import os
    expose_bypass = os.environ.get("PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION", "").lower() in ("true", "1", "yes")

    tools = [
        Tool(
            name="patch_file",
            description=(
                "Edit a file by replacing an exact text block (search_content/replace_content) "
                "or applying a unified diff (patch_content). Optionally scope to a line range or AST symbol. "
                "CRITICAL: Do not pass large blocks of code (over 50 lines) into search_content/replace_content. "
                "Instead, use 'symbol_name' to target class/function boundaries for safer, faster edits with less token overhead."
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
                        "description": "The exact string block to search for. Must match uniquely within the scope unless allow_multiple is True. Avoid passing blocks larger than 50 lines; use 'symbol_name' instead."
                    },
                    "replace_content": {
                        "type": "string",
                        "description": "The string block to replace the search content with. Avoid passing blocks larger than 50 lines; use 'symbol_name' instead."
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
                                "allow_multiple": {"type": "boolean", "description": "If True, replaces all occurrences of search_content within the scope. Defaults to False."},
                                "line_filter": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "integer"}
                                    ],
                                    "description": "Optional assertion (line number or substring check)."
                                }
                            },
                            "required": ["search_content", "replace_content"]
                        },
                        "description": "Optional list of replacements to apply in a single call to the same file. Applied bottom-up to avoid line-drift."
                    }
                },
                "required": ["target_file"]
            }
        ),
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
                "Create a new file or fully overwrite an existing file. "
                "Automatically runs syntax validation and linting on the content before writing."
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
        )
    ]

    if expose_bypass:
        for tool in tools:
            if tool.name in ("patch_file", "batch_patch_files", "write_file"):
                tool.inputSchema["properties"]["bypass_validation"] = {
                    "type": "boolean",
                    "description": "If True, bypasses syntax validation and linting checks. Use with caution.",
                    "default": False
                }

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute the requested tool."""
    if name not in ("patch_file", "batch_patch_files", "apply_last_dry_run", "write_file"):
        raise ValueError(f"Unknown tool: {name}")

    try:
        if name == "apply_last_dry_run":
            return _execute_apply_last_dry_run(arguments)
        elif name == "batch_patch_files":
            return _execute_batch_patch_files(arguments)
        elif name == "write_file":
            return _execute_write_file(arguments)
        else:
            return _execute_patch_file(arguments)
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
    target_file = arguments.get("target_file")
    search_content = arguments.get("search_content")
    replace_content = arguments.get("replace_content")
    patch_content = arguments.get("patch_content")
    
    if not target_file:
        return [TextContent(type="text", text="Error: target_file is required.")]

    replacements = arguments.get("replacements")

    if patch_content is None and replacements is None and (search_content is None or replace_content is None):
        return [TextContent(type="text", text="Error: Either replacements, patch_content, OR both search_content and replace_content are required.")]

    folder_filter = arguments.get("folder_filter")
    file_filter = arguments.get("file_filter")
    start_line = int(arguments.get("start_line")) if arguments.get("start_line") is not None else None
    end_line = int(arguments.get("end_line")) if arguments.get("end_line") is not None else None
    symbol_name = arguments.get("symbol_name")
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
    )

    return [TextContent(type="text", text=json.dumps(res, indent=2))]


def main() -> None:
    """Server entry point."""
    parser = argparse.ArgumentParser(description="patchitRIGHT MCP Server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args()

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
