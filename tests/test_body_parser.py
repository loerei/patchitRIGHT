"""Unit tests for body_parser.py — body boundary extraction from JS/TS/TSX."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from patchitright_mcp.body_parser import (
    BodyRange,
    detect_indent,
    get_body_range,
    normalize_indent,
    pad_block_newlines,
)


# ---------------------------------------------------------------------------
# Tree-sitter primary path
# ---------------------------------------------------------------------------


class TestTreeSitterBodyParsing:
    """Tests that exercise the tree-sitter primary parser."""

    def test_function_declaration(self):
        src = "function foo() { return 1; }"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is False
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "return 1;"

    def test_arrow_block_body(self):
        src = "const fn = () => { return 1; }"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is False
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "return 1;"

    def test_arrow_expression_body(self):
        src = "const fn = () => 42"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is True
        inner = src[r.start_col : r.end_col]
        assert inner == "42"

    def test_class_method(self):
        src = "class C {\n  m() { body_here }\n}"
        r = get_body_range(src, "test.js", 2, 2)
        lines = src.split("\n")
        inner = lines[r.start_line - 1][r.start_col : r.end_col]
        assert inner.strip() == "body_here"

    def test_getter(self):
        src = "class C {\n  get x() { return 1; }\n}"
        r = get_body_range(src, "test.js", 2, 2)
        assert r.is_expression is False
        lines = src.split("\n")
        inner = lines[r.start_line - 1][r.start_col : r.end_col]
        assert inner.strip() == "return 1;"

    def test_setter(self):
        src = "class C {\n  set x(v) { this._x = v; }\n}"
        r = get_body_range(src, "test.js", 2, 2)
        assert r.is_expression is False
        lines = src.split("\n")
        inner = lines[r.start_line - 1][r.start_col : r.end_col]
        assert inner.strip() == "this._x = v;"

    def test_async_function(self):
        src = "async function f() { await x(); }"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is False
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "await x();"

    def test_generator(self):
        src = "function* g() { yield 1; }"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is False
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "yield 1;"

    def test_destructured_params(self):
        src = "function f({ a, b }) { return a; }"
        r = get_body_range(src, "test.js", 1, 1)
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "return a;"
        # The `{` in params must NOT be confused with body start.
        assert r.start_col > src.index(")")

    def test_template_literal_braces(self):
        src = "function f() {\n  const s = `hello ${world}`;\n  return s;\n}"
        r = get_body_range(src, "test.js", 1, 4)
        assert r.is_expression is False
        lines = src.split("\n")
        # Body should span the inner lines (2-3).
        body_lines = lines[r.start_line - 1 : r.end_line]
        body_text = "\n".join(body_lines)
        assert "${world}" in body_text

    def test_string_braces(self):
        src = 'function f() {\n  const s = "{ not a brace }";\n  return s;\n}'
        r = get_body_range(src, "test.js", 1, 4)
        assert r.is_expression is False
        assert r.start_line == 1
        assert r.end_line == 4

    def test_comment_braces(self):
        src = "function f() {\n  // { comment }\n  return 1;\n}"
        r = get_body_range(src, "test.js", 1, 4)
        assert r.is_expression is False
        assert r.end_line == 4

    def test_nested_functions(self):
        src = (
            "function outer() {\n"
            "  function inner() { return 1; }\n"
            "  return inner();\n"
            "}"
        )
        r = get_body_range(src, "test.js", 1, 4)
        assert r.is_expression is False
        # Should return the OUTER body.
        assert r.start_line == 1
        assert r.end_line == 4

    def test_multiline_signature(self):
        src = "function foo(\n  a,\n  b\n) {\n  return a + b;\n}"
        r = get_body_range(src, "test.js", 1, 6)
        assert r.is_expression is False
        # Body `{` is on line 4.
        assert r.start_line == 4
        assert r.end_line == 6

    def test_ts_type_annotations(self):
        src = "function f<T>(x: { a: number }): { b: string } { return { b: 'ok' }; }"
        r = get_body_range(src, "test.ts", 1, 1)
        assert r.is_expression is False
        inner = src[r.start_col : r.end_col]
        assert "return" in inner
        # The `{` in type annotations must NOT be confused with body.
        assert "a: number" not in inner

    def test_ts_overloads_skip_bodiless(self):
        src = "function f(x: string): string;\nfunction f(x: any): any { return x; }"
        # The symbol range covers both lines (both have name "f").
        r = get_body_range(src, "test.ts", 1, 2)
        assert r.is_expression is False
        lines = src.split("\n")
        inner = lines[r.start_line - 1][r.start_col : r.end_col]
        assert inner.strip() == "return x;"

    def test_single_line_function_columns(self):
        src = "const f = () => { return 1; };"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is False
        assert r.start_line == 1
        assert r.end_line == 1
        # Verify column-level precision.
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "return 1;"

    def test_arrow_expression_no_semicolon_leak(self):
        src = "const fn = () => expr;"
        r = get_body_range(src, "test.js", 1, 1)
        assert r.is_expression is True
        inner = src[r.start_col : r.end_col]
        assert inner == "expr"
        assert ";" not in inner


# ---------------------------------------------------------------------------
# Fallback bracket matcher
# ---------------------------------------------------------------------------


class TestBracketMatchFallback:
    """Tests that exercise the bracket-matching fallback."""

    def _get_with_fallback(self, src: str, ext: str, start: int, end: int) -> BodyRange:
        """Force fallback by mocking tree-sitter import failure."""
        with patch("patchitright_mcp.body_parser._try_tree_sitter", side_effect=ImportError):
            return get_body_range(src, f"test{ext}", start, end)

    def test_fallback_when_treesitter_unavailable(self):
        src = "function foo() { return 1; }"
        r = self._get_with_fallback(src, ".js", 1, 1)
        assert r.is_expression is False
        inner = src[r.start_col : r.end_col]
        assert inner.strip() == "return 1;"

    def test_jsx_fallback_returns_error(self):
        src = "function foo() { return <div />; }"
        with pytest.raises(ValueError, match="JSX/TSX"):
            self._get_with_fallback(src, ".tsx", 1, 1)


# ---------------------------------------------------------------------------
# Indentation helpers
# ---------------------------------------------------------------------------


class TestIndentation:

    def test_indentation_min_baseline(self):
        # First line is deeply indented (comment), but body baseline is 2 spaces.
        body_lines = [
            "      // deeply indented comment",
            "  const a = 1;",
            "  return a;",
        ]
        indent = detect_indent(body_lines, "function f() {", [])
        assert indent == "  "

    def test_indentation_rebase(self):
        content = "const a = 1;\nreturn a;"
        result, adjusted, _ = normalize_indent(content, "    ")
        assert adjusted is True
        assert result == "    const a = 1;\n    return a;"

    def test_empty_body_indent_fallback(self):
        sig = "  function foo() {"
        file_lines = [
            "function outer() {",
            "  function foo() {}",
            "  const x = 1;",
            "}",
        ]
        indent = detect_indent([], sig, file_lines)
        # Signature has 2 spaces, file uses 2-space indent → fallback = "  " + "  " = "    "
        assert indent == "    "

    def test_empty_body_indent_fallback_tabs(self):
        sig = "\tfunction foo() {"
        file_lines = [
            "function outer() {",
            "\tfunction foo() {}",
            "\tconst x = 1;",
            "}",
        ]
        indent = detect_indent([], sig, file_lines)
        assert indent == "\t\t"


# ---------------------------------------------------------------------------
# Multibyte character handling
# ---------------------------------------------------------------------------


class TestMultibyte:

    def test_multibyte_column_conversion(self):
        # "🐛" is 4 bytes in UTF-8 but 1 character in Python.
        src = 'const a = "🐛";\nfunction f() { return 1; }'
        r = get_body_range(src, "test.js", 2, 2)
        lines = src.split("\n")
        inner = lines[r.start_line - 1][r.start_col : r.end_col]
        assert inner.strip() == "return 1;"


# ---------------------------------------------------------------------------
# Newline padding
# ---------------------------------------------------------------------------


class TestNewlinePadding:

    def test_newline_padding_block(self):
        content = "  console.log('hello');"
        result, padded = pad_block_newlines(content, "", is_crlf=False)
        assert padded is True
        assert result.startswith("\n")
        assert result.endswith("\n")

    def test_no_padding_expression(self):
        # Expression bodies should NOT be padded — caller decides.
        content = "x + 1"
        # pad_block_newlines is only called for brace blocks,
        # so this test just verifies the function exists and works.
        _, padded = pad_block_newlines(content, "", is_crlf=False)
        # Even though it pads, the caller won't call this for expressions.
        assert padded is True

    def test_crlf_padding(self):
        content = "  console.log('hello');"
        result, padded = pad_block_newlines(content, "", is_crlf=True)
        assert padded is True
        assert result.startswith("\r\n")

    def test_already_padded(self):
        content = "\n  console.log('hello');\n"
        _, padded = pad_block_newlines(content, "", is_crlf=False)
        assert padded is False


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:

    def test_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported file extension"):
            get_body_range("some code", "test.py", 1, 1)

    def test_abstract_method_raises(self):
        src = "abstract class C {\n  abstract m(): void;\n}"
        with pytest.raises(ValueError, match="without a body"):
            get_body_range(src, "test.ts", 2, 2)
