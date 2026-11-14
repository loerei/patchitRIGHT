import os
import shutil
import tempfile
from pathlib import Path
import pytest

from patchitright_mcp.patch_file import write_file, apply_last_dry_run


@pytest.fixture
def temp_workspace():
    """Create a temporary directory for workspace testing."""
    temp_dir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    os.chdir(temp_dir)
    # Create an anchor file to make it a workspace root
    Path(".patchitRIGHT").touch()
    
    yield Path(temp_dir)
    
    os.chdir(old_cwd)
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass


def test_write_file_new(temp_workspace):
    # Test writing a new file in the workspace
    target = "new_file.py"
    code = "def hello():\n    print('hello')\n"
    res = write_file(target_file=target, code_content=code, allow_overwrite=False)
    
    assert res.get("success") is True
    assert res.get("dryRun") is False
    assert "Successfully created" in res.get("message")
    
    # Verify file was written
    target_path = temp_workspace / target
    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == code


def test_write_file_existing_block(temp_workspace):
    target = "existing.py"
    target_path = temp_workspace / target
    target_path.write_text("orig_content", encoding="utf-8")
    
    # Try writing without allow_overwrite
    res = write_file(target_file=target, code_content="new_content", allow_overwrite=False)
    assert "error" in res
    assert "already exists" in res["error"]
    assert target_path.read_text(encoding="utf-8") == "orig_content"


def test_write_file_existing_overwrite(temp_workspace):
    target = "existing.py"
    target_path = temp_workspace / target
    target_path.write_text("orig_content", encoding="utf-8")
    
    # Try writing with allow_overwrite
    res = write_file(target_file=target, code_content="new_content", allow_overwrite=True)
    assert res.get("success") is True
    assert "Successfully overwritten" in res.get("message")
    assert target_path.read_text(encoding="utf-8") == "new_content"


def test_write_file_auto_create_parents(temp_workspace):
    target = "sub/dir/new_file.py"
    code = "print('hello')\n"
    res = write_file(target_file=target, code_content=code, allow_overwrite=False)
    
    assert res.get("success") is True
    target_path = temp_workspace / target
    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == code


def test_write_file_syntax_error(temp_workspace):
    target = "bad.py"
    # Invalid python code (missing paren)
    code = "print('hello'\n"
    res = write_file(target_file=target, code_content=code, allow_overwrite=False)
    
    assert "error" in res
    assert "Syntax Error" in res["error"]
    assert not (temp_workspace / target).exists()


def test_write_file_bypass_validation(temp_workspace):
    target = "bad.py"
    code = "print('hello'\n"
    res = write_file(target_file=target, code_content=code, allow_overwrite=False, bypass_validation=True)
    
    assert res.get("success") is True
    assert (temp_workspace / target).exists()
    assert (temp_workspace / target).read_text(encoding="utf-8") == code


def test_write_file_dry_run_and_apply(temp_workspace):
    target = "dry_run_test.py"
    code = "x = 42\n"
    
    # Dry run
    res = write_file(target_file=target, code_content=code, allow_overwrite=False, dry_run=True)
    assert res.get("success") is True
    assert res.get("dryRun") is True
    assert "Preview of creating new file" in res.get("message")
    assert "run_id" in res
    
    target_path = temp_workspace / target
    assert not target_path.exists()
    
    # Apply dry run
    apply_res = apply_last_dry_run(res["run_id"])
    assert apply_res.get("success") is True
    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == code


def test_write_file_es_module_js_node_fallback(temp_workspace, monkeypatch):
    """Test writing ES Module .js file when package.json contains 'type': 'module'."""
    # Force Biome check to fail to trigger node check fallback
    from patchitright_mcp.validators.javascript import JsTsValidator
    monkeypatch.setattr(JsTsValidator, "_get_biome_command", lambda self, filename="": None)

    # Set up package.json with "type": "module"
    pkg_json = temp_workspace / "package.json"
    pkg_json.write_text('{"type": "module"}', encoding="utf-8")

    target = "bin/agents.js"
    code = "#!/usr/bin/env node\nimport fs from 'node:fs';\nimport path from 'node:path';\nexport function run() { return fs; }\n"

    res = write_file(target_file=target, code_content=code, allow_overwrite=False)
    assert res.get("success") is True
    assert res.get("target_file") == os.path.normpath(target)
    assert res.get("diff_content") is None
    assert res.get("occurrences") == 1
    assert res.get("modified_files") is None
    target_path = temp_workspace / target
    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == code


def test_write_file_mjs_and_cjs_syntax(temp_workspace, monkeypatch):
    """Test syntax validation for .mjs and .cjs extensions under node check fallback."""
    from patchitright_mcp.validators.javascript import JsTsValidator
    monkeypatch.setattr(JsTsValidator, "_get_biome_command", lambda self, filename="": None)

    # Valid .mjs file
    mjs_target = "module.mjs"
    mjs_code = "import fs from 'node:fs';\nexport default fs;\n"
    res_mjs = write_file(target_file=mjs_target, code_content=mjs_code, allow_overwrite=False)
    assert res_mjs.get("success") is True

    # Valid .cjs file
    cjs_target = "script.cjs"
    cjs_code = "const fs = require('node:fs');\nmodule.exports = fs;\n"
    res_cjs = write_file(target_file=cjs_target, code_content=cjs_code, allow_overwrite=False)
    assert res_cjs.get("success") is True

    # Invalid .mjs file (syntax error)
    bad_mjs_target = "bad.mjs"
    bad_mjs_code = "import { from 'node:fs';\n"
    res_bad = write_file(target_file=bad_mjs_target, code_content=bad_mjs_code, allow_overwrite=False)
    assert "error" in res_bad
    assert "Syntax Error" in res_bad["error"]
    assert res_bad.get("target_file") == os.path.normpath(bad_mjs_target)
    assert res_bad.get("diff_content") is None
    assert res_bad.get("occurrences") is None
    assert res_bad.get("modified_files") is None


def test_write_file_pure_copy_and_binary_copy(temp_workspace):
    """write_file copies text files (validated) and binary files cleanly, including dry-run apply."""
    src_text = temp_workspace / "src_mod.py"
    src_text.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

    res_text = write_file(target_file="dst_mod.py", source_file="src_mod.py")
    assert res_text["success"] is True
    assert (temp_workspace / "dst_mod.py").read_text(encoding="utf-8") == src_text.read_text(encoding="utf-8")
    assert src_text.exists()

    raw_bytes = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff\xfe"
    src_bin = temp_workspace / "asset.bin"
    src_bin.write_bytes(raw_bytes)

    dry_res = write_file(target_file="copied/asset.bin", source_file="asset.bin", dry_run=True)
    assert dry_res["success"] is True
    assert dry_res["dryRun"] is True
    assert "Binary file" in dry_res["diff_content"]
    assert not (temp_workspace / "copied/asset.bin").exists()

    apply_res = apply_last_dry_run(dry_res["run_id"])
    assert apply_res["success"] is True
    assert (temp_workspace / "copied/asset.bin").read_bytes() == raw_bytes


def test_write_file_move_with_delete_source_dry_run_and_apply(temp_workspace):
    """delete_source=True atomically unlinks source_file on whole-file copy and blocks on partial slices."""
    src = temp_workspace / "old_name.py"
    src.write_text("VALUE = 1\nNEXT = 2\n", encoding="utf-8")

    err_slice = write_file(
        target_file="sliced.py",
        source_file="old_name.py",
        source_start_line=1,
        source_end_line=1,
        delete_source=True,
    )
    assert "error" in err_slice
    assert src.exists()

    dry_move = write_file(
        target_file="moved/new_name.py",
        source_file="old_name.py",
        delete_source=True,
        dry_run=True,
    )
    assert dry_move["success"] is True
    assert dry_move["dryRun"] is True
    assert src.exists()
    assert not (temp_workspace / "moved/new_name.py").exists()

    applied = apply_last_dry_run(dry_move["run_id"])
    assert applied["success"] is True
    assert not src.exists()
    assert (temp_workspace / "moved/new_name.py").read_text(encoding="utf-8") == "VALUE = 1\nNEXT = 2\n"


def test_write_file_copy_then_patch_and_line_slice_cut_source(temp_workspace):
    """write_file supports inline copy-then-patch transforms, anchored line slices, and atomic cut_source."""
    src = temp_workspace / "monolith.py"
    src.write_text(
        "def keep_first():\n"
        "    return 1\n"
        "\n"
        "def extracted_helper(x):\n"
        "    return x + 10\n",
        encoding="utf-8",
    )

    res_patch = write_file(
        target_file="copy_patched.py",
        source_file="monolith.py",
        replacements=[
            {"search_content": "return 1", "replace_content": "return 99"},
            {"search_content": "return x + 10", "replace_content": "return x + 20"},
        ],
    )
    assert res_patch["success"] is True
    assert "return 99" in (temp_workspace / "copy_patched.py").read_text(encoding="utf-8")
    assert "return 1\n" in src.read_text(encoding="utf-8")

    bad_anchor = write_file(
        target_file="helper.py",
        source_file="monolith.py",
        source_start_line=4,
        source_end_line=5,
        source_start_anchor="def missing_fn",
        cut_source=True,
    )
    assert "error" in bad_anchor
    assert not (temp_workspace / "helper.py").exists()
    assert "extracted_helper" in src.read_text(encoding="utf-8")

    bad_syntax_cut = write_file(
        target_file="broken_slice.py",
        source_file="monolith.py",
        source_start_line=5,
        source_end_line=5,
        source_start_anchor="return x + 10",
        cut_source=True,
        insert_line=1,
        insert_content="def incomplete(:\n",
    )
    assert "error" in bad_syntax_cut
    assert "extracted_helper" in src.read_text(encoding="utf-8")

    res_cut = write_file(
        target_file="helper.py",
        source_file="monolith.py",
        source_start_line=4,
        source_end_line=5,
        source_start_anchor="def extracted_helper",
        cut_source=True,
    )
    assert res_cut["success"] is True
    assert (temp_workspace / "helper.py").read_text(encoding="utf-8") == "def extracted_helper(x):\n    return x + 10\n"
    assert src.read_text(encoding="utf-8") == "def keep_first():\n    return 1\n\n"


def test_write_file_source_symbol_extraction(temp_workspace):
    """write_file extracts an AST symbol completely using jCodeMunch storage_path."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = temp_workspace / "source_syms.py"
    src.write_text(
        "def alpha():\n"
        "    return 1\n\n"
        "def beta(v):\n"
        "    return v * 2\n",
        encoding="utf-8",
    )
    store_path = str(temp_workspace / "store")
    index_folder(str(temp_workspace), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

    res = write_file(
        target_file="beta_only.py",
        source_file="source_syms.py",
        source_symbol="beta",
        storage_path=store_path,
        cut_source=True,
    )
    assert res["success"] is True
    assert (temp_workspace / "beta_only.py").read_text(encoding="utf-8") == "def beta(v):\n    return v * 2\n"
    assert "def beta" not in src.read_text(encoding="utf-8")
    assert "def alpha():" in src.read_text(encoding="utf-8")
