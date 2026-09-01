import pytest
from unittest.mock import MagicMock, patch
from patchitright_mcp.validators import ValidationService, SyntaxValidationError
from patchitright_mcp.validators.python import PythonValidator
from patchitright_mcp.validators.javascript import JsTsValidator
from patchitright_mcp.validators.config_files import JsonValidator, TomlValidator, YamlValidator

def test_python_validator():
    val = PythonValidator()
    
    # Valid Python code
    val.validate("def main():\n    pass\n", "test.py")
    
    # Invalid Python code (syntax error)
    with pytest.raises(SyntaxValidationError) as exc_info:
        val.validate("def main():\n  pass\n  if :\n", "test.py")
    assert exc_info.value.line == 3
    assert exc_info.value.filename == "test.py"

    # Ruff check warnings
    warnings = val.lint("import os\n", "test.py")
    # Should report F401: os imported but unused (assuming local ruff is available, else mock)
    if warnings:
        assert any("F401" in w for w in warnings)


def test_js_ts_validator_mocked():
    val = JsTsValidator()
    
    # 1. Mock biome is found and validate succeeds
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/biome" if cmd == "biome" else None
        
        # Valid original, valid new
        mock_run.returncode = 0
        mock_run.stdout = ""
        mock_run.stderr = ""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        
        val.validate("const a = 1;", "app.ts", "const a = 1;")
        assert mock_run.call_count == 2 # 1 for original check, 1 for current check

    # 2. Mock biome is found and validate fails on syntax error
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/biome" if cmd == "biome" else None
        
        # Valid original check passes (0)
        orig_res = MagicMock(returncode=0, stdout="", stderr="")
        # New check fails (1) with syntax error
        new_res = MagicMock(returncode=1, stdout="parse error: expected ';'", stderr="")
        mock_run.side_effect = [orig_res, new_res]
        
        with pytest.raises(SyntaxValidationError) as exc_info:
            val.validate("const a =", "app.ts", "const a = 1;")
        assert "Biome Syntax Error" in str(exc_info.value)

    # 3. Fallback to node --check for JS files
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/node" if cmd == "node" else None
        
        orig_res = MagicMock(returncode=0, stdout="", stderr="")
        new_res = MagicMock(returncode=1, stdout="", stderr="SyntaxError: Unexpected end of input")
        mock_run.side_effect = [orig_res, new_res]
        
        with pytest.raises(SyntaxValidationError) as exc_info:
            val.validate("const a =", "app.js", "const a = 1;")
        assert "Node JS Syntax Error" in str(exc_info.value)


def test_js_ts_validator_tsc_fallback():
    val = JsTsValidator()
    # Mock tsc is found and Biome is not found, validate fails on syntax error
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        # biome not found, tsc found
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/tsc" if cmd == "tsc" else None

        # Valid original check passes (0)
        orig_res = MagicMock(returncode=0, stdout="", stderr="")
        # New check fails (1) with syntax error
        new_res = MagicMock(
            returncode=1,
            stdout="app.patchitright_temp.ts(1,7): error TS1005: ';' expected.",
            stderr=""
        )
        mock_run.side_effect = [orig_res, new_res]

        with pytest.raises(SyntaxValidationError) as exc_info:
            val.validate("const a =", "app.ts", "const a = 1;")
        assert "TSC TS Syntax Error" in str(exc_info.value)
        assert exc_info.value.line == 1
        assert exc_info.value.column == 7


def test_json_validator():
    val = JsonValidator()
    
    # Valid JSON
    val.validate('{"key": "value", "arr": [1, 2]}', "config.json")
    
    # JSONC (JSON with comments)
    jsonc_content = """
    // This is a comment
    {
        "key": "value", /* Inline comment */
        "arr": [1, 2]
    }
    """
    val.validate(jsonc_content, "config.json", '{"key": "value"}')
    
    # Invalid JSON
    with pytest.raises(SyntaxValidationError) as exc_info:
        val.validate('{"key": "value", "arr": [1, 2,]}', "config.json")
    assert exc_info.value.filename == "config.json"

    # Test Biome JSON linting
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/biome" if cmd == "biome" else None
        mock_run.return_value = MagicMock(returncode=0, stdout="warning: duplicate key 'key'", stderr="")
        
        warnings = val.lint('{"key": "value", "key": "value2"}', "config.json")
        assert any("duplicate key" in w for w in warnings)


def test_toml_validator():
    val = TomlValidator()
    
    # Valid TOML
    val.validate('title = "TOML Example"\n[owner]\nname = "Tom"', "config.toml")
    
    # Invalid TOML
    with pytest.raises(SyntaxValidationError) as exc_info:
        val.validate('title = "TOML Example"\n[owner\nname = "Tom"', "config.toml")
    assert exc_info.value.filename == "config.toml"
    assert exc_info.value.line in (2, 3)
    assert exc_info.value.column is not None


def test_yaml_validator():
    val = YamlValidator()
    
    # Valid YAML
    val.validate("key: value\nlist:\n  - item 1\n  - item 2\n", "config.yaml")
    
    # Invalid YAML
    with pytest.raises(SyntaxValidationError) as exc_info:
        val.validate("key: value\nlist:\n  - item 1\n  -item 2\n", "config.yaml")
    assert exc_info.value.filename == "config.yaml"
    assert exc_info.value.line == 5
    assert exc_info.value.column == 1


def test_validation_service():
    service = ValidationService()
    
    # Check that it returns linter warnings for Python files (if ruff installed)
    warnings = service.lint_file("app.py", "import sys\n")
    if warnings:
        assert any("F401" in w for w in warnings)

    # Check validation routing
    service.validate_file("app.json", '{"a": 1}')
    with pytest.raises(SyntaxValidationError):
        service.validate_file("app.json", '{"a": 1,}')


def test_js_ts_validator_biome_warnings_only():
    val = JsTsValidator()
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/biome" if cmd == "biome" else None
        
        # Valid original check passes (0)
        orig_res = MagicMock(returncode=0, stdout="", stderr="")
        # New check returns 1 (non-zero due to warnings), but only contains a lint warning, no syntax error
        biome_output = '{"summary":{"changed":0,"unchanged":1,"matches":0,"errors":0,"warnings":1,"infos":0},"diagnostics":[{"severity":"warning","message":"This let declares a variable that is only assigned once.","category":"lint/style/useConst"}],"command":"check"}'
        new_res = MagicMock(returncode=1, stdout=biome_output, stderr="")
        mock_run.side_effect = [orig_res, new_res]
        
        # This should NOT raise any SyntaxValidationError because there is no parse/syntax error
        val.validate("let x = 1; console.log(x);", "app.ts", "const x = 1;")
        assert mock_run.call_count == 2


def test_js_ts_validator_biome_original_warnings_only():
    val = JsTsValidator()
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd, *args, **kwargs: "/usr/bin/biome" if cmd == "biome" else None
        
        # Original check returns 1 (warnings)
        biome_output = '{"summary":{"changed":0,"unchanged":1,"matches":0,"errors":0,"warnings":1,"infos":0},"diagnostics":[{"severity":"warning","message":"This let declares a variable that is only assigned once.","category":"lint/style/useConst"}],"command":"check"}'
        orig_res = MagicMock(returncode=1, stdout=biome_output, stderr="")
        new_res = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = [orig_res, new_res]
        
        # This should call the new check because original has no syntax error (so we do not skip validation)
        val.validate("const x = 1;", "app.ts", "let x = 1;")
        assert mock_run.call_count == 2


def test_js_ts_validator_real(tmp_path, monkeypatch):
    from patchitright_mcp.patch_file import patch_file
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "temp_test.ts"
    f.write_text("const x: number = 1;\n")

    # Patch that introduces a syntax error
    res = patch_file(
        target_file="temp_test.ts",
        search_content="const x: number = 1;",
        replace_content="const x:",
        dry_run=False
    )
    import shutil
    if shutil.which("npx"):
        assert "error" in res
        assert "Syntax Error" in res["error"] or "Biome Syntax Error" in res["error"]
        assert res["line"] == 1
        assert res["column"] in (7, 9)
        assert f.read_text() == "const x: number = 1;\n"


def test_js_ts_validator_detect_package_manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    val = JsTsValidator()

    # Case 1: node_modules/.pnpm exists
    (tmp_path / "node_modules" / ".pnpm").mkdir(parents=True, exist_ok=True)
    assert val._detect_package_manager(str(tmp_path / "app.js")) == "pnpm"

    # Clean up node_modules
    import shutil as local_shutil
    local_shutil.rmtree(tmp_path / "node_modules")

    # Case 2: node_modules exists, package-lock.json exists
    (tmp_path / "node_modules").mkdir(exist_ok=True)
    (tmp_path / "package-lock.json").write_text("")
    assert val._detect_package_manager(str(tmp_path / "app.js")) == "npm"

    # Case 3: node_modules exists, yarn.lock exists
    (tmp_path / "package-lock.json").unlink()
    (tmp_path / "yarn.lock").write_text("")
    assert val._detect_package_manager(str(tmp_path / "app.js")) == "yarn"

    # Case 4: No node_modules, multiple lockfiles, mtime comparison
    local_shutil.rmtree(tmp_path / "node_modules")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    # Set mtimes: pnpm-lock.yaml is newest
    import os
    os.utime(tmp_path / "pnpm-lock.yaml", (1000, 2000))
    os.utime(tmp_path / "yarn.lock", (1000, 1000))
    assert val._detect_package_manager(str(tmp_path / "app.js")) == "pnpm"

    # Set yarn.lock to be newest
    os.utime(tmp_path / "yarn.lock", (1000, 3000))
    assert val._detect_package_manager(str(tmp_path / "app.js")) == "yarn"


def test_json_validator_edge_cases():
    val = JsonValidator()

    # 1. Empty or whitespace content
    val.validate("", "config.json")
    val.validate("   \n\t ", "config.json")

    # 2. Already invalid original content skips validation
    val.validate("invalid {", "config.json", original_content="invalid {")

    # 3. .json5 with unquoted keys and comments
    val.validate("{ key: 'value', // comment\n }", "config.json5", original_content="{}")

    # 4. Fallback when json5 is None
    with patch("patchitright_mcp.validators.config_files.json5", None):
        val.validate('{"a": 1}', "config.jsonc")
        with pytest.raises(SyntaxValidationError):
            val.validate('{ bad: json }', "config.jsonc")


def test_toml_validator_edge_cases():
    val = TomlValidator()

    # 1. Empty content
    val.validate("", "config.toml")

    # 2. Already invalid original content skips validation
    val.validate("[invalid", "config.toml", original_content="[invalid")

    # 3. tomllib is None
    with patch("patchitright_mcp.validators.config_files.tomllib", None):
        val.validate("key = 'val'", "config.toml")

    # 4. Detailed error message parsing with line and column
    with pytest.raises(SyntaxValidationError) as exc:
        val.validate("key = [1, 2", "config.toml")
    assert "TOML Syntax Error" in str(exc.value)


def test_yaml_validator_edge_cases():
    val = YamlValidator()

    # 1. Empty content
    val.validate("", "config.yaml")

    # 2. Already invalid original content skips validation
    val.validate("key: : bad", "config.yaml", original_content="key: : bad")

    # 3. PyYAML not installed (ImportError)
    with patch.dict("sys.modules", {"yaml": None}):
        val.validate("key: val", "config.yaml")

    # 4. YAML without problem_mark (generic exception)
    with patch("yaml.safe_load", side_effect=Exception("Generic YAML fail")):
        with pytest.raises(SyntaxValidationError) as exc:
            val.validate("key: val", "config.yaml")
        assert "Generic YAML fail" in str(exc.value)


def test_js_ts_validator_find_biome_runners():
    val = JsTsValidator()

    # 1. pnpm dlx detection
    with patch.object(val, "_detect_package_manager", return_value="pnpm"), \
         patch("shutil.which", side_effect=lambda x: "/usr/bin/pnpm" if x == "pnpm" else None):
        runner, args = val._get_biome_command("app.ts", "check")
        assert runner == "/usr/bin/pnpm"
        assert args == ["dlx", "@biomejs/biome", "check"]

    # 2. yarn dlx detection
    with patch.object(val, "_detect_package_manager", return_value="yarn"), \
         patch("shutil.which", side_effect=lambda x: "/usr/bin/yarn" if x == "yarn" else None):
        runner, args = val._get_biome_command("app.ts", "check")
        assert runner == "/usr/bin/yarn"
        assert args == ["dlx", "@biomejs/biome", "check"]

    # 3. npm npx detection
    with patch.object(val, "_detect_package_manager", return_value="npm"), \
         patch("shutil.which", side_effect=lambda x: "/usr/bin/npx" if x == "npx" else None):
        runner, args = val._get_biome_command("app.ts", "check")
        assert runner == "/usr/bin/npx"
        assert args == ["--offline", "@biomejs/biome", "check"]


def test_js_ts_validator_syntax_error_sorting_and_text_fallback():
    val = JsTsValidator()

    # 1. Structured JSON diagnostics with sorting by line/column
    diagnostics = [
        {"category": "parse/error", "location": {"start": {"line": 10, "column": 5}}, "message": "second err"},
        {"category": "parse/error", "location": {"start": {"line": 2, "column": 3}}, "message": "first err"},
        {"category": "lint/style", "location": {"start": {"line": 1, "column": 1}}, "message": "lint warning"},
    ]
    earliest = val._find_json_syntax_error(diagnostics)
    assert earliest["message"] == "first err"

    # 2. Plain-text syntax error detection
    plain_output = "app.ts:5:12 parse ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n  × Expected a semicolon\n"
    res = val._find_text_syntax_error(plain_output, "app.ts")
    assert res is not None
    msg, line, col = res
    assert line == 5
    assert col == 12



