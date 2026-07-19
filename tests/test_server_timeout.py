import pytest
import json
from unittest.mock import patch
from mcp.types import TextContent
from patchitright_mcp.server import call_tool

@pytest.mark.asyncio
async def test_tool_call_success():
    with patch("patchitright_mcp.server._execute_patch_file", return_value=[TextContent(type="text", text='{"success": true}')]):
        res = await call_tool("patch_file", {"target_file": "test.txt", "set_timeout": 2})
        assert len(res) == 1
        assert isinstance(res[0], TextContent)
        data = json.loads(res[0].text)
        assert data["success"] is True

@pytest.mark.asyncio
async def test_tool_call_timeout():
    import time
    def slow_sync_executor(*args, **kwargs):
        time.sleep(1.5)
        return [TextContent(type="text", text='{"success": true}')]

    with patch("patchitright_mcp.server._execute_patch_file", side_effect=slow_sync_executor):
        res = await call_tool("patch_file", {"target_file": "test.txt", "set_timeout": 0.1})
        assert len(res) == 1
        data = json.loads(res[0].text)
        assert data["success"] is False
        assert "TimeoutError" in data["error"]
        assert "The operation timed out during verification" in data["details"]["suggestion"]

@pytest.mark.asyncio
async def test_tool_call_no_timeout():
    import time
    def slow_sync_executor(*args, **kwargs):
        time.sleep(0.3)
        return [TextContent(type="text", text='{"success": true}')]

    with patch("patchitright_mcp.server._execute_patch_file", side_effect=slow_sync_executor):
        res = await call_tool("patch_file", {"target_file": "test.txt", "set_timeout": -1})
        assert len(res) == 1
        data = json.loads(res[0].text)
        assert data["success"] is True
