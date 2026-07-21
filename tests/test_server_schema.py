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
    assert set(guide_tool.inputSchema["properties"].keys()) == {"set_timeout", "file_type"}
    assert guide_tool.inputSchema["properties"]["file_type"]["type"] == "array"

    # Call the tool (general fallback)
    results = await call_tool("patchitright_guide", {})
    assert len(results) == 1
    import json
    data = json.loads(results[0].text)
    assert "version" in data
    assert "content" in data
    assert "patchitright-mcp" in data["content"]
    assert "JavaScript / TypeScript Clean-Code" not in data["content"]

    # Call the tool (single string js_ts - for robustness / backward compatibility)
    results_js = await call_tool("patchitright_guide", {"file_type": "js_ts"})
    data_js = json.loads(results_js[0].text)
    assert "JavaScript / TypeScript Clean-Code" in data_js["content"]
    assert "Native Imports" in data_js["content"]

    # Call the tool (single string html_css)
    results_html = await call_tool("patchitright_guide", {"file_type": "html_css"})
    data_html = json.loads(results_html[0].text)
    assert "HTML / CSS Accessibility" in data_html["content"]

    # Call the tool (single string python)
    results_py = await call_tool("patchitright_guide", {"file_type": "python"})
    data_py = json.loads(results_py[0].text)
    assert "Python Security Guidelines" in data_py["content"]

    # Call the tool (list: js_ts and html_css)
    results_list = await call_tool("patchitright_guide", {"file_type": ["js_ts", "html_css"]})
    data_list = json.loads(results_list[0].text)
    assert "JavaScript / TypeScript Clean-Code" in data_list["content"]
    assert "HTML / CSS Accessibility" in data_list["content"]
    assert "Python Security Guidelines" not in data_list["content"]

