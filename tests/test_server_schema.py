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
    with patch.dict(os.environ, {"PATCHITRIGHT_EXPOSE_BYPASS_VALIDATION": "true", "PATCHITRIGHT_SHOW_LEGACY": "true"}):
        tools = await list_tools()
        for tool in tools:
            properties = tool.inputSchema.get("properties", {})
            if tool.name in ("patch_file", "batch_patch_files", "write_file"):
                assert "bypass_validation" in properties
                assert properties["bypass_validation"]["type"] == "boolean"
            else:
                assert "bypass_validation" not in properties


@pytest.mark.asyncio
async def test_list_tools_show_legacy_default_off():
    with patch.dict(os.environ, {"PATCHITRIGHT_SHOW_LEGACY": "false", "SHOW_LEGACY": "false"}):
        tools = await list_tools()
        tool_dict = {t.name: t for t in tools}
        assert "batch_patch_files" not in tool_dict
        assert "apply_last_dry_run" not in tool_dict
        assert "patch_file" in tool_dict
        assert "write_file" in tool_dict
        assert "patchitright_guide" in tool_dict
        assert "dry_run" not in tool_dict["patch_file"].inputSchema["properties"]
        assert "dry_run" not in tool_dict["write_file"].inputSchema["properties"]


@pytest.mark.asyncio
async def test_list_tools_show_legacy_enabled():
    with patch.dict(os.environ, {"PATCHITRIGHT_SHOW_LEGACY": "true"}):
        tools = await list_tools()
        tool_dict = {t.name: t for t in tools}
        assert "batch_patch_files" in tool_dict
        assert "apply_last_dry_run" in tool_dict
        assert "dry_run" in tool_dict["patch_file"].inputSchema["properties"]
        assert "dry_run" in tool_dict["write_file"].inputSchema["properties"]



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
    assert "Insert code at line" in data["content"]
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


@pytest.mark.asyncio
async def test_patch_file_schema_line_insertion_fields():
    from patchitright_mcp.server import list_tools
    tools = await list_tools()
    patch_tool = next((t for t in tools if t.name == "patch_file"), None)
    assert patch_tool is not None
    props = patch_tool.inputSchema.get("properties", {})

    # Top-level insertion properties
    for param in ("insert_line", "insert_content", "auto_indent"):
        assert param in props

    assert "insert_position" not in props

    # Item-level insertion properties via $defs/PatchOp and files items
    patch_op_props = patch_tool.inputSchema["$defs"]["PatchOp"]["properties"]
    assert props["replacements"]["items"] == {"$ref": "#/$defs/PatchOp"}
    for param in ("insert_line", "insert_content", "auto_indent"):
        assert param in patch_op_props
    assert "insert_position" not in patch_op_props

    files_schema = props["files"]["items"]
    assert {"$ref": "#/$defs/PatchOp"} in files_schema["allOf"]
    files_props = {**patch_op_props, **files_schema.get("properties", {})}
    for param in ("insert_line", "insert_content", "auto_indent"):
        assert param in files_props
    assert "insert_position" not in files_props


def test_enrich_tool_schema_legacy_copy():
    from patchitright_mcp.tool_schemas import _enrich_tool_schema

    class DummyTool:
        def __init__(self):
            self.name = "custom_tool"
            self.inputSchema = {"type": "object", "properties": {}}

    dummy = DummyTool()
    enriched = _enrich_tool_schema(dummy, expose_bypass=True)
    assert "set_timeout" in enriched.inputSchema["properties"]


@pytest.mark.asyncio
async def test_schema_parity_and_extension_bounds():
    """Shared schemas expose new slice/broadcast/guard fields consistently with strict integer bounds."""
    from patchitright_mcp.server import list_tools

    tools = {t.name: t for t in await list_tools()}
    patch_schema = tools["patch_file"].inputSchema
    write_schema = tools["write_file"].inputSchema
    patch_props = patch_schema["properties"]
    write_props = write_schema["properties"]
    repl_props = patch_schema["$defs"]["PatchOp"]["properties"]
    files_props = {**repl_props, **patch_props["files"]["items"]["properties"]}

    for key in ("target_files", "target_glob", "skip_unmatched", "expected_occurrences", "start_anchor", "end_anchor"):
        assert key in patch_props

    for shared_slice in ("source_file", "source_start_line", "source_end_line", "source_symbol", "source_start_anchor", "source_end_anchor", "delete_source", "cut_source"):
        assert shared_slice in patch_props
        assert shared_slice in repl_props
        assert shared_slice in files_props
        assert shared_slice in write_props

    for shared_edit in ("expected_occurrences", "start_anchor", "end_anchor", "line_filter"):
        assert shared_edit in patch_props
        assert shared_edit in repl_props
        assert shared_edit in files_props
        assert shared_edit in write_props

    assert files_props["action"]["enum"] == ["patch", "write", "delete"]
    assert "anyOf" in files_props["line_filter"]
    assert patch_props["expected_occurrences"]["minimum"] == 1
    assert patch_props["source_start_line"]["minimum"] == 1
    assert patch_props["source_end_line"]["minimum"] == 1
    assert write_props["source_start_line"]["minimum"] == 1
    assert {"required": ["source_file"]} in write_schema["anyOf"]

    import jsonschema
    jsonschema.validate(
        instance={"files": [{"target_file": "a.py", "replacements": [{"search_content": "a", "replace_content": "b"}]}]},
        schema=patch_schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"target_file": "a.py", "replacements": [True]}, schema=patch_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"files": [{"target_file": "a.py", "start_line": 0}]}, schema=patch_schema)


@pytest.mark.asyncio
async def test_schemas_exclude_storage_path():
    """storage_path is no longer exposed in MCP tool schemas and symbol_name is index-free."""
    from patchitright_mcp.server import list_tools

    with patch.dict(os.environ, {"PATCHITRIGHT_SHOW_LEGACY": "true"}):
        tools = {t.name: t for t in await list_tools()}

    for name in ("patch_file", "write_file", "batch_patch_files"):
        props = tools[name].inputSchema.get("properties", {})
        assert "storage_path" not in props

    patch_props = tools["patch_file"].inputSchema["properties"]
    assert "jcodemunch" not in patch_props["symbol_name"]["description"].lower()



