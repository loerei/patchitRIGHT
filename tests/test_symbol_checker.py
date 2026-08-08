import pytest
from patchitright_mcp.symbol_checker import (
    normalize_lf,
    mask_comments_and_strings,
    extract_declarations,
    detect_omitted_symbols,
    detect_net_omitted_symbols,
    extract_net_diff_declarations,
)

def test_normalize_lf():
    crlf_content = "line1\r\nline2\r\nline3"
    assert normalize_lf(crlf_content) == "line1\nline2\nline3"

def test_mask_comments_and_strings_js():
    code = 'const x = "hello world"; // comment here\nconst y = `val is ${firstIndex}`;'
    masked = mask_comments_and_strings(code, "test.ts")
    assert len(masked) == len(code)
    assert "hello world" not in masked
    assert "comment here" not in masked
    assert "${firstIndex}" in masked

def test_mask_comments_and_strings_py():
    code = 'def foo():\n    # comment\n    s = "hello"\n    f = f"var is {first_index}"'
    masked = mask_comments_and_strings(code, "test.py")
    assert len(masked) == len(code)
    assert "comment" not in masked
    assert "hello" not in masked
    assert "{first_index}" in masked

def test_extract_declarations_js():
    code = """
    const firstIndex = 1;
    let lastIndex = 2;
    var count = 3;
    function sortTabs(a, b) {}
    class Sorter {}
    interface Options {}
    type MyType = string;
    const { x, y: alias = 10 } = obj;
    import { foo as myFoo } from 'mod';
    """
    decls = extract_declarations(code, "test.ts")
    assert "firstIndex" in decls
    assert "lastIndex" in decls
    assert "count" in decls
    assert "sortTabs" in decls
    assert "Sorter" in decls
    assert "alias" in decls
    assert "myFoo" in decls
    assert "y" not in decls
    assert "foo" not in decls

def test_extract_declarations_py():
    code = """
    def my_func(arg1, arg2):
        self.attr = 10
        local_var = 20
        var_a, var_b = get_pair()
    """
    decls = extract_declarations(code, "test.py")
    assert "my_func" in decls
    assert "arg1" in decls
    assert "arg2" in decls
    assert "local_var" in decls
    assert "var_a" in decls
    assert "var_b" in decls
    assert "attr" not in decls
    assert "self" not in decls

def test_detect_omitted_symbols_sorter_replica():
    file_content = """
    const domainTabs = targetTabs.filter(t => t.domain === movedDomain);
    const firstIndex = targetTabs.findIndex(t => t.domain === movedDomain);
    let lastIndex = -1;
    for (let i = targetTabs.length - 1; i >= 0; i -= 1) {
        if (targetTabs[i].domain === movedDomain) {
            lastIndex = i;
            break;
        }
    }

    let isContiguous = true;
    for (let i = firstIndex; i <= lastIndex; i += 1) {
        if (targetTabs[i].domain !== movedDomain) {
            isContiguous = false;
            break;
        }
    }
    """
    search_content = """    const firstIndex = targetTabs.findIndex(t => t.domain === movedDomain);
    let lastIndex = -1;"""
    replace_content = """    // removed indices"""

    match_start = file_content.find(search_content)
    match_end = match_start + len(search_content)

    warnings = detect_omitted_symbols(
        file_content=file_content,
        match_start=match_start,
        match_end=match_end,
        original_slice=search_content,
        replace_content=replace_content,
        filename="sorter.ts"
    )

    assert len(warnings) > 0
    warning_str = "\n".join(warnings)
    assert "firstIndex" in warning_str
    assert "lastIndex" in warning_str
    assert "Symbol Omission Alert" in warning_str

def test_property_access_and_object_keys_not_flagged():
    file_content = """
    function test() {
        const firstIndex = 1;
        console.log(obj.firstIndex);
        const cfg = { firstIndex: 2 };
        interface Config { firstIndex?: number; }
    }
    """
    search_content = "        const firstIndex = 1;"
    replace_content = ""

    match_start = file_content.find(search_content)
    match_end = match_start + len(search_content)

    warnings = detect_omitted_symbols(
        file_content=file_content,
        match_start=match_start,
        match_end=match_end,
        original_slice=search_content,
        replace_content=replace_content,
        filename="test.ts"
    )

    assert len(warnings) == 0

def test_non_code_files_bypassed():
    file_content = "# Title\nfirstIndex = 1\nsecondIndex = 2"
    warnings = detect_omitted_symbols(
        file_content=file_content,
        match_start=0,
        match_end=10,
        original_slice="firstIndex",
        replace_content="",
        filename="readme.md"
    )
    assert len(warnings) == 0

def test_detect_net_omitted_symbols_batch():
    patched_content = """
    const y = 20;
    console.log(x + y);
    """
    deleted_symbols = {"x"}

    warnings = detect_net_omitted_symbols(
        patched_content=patched_content,
        deleted_symbols=deleted_symbols,
        filename="test.js"
    )
    assert len(warnings) == 1
    assert "x" in warnings[0]

def test_patch_engine_symbol_warnings():
    from patchitright_mcp.engine import PatchEngine
    file_content = """
    const firstIndex = 1;
    let lastIndex = 2;
    console.log(firstIndex + lastIndex);
    """
    engine = PatchEngine(file_content, "test.ts")
    assert hasattr(engine, "symbol_warnings")
    engine.apply_classic_patch(
        search_content="    const firstIndex = 1;\n    let lastIndex = 2;",
        replace_content="// indices removed"
    )
    assert len(engine.symbol_warnings) > 0
    assert "firstIndex" in "\n".join(engine.symbol_warnings)

def test_run_cache_symbol_warnings_persistence():
    from pathlib import Path
    from patchitright_mcp.run_cache import RunCache
    cache = RunCache()
    target_path = Path("d:/Projects/patchitRIGHT/test.ts")
    entries = [{
        "target_path": target_path,
        "patched_content": "const y = 10;",
        "symbol_warnings": ["Symbol Omission Alert: 'firstIndex'..."],
        "warnings": ["Symbol Omission Alert: 'firstIndex'..."]
    }]
    run_id = cache.store(entries, {str(target_path): "const firstIndex = 1;"})
    consumed_files = cache.consume(run_id)
    assert consumed_files is not None
    assert "symbol_warnings" in consumed_files["files"][0]
    assert len(consumed_files["files"][0]["symbol_warnings"]) == 1





def test_patch_file_batch_replacements_net_omitted_symbols(tmp_path, monkeypatch):
    from patchitright_mcp.patch_file import patch_file
    monkeypatch.delenv("PATCHITRIGHT_IGNORE_WARNINGS", raising=False)
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "batch.ts"
    f.write_text("const firstIndex = 1;\nconst lastIndex = 2;\nconsole.log(firstIndex + lastIndex);")

    res = patch_file(
        target_file="batch.ts",
        replacements=[
            {"search_content": "const firstIndex = 1;", "replace_content": "// firstIndex deleted"},
            {"search_content": "const lastIndex = 2;", "replace_content": "const lastIndex = 2; // lastIndex kept"}
        ],
        dry_run=False
    )
    assert res["success"] is True
    assert "warnings" in res
    assert any("Symbol Omission Alert: 'firstIndex'" in w for w in res["warnings"])


def test_apply_last_dry_run_cache_retrieval_symbol_warnings(tmp_path, monkeypatch):
    from patchitright_mcp.patch_file import patch_file, apply_last_dry_run
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "dry.ts"
    f.write_text("const firstIndex = 1;\nconsole.log(firstIndex);")

    dry_res = patch_file(
        target_file="dry.ts",
        search_content="const firstIndex = 1;",
        replace_content="// removed",
        dry_run=True
    )
    assert dry_res["success"] is True
    run_id = dry_res["run_id"]

    apply_res = apply_last_dry_run(run_id=run_id)
    assert apply_res["success"] is True
    assert "warnings" in apply_res
    assert any("Symbol Omission Alert: 'firstIndex'" in w for w in apply_res["warnings"])


def test_dynamic_is_transient_symbol(monkeypatch):
    from patchitright_mcp.symbol_checker import is_transient_symbol
    # 1-char symbols automatically transient
    assert is_transient_symbol("a") is True
    assert is_transient_symbol("z") is True
    assert is_transient_symbol("1") is True

    # Default transient symbols
    assert is_transient_symbol("item") is True
    assert is_transient_symbol("req") is True

    # Custom variable non-transient by default
    assert is_transient_symbol("myCustomVar") is False

    # Custom variable transient via env var
    monkeypatch.setenv("PATCHITRIGHT_TRANSIENT_SYMBOLS", "myCustomVar, tempFlag")
    assert is_transient_symbol("myCustomVar") is True
    assert is_transient_symbol("tempFlag") is True



def test_write_file_and_markdown_bypass(tmp_path, monkeypatch):
    from patchitright_mcp.patch_file import write_file, patch_file
    monkeypatch.chdir(tmp_path)

    # 1. write_file bypass
    f1 = tmp_path / "overwrite.ts"
    f1.write_text("const unusedVar = 100;\nconsole.log(unusedVar);")
    res1 = write_file(target_file="overwrite.ts", code_content="// completely new content", allow_overwrite=True)
    assert res1["success"] is True
    assert "warnings" not in res1

    # 2. .md file bypass
    f2 = tmp_path / "doc.md"
    f2.write_text("# Heading\nconst x = 1;\nconsole.log(x);")
    res2 = patch_file(
        target_file="doc.md",
        search_content="const x = 1;",
        replace_content="// removed",
        dry_run=False
    )
    assert res2["success"] is True
    assert "warnings" not in res2


def test_python_hash_inside_string_literal():
    from patchitright_mcp.symbol_checker import mask_comments_and_strings
    code = 'hash_str = "# not a comment"\nmy_var = 123'
    masked = mask_comments_and_strings(code, "test.py")
    assert "my_var" in masked



