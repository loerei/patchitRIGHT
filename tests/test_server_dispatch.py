"""Comprehensive unit tests for MCP server dispatch, tool routing, and argument validation."""

import json
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from patchitright_mcp.server import (
    _execute_apply_last_dry_run,
    _execute_batch_patch_files,
    _execute_patch_file,
    _execute_write_file,
    call_tool,
    list_tools,
    main,
)


@pytest.mark.asyncio
async def test_list_tools_environment_flags(monkeypatch):
    """Test list_tools with various environment variable toggles."""
    monkeypatch.delenv("PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION", raising=False)
    monkeypatch.delenv("PATCHITRIGHT_SHOW_LEGACY", raising=False)
    monkeypatch.delenv("SHOW_LEGACY", raising=False)

    tools = await list_tools()
    tool_names = [t.name for t in tools]
    assert "patch_file" in tool_names
    assert "write_file" in tool_names
    assert "patchitright_guide" in tool_names
    assert "batch_patch_files" not in tool_names

    # Test exposing bypass validation and legacy batch tool
    monkeypatch.setenv("PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION", "true")
    monkeypatch.setenv("PATCHITRIGHT_SHOW_LEGACY", "true")

    tools_enriched = await list_tools()
    tool_names_enriched = [t.name for t in tools_enriched]
    assert "batch_patch_files" in tool_names_enriched
    patch_tool = next(t for t in tools_enriched if t.name == "patch_file")
    assert "bypass_validation" in patch_tool.inputSchema["properties"]


@pytest.mark.asyncio
async def test_call_tool_unknown_tool():
    """call_tool must raise ValueError for unrecognised tool names."""
    with pytest.raises(ValueError, match="Unknown tool: invalid_tool"):
        await call_tool("invalid_tool", {})


@pytest.mark.asyncio
async def test_call_tool_guide_arguments():
    """patchitright_guide handles string, array, and empty file_type arguments."""
    # 1. No arguments (defaults to ['general'])
    res1 = await call_tool("patchitright_guide", {})
    data1 = json.loads(res1[0].text)
    assert "content" in data1
    assert "patchitright" in data1["content"].lower()

    # 2. String argument
    res2 = await call_tool("patchitright_guide", {"file_type": "python"})
    data2 = json.loads(res2[0].text)
    assert "python" in data2["content"].lower()

    # 3. Array argument
    res3 = await call_tool("patchitright_guide", {"file_type": ["js_ts", "python"]})
    data3 = json.loads(res3[0].text)
    assert "content" in data3


@pytest.mark.asyncio
async def test_call_tool_timeout_parsing_and_trigger(tmp_path):
    """call_tool handles custom timeouts, negative timeouts, and TimeoutError payloads."""
    test_file = tmp_path / "timeout_test.txt"
    test_file.write_text("Hello\n")

    # 1. Invalid string timeout falls back to default without crashing
    res = await call_tool("patch_file", {
        "target_file": str(test_file),
        "search_content": "Hello",
        "replace_content": "World",
        "set_timeout": "invalid_number"
    })
    assert isinstance(res, list)
    assert res[0].type == "text"

    # 2. Negative timeout disables timeout
    await call_tool("patch_file", {
        "target_file": str(test_file),
        "search_content": "World",
        "replace_content": "Universe",
        "set_timeout": -1
    })
    assert "Universe" in test_file.read_text()

    # 3. Microsecond timeout triggers TimeoutError payload
    with patch("patchitright_mcp.server._execute_patch_file") as mock_exec:
        import time
        def slow_exec(args):
            time.sleep(0.1)
            return [TextContent(type="text", text="done")]
        mock_exec.side_effect = slow_exec

        res_timeout = await call_tool("patch_file", {
            "target_file": str(test_file),
            "search_content": "Universe",
            "replace_content": "Cosmos",
            "set_timeout": 0.001
        })
        error_payload = json.loads(res_timeout[0].text)
        assert error_payload["success"] is False
        assert "TimeoutError" in error_payload["error"]
        assert error_payload["details"]["tool"] == "patch_file"


@pytest.mark.asyncio
async def test_call_tool_unhandled_exception():
    """call_tool catches unhandled exceptions and formats error text."""
    with patch("patchitright_mcp.server._execute_patch_file", side_effect=RuntimeError("Fatal DB Error")):
        res = await call_tool("patch_file", {"target_file": "dummy.py"})
        assert "Error executing patch_file: Fatal DB Error" in res[0].text


def test_execute_write_file_validation(tmp_path):
    """_execute_write_file validates required arguments."""
    # 1. Missing target_file
    res1 = _execute_write_file({})
    assert "Error: target_file is required." in res1[0].text

    # 2. Missing code_content
    res2 = _execute_write_file({"target_file": str(tmp_path / "out.txt")})
    assert "Error: code_content is required." in res2[0].text

    # 3. Successful write
    target = tmp_path / "out.txt"
    res3 = _execute_write_file({
        "target_file": str(target),
        "code_content": "Hello World\n",
        "allow_overwrite": True,
        "dry_run": False
    })
    data = json.loads(res3[0].text)
    assert data["success"] is True
    assert target.read_text() == "Hello World\n"


def test_execute_apply_last_dry_run_validation():
    """_execute_apply_last_dry_run validates run_id."""
    res1 = _execute_apply_last_dry_run({})
    assert "Error: run_id is required for apply_last_dry_run." in res1[0].text

    with patch("patchitright_mcp.server.apply_last_dry_run", return_value={"success": True, "dryRun": False}) as mock_apply:
        res2 = _execute_apply_last_dry_run({"run_id": "test_run_123"})
        mock_apply.assert_called_once_with(run_id="test_run_123")
        data = json.loads(res2[0].text)
        assert data["success"] is True


def test_execute_batch_patch_files_validation():
    """_execute_batch_patch_files validates patches parameter."""
    res1 = _execute_batch_patch_files({})
    assert "Error: patches array is required for batch_patch_files." in res1[0].text

    with patch("patchitright_mcp.server.batch_patch_files", return_value={"success": True}) as mock_batch:
        res2 = _execute_batch_patch_files({"patches": [{"target_file": "a.txt", "patch_content": "@@ -1 +1 @@"}]})
        mock_batch.assert_called_once()
        data = json.loads(res2[0].text)
        assert data["success"] is True


def test_execute_patch_file_validation(tmp_path):
    """_execute_patch_file validates target_file, replacements, and symbol scope."""
    # 1. Missing target_file and files
    res1 = _execute_patch_file({})
    assert "Error: Either 'target_file' or 'files' is required." in res1[0].text

    # 2. Symbol scope full/body missing symbol_name or replace_content
    res2 = _execute_patch_file({
        "target_file": "foo.py",
        "symbol_scope": "full",
        "replace_content": "def bar(): pass"
    })
    assert "Error: Both symbol_name and replace_content are required" in res2[0].text

    # 3. Missing replacement directives
    res3 = _execute_patch_file({
        "target_file": "foo.py",
        "symbol_scope": "boundary"
    })
    assert "Error: Either replacements, patch_content, insert_content, OR both search_content and replace_content are required." in res3[0].text

    # 4. Line filter parsing (integer and string)
    target = tmp_path / "sample.py"
    target.write_text("a = 1\nb = 2\nc = 3\n")

    res_line_int = _execute_patch_file({
        "target_file": str(target),
        "search_content": "b = 2",
        "replace_content": "b = 20",
        "line_filter": 2
    })
    assert json.loads(res_line_int[0].text)["success"] is True

    res_line_str = _execute_patch_file({
        "target_file": str(target),
        "search_content": "b = 20",
        "replace_content": "b = 200",
        "line_filter": "b = 20"
    })
    assert json.loads(res_line_str[0].text)["success"] is True


def test_main_cli_arguments_and_env(monkeypatch):
    """main parses CLI flags, honors environment variable timeouts, and runs startup recovery."""
    with patch("patchitright_mcp.server.run_startup_recovery") as mock_recovery, \
         patch("mcp.server.stdio.stdio_server"), \
         patch("asyncio.run"):

        # 1. CLI default-timeout flag
        monkeypatch.setattr("sys.argv", ["server.py", "--default-timeout", "25.5"])
        main()
        mock_recovery.assert_called()

        # 2. Environment variable timeout override
        monkeypatch.setenv("PATCHITRIGHT_DEFAULT_TIMEOUT", "45.0")
        monkeypatch.setattr("sys.argv", ["server.py"])
        main()
