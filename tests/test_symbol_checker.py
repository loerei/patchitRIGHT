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

def test_extract_declarations_py_args_classes_and_comments():
    # 1. Comments & Docstrings
    code_comments = '"""Docstring top"""\n# Leading comment\nx_val = 10  # trailing comment\n\'\'\'Single triple\'\'\''
    masked = mask_comments_and_strings(code_comments, "test.py")
    assert "Docstring top" not in masked
    assert "Single triple" not in masked
    assert "Leading comment" not in masked
    assert "trailing comment" not in masked
    assert "x_val = 10" in masked

    # 2. Python args, kwargs, varargs, classes
    code_fn = (
        "class StandaloneClass:\n"
        "    def execute(self, param_a, param_b=1, *extra_args, **kw_options):\n"
        "        target_one = target_two = 100\n"
        "        return param_a + target_one\n"
    )
    decls = extract_declarations(code_fn, "test.py")
    assert "StandaloneClass" in decls
    assert "execute" in decls
    assert "param_a" in decls
    assert "param_b" in decls
    assert "extra_args" in decls
    assert "kw_options" in decls
    assert "target_one" in decls
    assert "target_two" in decls

    # 3. Incomplete Python syntax regex fallback
    incomplete_code = "def incomplete_func(arg:\nclass IncompleteClass:\nval_a = 10"
    decls_fallback = extract_declarations(incomplete_code, "test.py")
    assert "incomplete_func" in decls_fallback
    assert "IncompleteClass" in decls_fallback
    assert "val_a" in decls_fallback


def test_extract_declarations_js_arrows_and_imports():
    # 1. Arrow function params
    js_arrow = "(first_item, second_item) => first_item + second_item;"
    decls_arrow = extract_declarations(js_arrow, "test.js")
    assert "first_item" in decls_arrow
    assert "second_item" in decls_arrow

    # 2. Imports default and named
    js_imp = "import DefaultExport from 'lib';\nimport { origName as aliasName, directName } from 'other';"
    decls_imp = extract_declarations(js_imp, "test.js")
    assert "DefaultExport" in decls_imp
    assert "aliasName" in decls_imp
    assert "directName" in decls_imp

    # 3. extract_net_diff_declarations
    patch_str = (
        "--- a/file.js\n"
        "+++ b/file.js\n"
        "@@ -1,3 +1,3 @@\n"
        "-const oldDeclaredSymbol = 1;\n"
        "+const newDeclaredSymbol = 2;\n"
    )
    net_decls = extract_net_diff_declarations(patch_str, "file.js")
    assert "oldDeclaredSymbol" in net_decls
    assert "newDeclaredSymbol" not in net_decls




def test_mask_comments_and_strings_py_fstrings_and_decorators():
    # 1. f-strings with expressions and escaped braces
    code = 'f = f"Result: {compute(val)} and escaped {{brace}}"\n'
    masked = mask_comments_and_strings(code, "test.py")
    assert "compute(val)" in masked
    assert "Result:" not in masked

    # 2. Multi-target assignment and decorators
    code_decls = (
        "@my_decorator\n"
        "class MyService:\n"
        "    field_x = field_y = field_z = 42\n"
        "    def method(self, arg_one, arg_two=10):\n"
        "        pass\n"
    )
    decls = extract_declarations(code_decls, "test.py")
    assert "MyService" in decls
    assert "field_x" in decls
    assert "field_y" in decls
    assert "field_z" in decls
    assert "method" in decls



def test_mask_comments_and_strings_js_block_comments():
    code = '/* Multi line\n   block comment */\nconst z = 10;'
    masked = mask_comments_and_strings(code, "test.js")
    assert "block comment" not in masked
    assert "const z = 10;" in masked


def test_extract_declarations_py_async_and_tuples():
    code = (
        "async def handle_request(req):\n"
        "    alpha_val, beta_val = get_pair()\n"
        "    first_item, *rest_items = get_list()\n"
        "    return alpha_val + beta_val\n"
    )
    decls = extract_declarations(code, "test.py")
    assert "handle_request" in decls
    assert "alpha_val" in decls
    assert "beta_val" in decls
    assert "first_item" in decls



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


def test_mask_comments_and_strings_shebang_and_division():
    from patchitright_mcp.symbol_checker import mask_comments_and_strings
    code = "#!/usr/bin/env node\nconst a = 10;\nconst b = 2;\nconst c = a / b;\n// comment\nconst d = 4;"
    masked = mask_comments_and_strings(code, "test.js")
    assert "const a = 10;" in masked
    assert "const c = a / b;" in masked
    assert "const d = 4;" in masked
    assert "// comment" not in masked


def test_mask_comments_and_strings_escaped_quotes():
    from patchitright_mcp.symbol_checker import mask_comments_and_strings
    # String ending with escaped backslash: "C:\\path\\"
    code = 'const winPath = "C:\\\\path\\\\";\nconst targetVar = 999;'
    masked = mask_comments_and_strings(code, "test.js")
    assert "targetVar" in masked


def test_detect_omitted_symbols_shebang_js_no_hang():
    from patchitright_mcp.symbol_checker import detect_omitted_symbols
    code = "#!/usr/bin/env node\nconst helper = 1;\nconst ratio = 10 / 2;\nconsole.log(helper);"
    warnings = detect_omitted_symbols(
        file_content=code,
        match_start=code.find("const helper = 1;"),
        match_end=code.find("const helper = 1;") + len("const helper = 1;"),
        original_slice="const helper = 1;",
        replace_content="// removed",
        filename="cli.js"
    )
    assert len(warnings) == 1
    assert "Symbol Omission Alert: 'helper'" in warnings[0]




