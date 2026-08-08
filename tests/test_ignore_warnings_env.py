from patchitright_mcp.validators import ValidationService

def test_filter_warnings_all(monkeypatch):
    warnings = [
        "x Formatter would have printed...",
        "F401 'os' imported but unused"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "all")
    filtered = ValidationService.filter_warnings(warnings)
    assert filtered == []

    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "1")
    filtered = ValidationService.filter_warnings(warnings)
    assert filtered == []

def test_filter_warnings_format_only(monkeypatch):
    warnings = [
        "x Formatter would have printed the following content:",
        "Tab vs space mismatch",
        "F401 'os' imported but unused"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "format")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 1
    assert "F401" in filtered[0]

def test_filter_warnings_formatting_alias(monkeypatch):
    warnings = [
        "x Formatter would have printed the following content:",
        "Tab vs space mismatch",
        "F401 'os' imported but unused"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "formatting")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 1
    assert "F401" in filtered[0]

def test_filter_warnings_codesmell_only(monkeypatch):
    warnings = [
        "x Formatter would have printed the following content:",
        "F401 'os' imported but unused"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "codesmell")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 1
    assert "Formatter" in filtered[0]

def test_filter_warnings_lint_aliases(monkeypatch):
    warnings = [
        "x Formatter would have printed the following content:",
        "F401 'os' imported but unused"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "lint")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 1
    assert "Formatter" in filtered[0]

    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "linter")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 1
    assert "Formatter" in filtered[0]

def test_filter_warnings_combined(monkeypatch):
    warnings = [
        "x Formatter would have printed the following content:",
        "F401 'os' imported but unused"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "format,codesmell")
    filtered = ValidationService.filter_warnings(warnings)
    assert filtered == []

def test_filter_warnings_multiline_formatter_block(monkeypatch):
    warnings = [
        "x Formatter would have printed the following content:",
        "1 | - import React from 'react';",
        "2 | + import React from \"react\";",
        "! All these imports are only used as types.",
        "1 | import React from 'react';"
    ]
    # Test format only ignore -> should remove the 3 formatter lines and keep the 2 lint lines
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "format")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 2
    assert filtered[0].startswith("!")
    assert filtered[1].startswith("1 |")

    # Test codesmell only ignore -> should remove the 2 lint lines and keep the 3 formatter lines
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "codesmell")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 3
    assert filtered[0].startswith("x Formatter")

