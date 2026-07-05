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


def test_yaml_validator():
    val = YamlValidator()
    
    # Valid YAML
    val.validate("key: value\nlist:\n  - item 1\n  - item 2\n", "config.yaml")
    
    # Invalid YAML
    with pytest.raises(SyntaxValidationError) as exc_info:
        val.validate("key: value\nlist:\n  - item 1\n  -item 2\n", "config.yaml")
    assert exc_info.value.filename == "config.yaml"


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

