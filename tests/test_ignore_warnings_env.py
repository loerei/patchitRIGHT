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

def test_parse_ignored_categories():
    assert ValidationService.parse_ignored_categories("") == set()
    assert ValidationService.parse_ignored_categories("all") == {"all", "symbol", "insertion", "lint", "format"}
    assert ValidationService.parse_ignored_categories("*") == {"all", "symbol", "insertion", "lint", "format"}
    assert ValidationService.parse_ignored_categories("true") == {"all", "symbol", "insertion", "lint", "format"}
    assert ValidationService.parse_ignored_categories("symbol,format") == {"symbol", "format"}
    assert ValidationService.parse_ignored_categories("symbols,insertions,linter,formatting") == {"symbol", "insertion", "lint", "format"}

def test_filter_warnings_symbol_category(monkeypatch):
    warnings = [
        "Symbol Omission Alert: 'foo' was declared in original slice and referenced on lines 10, but is missing from replace_content.",
        "F401 'os' imported but unused",
        "Warning: Specified insert_line (100) exceeds total file lines (50). Clamped insertion to end-of-file.",
        "x Formatter would have printed the following content:"
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "symbol")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 3
    assert not any("Symbol Omission Alert" in w for w in filtered)

    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "symbols")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 3
    assert not any("Symbol Omission Alert" in w for w in filtered)

def test_filter_warnings_insertion_category(monkeypatch):
    warnings = [
        "Symbol Omission Alert: 'foo' was declared in original slice and referenced on lines 10, but is missing from replace_content.",
        "F401 'os' imported but unused",
        "Warning: Specified insert_line (100) exceeds total file lines (50). Clamped insertion to end-of-file.",
        "Warning: Could not infer reference indentation for auto_indent. Defaulted to top-level (0 spaces).",
        "Warning: File uses spaces for indentation, but inserted content contains tabs while auto_indent=False."
    ]
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "insertion")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 2
    assert "Symbol Omission Alert" in filtered[0]
    assert "F401" in filtered[1]

    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "line")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 2

def test_filter_warnings_combined_categories(monkeypatch):
    warnings = [
        "Symbol Omission Alert: 'foo' was declared in original slice and referenced on lines 10, but is missing from replace_content.",
        "F401 'os' imported but unused",
        "Warning: Specified insert_line (100) exceeds total file lines (50). Clamped insertion to end-of-file.",
        "x Formatter would have printed the following content:"
    ]
    # Ignore symbol and format -> only lint and insertion remain
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "symbol,format")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 2
    assert "F401" in filtered[0]
    assert "exceeds total file lines" in filtered[1]

    # Ignore insertion and lint -> only symbol and format remain
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "insertion,lint")
    filtered = ValidationService.filter_warnings(warnings)
    assert len(filtered) == 2
    assert "Symbol Omission Alert" in filtered[0]
    assert "Formatter" in filtered[1]


def test_lint_file_delegation_and_short_circuit(monkeypatch):
    """Test ValidationService.lint_file delegation of ignore flags to validators and early short-circuiting."""
    service = ValidationService()
    calls = []

    class MockValidator:
        def lint(self, content: str, filename: str, ignore_format: bool = False, ignore_codesmell: bool = False) -> list[str]:
            calls.append((ignore_format, ignore_codesmell))
            return [
                "x Formatter would have printed formatted output",
                "F401 'unused' imported but unused",
                "Symbol Omission Alert: 'foo' was omitted",
            ]

    monkeypatch.setattr(service, "_get_validator", lambda fn: MockValidator())

    # 1. Default (no env set) -> both flags False, all 3 warnings returned
    monkeypatch.delenv("PATCHITRIGHT_IGNORE_WARNINGS", raising=False)
    res = service.lint_file("test.py", "some content")
    assert len(calls) == 1
    assert calls[-1] == (False, False)
    assert len(res) == 3

    # 2. ignore format -> ignore_format=True, ignore_codesmell=False, format warning filtered out
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "format")
    res = service.lint_file("test.py", "some content")
    assert len(calls) == 2
    assert calls[-1] == (True, False)
    assert len(res) == 2
    assert not any("Formatter" in w for w in res)

    # 3. ignore lint -> ignore_format=False, ignore_codesmell=True, lint warning filtered out
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "lint")
    res = service.lint_file("test.py", "some content")
    assert len(calls) == 3
    assert calls[-1] == (False, True)
    assert len(res) == 2
    assert not any("F401" in w for w in res)

    # 4. Short-circuit: ignore all -> validator is NOT called at all
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "all")
    res = service.lint_file("test.py", "some content")
    assert len(calls) == 3  # No new call made
    assert res == []

    # 5. Short-circuit: ignore format,lint -> validator is NOT called at all
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "format,lint")
    res = service.lint_file("test.py", "some content")
    assert len(calls) == 3  # No new call made
    assert res == []


def test_lint_file_explicit_ignored_categories_argument(monkeypatch):
    """Test ValidationService.lint_file with explicit ignored_categories parameter overriding env var."""
    service = ValidationService()
    calls = []

    class MockValidator:
        def lint(self, content: str, filename: str, ignore_format: bool = False, ignore_codesmell: bool = False) -> list[str]:
            calls.append((ignore_format, ignore_codesmell))
            return [
                "x Formatter would have printed formatted output",
                "F401 'unused' imported but unused",
            ]

    monkeypatch.setattr(service, "_get_validator", lambda fn: MockValidator())
    monkeypatch.setenv("PATCHITRIGHT_IGNORE_WARNINGS", "all")  # Should be overridden by explicit param

    res = service.lint_file("test.py", "some content", ignored_categories={"format"})
    assert len(calls) == 1
    assert calls[0] == (True, False)
    assert len(res) == 1
    assert "F401" in res[0]


def test_lint_file_unregistered_extension():
    """Test ValidationService.lint_file on unsupported extension returns empty list."""
    service = ValidationService()
    assert service.lint_file("file.unknown_extension", "content") == []


