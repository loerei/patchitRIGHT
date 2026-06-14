"""MCP server for patchitRIGHT."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.types import Tool, TextContent

from . import __version__
from .patch_file import patch_file, batch_patch_files, run_startup_recovery, apply_last_dry_run


# Create the MCP server instance
server = Server("patchitright-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="patch_file",
            description=(
                "Perform a robust, AST-bounded search-and-replace edit on a target file. "
                "Can be optionally scoped to a line range or a specific AST symbol (function/class) "
                "using jCodeMunch index. Includes safety occurrence checks, workspace-scoped path "
                "protection for relative paths, and dry-run preview."
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
                        "description": "The exact string block to search for. Must match uniquely within the scope unless allow_multiple is True."
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
                    "dry_run": {
                        "type": "boolean",
                        "description": "If True, returns a unified diff preview of the changes without modifying the file. Defaults to False.",
                        "default": False
                    },
                    "storage_path": {
                        "type": "string",
                        "description": "Optional custom path to the jCodeMunch SQLite index database."
                    }
                },
                "required": ["target_file"]
            }
        ),
        Tool(
            name="batch_patch_files",
            description=(
                "Perform an atomic, transactional refactoring operation across multiple target files. "
                "Applies Git-style Unified Diffs (Fuzz = 0) with a safety lock: if any patch fails, "
                "the entire transaction is rolled back safely, leaving no corrupted files. "
                "Includes crash-resilient ephemeral backup files, optimistic hash-locking to prevent "
                "concurrency conflicts, and dry-run diff preview."
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
                        "description": "Optional custom path to the jCodeMunch SQLite index database."
                    }
                },
                "required": ["patches"]
            }
        ),
        Tool(
            name="apply_last_dry_run",
            description=(
                "Commit a patch that was previewed with dry_run=true, using only its run_id. "
                "Avoids resending search_content / replace_content / patch_content, "
                "cutting token usage roughly in half for the apply step. "
                "Fails with a clear error if the run_id is unknown, expired (TTL 300 s), "
                "or if any target file was modified after the dry-run (hash guard)."
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
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute the requested tool."""
    if name not in ("patch_file", "batch_patch_files", "apply_last_dry_run"):
        raise ValueError(f"Unknown tool: {name}")

    if name == "apply_last_dry_run":
        try:
            run_id = arguments.get("run_id")
            if not run_id:
                return [TextContent(type="text", text="Error: run_id is required for apply_last_dry_run.")]
            res = apply_last_dry_run(run_id=run_id)
            import json
            return [TextContent(type="text", text=json.dumps(res, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error executing apply_last_dry_run: {str(e)}")]

    if name == "batch_patch_files":
        try:
            patches = arguments.get("patches")
            if not patches:
                return [TextContent(type="text", text="Error: patches array is required for batch_patch_files.")]
            dry_run = bool(arguments.get("dry_run", False))
            storage_path = arguments.get("storage_path")
            
            res = batch_patch_files(
                patches=patches,
                dry_run=dry_run,
                storage_path=storage_path
            )
            import json
            return [TextContent(type="text", text=json.dumps(res, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error executing batch_patch_files: {str(e)}")]

    try:
        # Coerce/extract arguments
        target_file = arguments.get("target_file")
        search_content = arguments.get("search_content")
        replace_content = arguments.get("replace_content")
        patch_content = arguments.get("patch_content")
        
        if not target_file:
            return [TextContent(type="text", text="Error: target_file is required.")]

        if patch_content is None and (search_content is None or replace_content is None):
            return [TextContent(type="text", text="Error: Either patch_content OR both search_content and replace_content are required.")]

        folder_filter = arguments.get("folder_filter")
        file_filter = arguments.get("file_filter")
        
        start_line = arguments.get("start_line")
        if start_line is not None:
            start_line = int(start_line)
            
        end_line = arguments.get("end_line")
        if end_line is not None:
            end_line = int(end_line)
            
        symbol_name = arguments.get("symbol_name")
        allow_multiple = bool(arguments.get("allow_multiple", False))
        
        line_filter = arguments.get("line_filter")
        if line_filter is not None:
            try:
                line_filter = int(line_filter)
            except (ValueError, TypeError):
                line_filter = str(line_filter)
                
        dry_run = bool(arguments.get("dry_run", False))
        storage_path = arguments.get("storage_path")

        # Invoke the robust patch_file implementation
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
        )

        import json
        return [TextContent(type="text", text=json.dumps(res, indent=2))]

    except Exception as e:
        return [TextContent(type="text", text=f"Error executing patch_file: {str(e)}")]


def main() -> None:
    """Server entry point."""
    parser = argparse.ArgumentParser(description="patchitRIGHT MCP Server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

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
