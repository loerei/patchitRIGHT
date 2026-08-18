"""MCP server for patchitRIGHT."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp.server import Server
from mcp.types import TextContent, Tool

from . import __version__
from .guide_content import generate_patchitright_guide
from .patch_file import (
    apply_last_dry_run,
    batch_patch_files,
    patch_file,
    run_startup_recovery,
    write_file,
)
from .tool_schemas import get_tool_schemas

# Create the MCP server instance
server = Server("patchitright-mcp")

DEFAULT_TIMEOUT = 10.0


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    expose_bypass = os.environ.get("PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION", "").lower() in ("true", "1", "yes")
    show_legacy = os.environ.get("PATCHITRIGHT_SHOW_LEGACY", os.environ.get("SHOW_LEGACY", "")).lower() in ("true", "1", "yes")
    return get_tool_schemas(expose_bypass=expose_bypass, show_legacy=show_legacy)


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
            "content": generate_patchitright_guide(__version__, file_type),
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
        return [TextContent(type="text", text=f"Error executing {name}: {e!s}")]


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


def main() -> None:
    """Server entry point."""
    global DEFAULT_TIMEOUT
    parser = argparse.ArgumentParser(description="patchitRIGHT MCP Server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--default-timeout", type=float, default=10.0, help="Default tool execution timeout in seconds (-1 to disable)")
    args, _unknown = parser.parse_known_args()

    DEFAULT_TIMEOUT = args.default_timeout

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
