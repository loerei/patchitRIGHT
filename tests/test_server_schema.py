import os
from unittest.mock import patch
import pytest

from patchitright_mcp.server import list_tools


@pytest.mark.asyncio
async def test_list_tools_schema_without_bypass():
    with patch.dict(os.environ, {"PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION": "false"}):
        tools = await list_tools()
        for tool in tools:
            # bypass_validation should NOT be in the properties
            properties = tool.inputSchema.get("properties", {})
            assert "bypass_validation" not in properties


@pytest.mark.asyncio
async def test_list_tools_schema_with_bypass():
    with patch.dict(os.environ, {"PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION": "true"}):
        tools = await list_tools()
        for tool in tools:
            properties = tool.inputSchema.get("properties", {})
            if tool.name in ("patch_file", "batch_patch_files", "write_file"):
                assert "bypass_validation" in properties
                assert properties["bypass_validation"]["type"] == "boolean"
            else:
                assert "bypass_validation" not in properties


@pytest.mark.asyncio
async def test_patchitright_guide():
    from patchitright_mcp.server import list_tools, call_tool
    tools = await list_tools()
    guide_tool = next((t for t in tools if t.name == "patchitright_guide"), None)
    assert guide_tool is not None
    assert list(guide_tool.inputSchema["properties"].keys()) == ["set_timeout"]

    # Call the tool
    results = await call_tool("patchitright_guide", {})
    assert len(results) == 1
    import json
    data = json.loads(results[0].text)
    assert "version" in data
    assert "content" in data
    assert "patchitright-mcp" in data["content"]
    assert "patch_file" in data["content"]

