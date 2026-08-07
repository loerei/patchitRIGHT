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

