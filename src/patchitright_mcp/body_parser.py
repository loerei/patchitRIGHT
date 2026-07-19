"""Extract function body boundaries from JS/TS/TSX source code.

Primary: tree-sitter (via tree-sitter-language-pack) for 100% correct parsing.
Fallback: bracket-matching state machine for environments without tree-sitter.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Tree-sitter node types that represent function-like constructs with a "body" field.
FUNCTION_LIKE_TYPES = frozenset({
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
    "generator_function",
    "generator_function_declaration",
    # Group 1: C-Style
    "method_declaration",        # Java, C#
    "constructor_declaration",   # Java, C#
    "function_item",             # Rust
    "function_definition",       # C/C++, Python
    # Group 2: Python
    "async_function_definition", # Python async def
    "class_definition",          # Python, JS, C++
    # Group 3: CSS & HTML
    "rule_set",                  # CSS / SCSS
    "element",                   # HTML
})

# File extensions that are JSX/TSX — fallback bracket matcher is explicitly
# unsupported for these because JSX tags with nested braces confuse the state machine.
JSX_EXTENSIONS = frozenset({".jsx", ".tsx"})

# File size limits beyond which tree-sitter is skipped.
MAX_LINES_FOR_TREESITTER = 50_000
MAX_BYTES_FOR_TREESITTER = 5 * 1024 * 1024  # 5 MB

# Language mapping from file extension.
EXTENSION_TO_LANGUAGE = {
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",  # tree-sitter-javascript handles JSX
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    # Group 1: C-Style
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".java": "java",
    ".cs": "csharp",
    # Group 2: Python
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    # Group 3: CSS & HTML
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
}


@dataclass(frozen=True, slots=True)
class BodyRange:
    """Boundaries of a function body within a source file.

    All line numbers are 1-indexed. Column values are **character indices**
    (Python ``str`` offsets), NOT UTF-8 byte offsets.

    ``start_col`` / ``end_col`` point to the first / one-past-last character
    of the *inner* content:
    - For brace blocks: after ``{``, before ``}``
    - For arrow expressions: start/end of the expression node
    """

    start_line: int
    start_col: int
    end_line: int
    end_col: int
    is_expression: bool  # True for arrow expression bodies (no braces)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _check_large_file(source: str, line_count: int) -> tuple[bool, int]:
    """Return (is_large_file, byte_count) based on size thresholds."""
    char_count = len(source)
    if char_count > MAX_BYTES_FOR_TREESITTER:
        return True, char_count
    byte_count = len(source.encode("utf-8"))
    large_file = line_count > MAX_LINES_FOR_TREESITTER or byte_count > MAX_BYTES_FOR_TREESITTER
    return large_file, byte_count


def _run_tree_sitter_safe(
    source: str,
    language: str,
    symbol_start_line: int,
    symbol_end_line: int,
) -> Optional[BodyRange]:
    """Execute _try_tree_sitter catching exceptions safely."""
    try:
        return _try_tree_sitter(source, language, symbol_start_line, symbol_end_line)
    except ImportError:
        logger.warning(
            "tree-sitter-language-pack not available; falling back to bracket matching."
        )
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("tree-sitter parsing failed (%s); falling back to bracket matching.", exc)
    return None


def get_body_range(
    source: str,
    file_path: str,
    symbol_start_line: int,
    symbol_end_line: int,
) -> BodyRange:
    """Return the body range for the function at the given symbol line range.

    Parameters
    ----------
    source:
        Full file content as a Python string.
    file_path:
        File path (used to determine language from extension).
    symbol_start_line, symbol_end_line:
        1-indexed inclusive line range of the symbol (from jcodemunch index).

    Raises
    ------
    ValueError
        If the body cannot be determined (unsupported language, no body, etc.).
    """
    ext = os.path.splitext(file_path)[1].lower()
    language = EXTENSION_TO_LANGUAGE.get(ext)
    if not language:
        raise ValueError(
            f"Unsupported file extension '{ext}' for body parsing. "
            f"Supported: {', '.join(sorted(EXTENSION_TO_LANGUAGE.keys()))}"
        )

    lines = source.split("\n")
    line_count = len(lines)
    large_file, byte_count = _check_large_file(source, line_count)

    if not large_file:
        result = _run_tree_sitter_safe(source, language, symbol_start_line, symbol_end_line)
        if result is not None:
            return result
    else:
        logger.info(
            "File exceeds size limits (%d lines, %d bytes); skipping tree-sitter.",
            line_count,
            byte_count,
        )

    # Fallback: bracket matcher / AST parser.
    if language == "python":
        py_result = _python_ast_fallback(source, symbol_start_line, symbol_end_line)
        if py_result is not None:
            return py_result
        raise ValueError(
            f"Could not determine Python function body boundaries in '{file_path}' "
            f"(lines {symbol_start_line}-{symbol_end_line})."
        )

    if ext in JSX_EXTENSIONS:
        raise ValueError(
            f"Cannot use bracket-matching fallback for JSX/TSX file '{file_path}'. "
            "Install tree-sitter-language-pack or use start_line/end_line manual scoping."
        )

    try:
        result = _bracket_match_fallback(source, symbol_start_line, symbol_end_line)
    except ValueError:
        raise
    if result is None:
        raise ValueError(
            f"Could not determine function body boundaries in '{file_path}' "
            f"(lines {symbol_start_line}-{symbol_end_line})."
        )
    return result


# ---------------------------------------------------------------------------
# Indentation helpers
# ---------------------------------------------------------------------------

def detect_indent(body_lines: list[str], signature_line: str, all_file_lines: list[str]) -> str:
    """Detect the base indentation of the body content.

    Returns the whitespace prefix that each body line should start with.
    """
    non_empty = [line for line in body_lines if line.strip()]
    if non_empty:
        return min((line[: len(line) - len(line.lstrip())] for line in non_empty), key=len)

    # Empty body fallback: signature indent + 1 level.
    sig_indent = signature_line[: len(signature_line) - len(signature_line.lstrip())]
    indent_unit = _infer_indent_unit(all_file_lines)
    return sig_indent + indent_unit


def normalize_indent(replace_content: str, target_indent: str) -> tuple[str, bool, str]:
    """Re-indent ``replace_content`` to match ``target_indent``.

    Returns (re-indented content, was_adjusted, delta_description).
    """
    lines = replace_content.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return replace_content, False, ""

    src_indent = min((line[: len(line) - len(line.lstrip())] for line in non_empty), key=len)
    if src_indent == target_indent:
        return replace_content, False, ""

    src_len = len(src_indent)
    result_lines: list[str] = []
    for line in lines:
        if line.strip():
            # Strip source base indent and prepend target.
            if line.startswith(src_indent):
                line = target_indent + line[src_len:]
            else:
                # Line has less indent than source base — keep relative.
                line = target_indent + line.lstrip()
        result_lines.append(line)

    delta = f"{repr(src_indent)} → {repr(target_indent)}"
    return "\n".join(result_lines), True, delta


def pad_block_newlines(
    replace_content: str,
    signature_indent: str,
    is_crlf: bool,
) -> tuple[str, bool]:
    """Ensure ``replace_content`` has leading/trailing newlines for brace blocks.

    Returns (padded content, was_padded).
    """
    eol = "\r\n" if is_crlf else "\n"
    padded = False

    if not replace_content.startswith(("\n", "\r\n")):
        replace_content = eol + replace_content
        padded = True

    if not replace_content.endswith(("\n", "\r\n")):
        replace_content = replace_content + eol
        padded = True

    # Ensure the trailing newline is followed by the signature indent
    # so the closing `}` aligns with the function signature.
    if not replace_content.endswith(signature_indent + eol) and not replace_content.endswith(
        eol + signature_indent
    ):
        # Strip trailing whitespace-only portion after last newline and re-add indent.
        last_eol = replace_content.rfind("\n")
        if last_eol != -1:
            after_last_eol = replace_content[last_eol + 1 :]
            if not after_last_eol.strip():
                replace_content = replace_content[: last_eol + 1] + signature_indent
                padded = True

    return replace_content, padded


# ---------------------------------------------------------------------------
# Tree-sitter primary path
# ---------------------------------------------------------------------------

def _bytes_to_char_col(line_text: str, byte_col: int) -> int:
    """Convert a tree-sitter UTF-8 byte column to a Python str character index."""
    line_bytes = line_text.encode("utf-8")
    byte_prefix = line_bytes[:byte_col]
    return len(byte_prefix.decode("utf-8"))


def _try_tree_sitter(
    source: str,
    language: str,
    symbol_start_line: int,
    symbol_end_line: int,
) -> Optional[BodyRange]:
    """Parse with tree-sitter and find the function body overlapping the target range."""
    from tree_sitter_language_pack import get_parser  # type: ignore[import-untyped]

    parser = get_parser(language)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    lines = source.split("\n")

    # 0-indexed target range.
    target_start = symbol_start_line - 1
    target_end = symbol_end_line - 1

    def _find_best_function(node) -> Optional[tuple]:
        """Walk tree to find the widest function-like node within the target range.

        The index range comes from jcodemunch which may include leading
        JSDoc / decorators, so the AST node will typically be *inside*
        the target range.  Among all matching nodes we pick the largest
        (outermost) one because the index range describes the whole
        symbol, not an inner nested function.
        """
        best = None

        # In C/C++, function declarations in header files (prototypes) are parsed as "function_declaration".
        # We need to recognize them to raise a ValueError, but _find_best_function should only return nodes that actually have a body.
        # So we check that node.type is FUNCTION_LIKE_TYPES or function_declaration, but body must not be None.
        is_func = node.type in FUNCTION_LIKE_TYPES or node.type == "function_declaration"
        if is_func:
            n_start = node.start_point[0]
            n_end = node.end_point[0]
            if n_start >= target_start and n_end <= target_end:
                body = node.child_by_field_name("body")
                if body is not None:
                    best = (node, body)

        for child in node.children:
            result = _find_best_function(child)
            if result is not None:
                # Prefer wider (outermost) match — the symbol index
                # targets the outer function, not inner nested ones.
                if best is None:
                    best = result
                else:
                    _, prev_body = best
                    _, new_body = result
                    prev_span = prev_body.end_byte - prev_body.start_byte
                    new_span = new_body.end_byte - new_body.start_byte
                    if new_span > prev_span:
                        best = result

        return best

    match = _find_best_function(tree.root_node)

    if match is None:
        # Check if there's a function node without a body (abstract / interface).
        def _has_bodiless_function(node) -> bool:
            if (
                node.type in FUNCTION_LIKE_TYPES
                or node.type in ("function_signature", "method_signature", "abstract_method_signature")
                or node.type == "function_declaration"  # C/C++ forward declarations can be function_declaration
                or node.type == "function_definition"  # C/C++ function definitions
                or node.type == "declaration"           # generic declaration node which tree-sitter C/C++ might use
            ):
                n_start = node.start_point[0]
                n_end = node.end_point[0]
                if n_start >= target_start and n_end <= target_end:
                    body = node.child_by_field_name("body")
                    if body is None:
                        return True
                    # In C/C++, a function_definition AST node has a body but it might be missing in a prototype.
                    # Some TS overload signatures might have type declarations as their child but no body.
            return any(_has_bodiless_function(c) for c in node.children)

        if _has_bodiless_function(tree.root_node):
            raise ValueError(
                "Cannot use scope 'body' on a symbol without a body "
                "(e.g., abstract method, interface signature, or type declaration)."
            )
        return None

    _, body_node = match

    if body_node.type == "arrow_expression_clause":
        expr_child = body_node.child_by_field_name("expression")
        if expr_child is not None:
            body_node = expr_child

    if language == "python":
        block_start_row, block_start_byte_col = body_node.start_point
        block_end_row, block_end_byte_col = body_node.end_point
        return BodyRange(
            start_line=block_start_row + 1,
            start_col=_bytes_to_char_col(lines[block_start_row], block_start_byte_col),
            end_line=block_end_row + 1,
            end_col=_bytes_to_char_col(lines[block_end_row], block_end_byte_col),
            is_expression=False,
        )

    # Extract body node source to check if it's a brace block (starts with `{` and ends with `}`).
    body_bytes = source_bytes[body_node.start_byte : body_node.end_byte]
    try:
        body_text = body_bytes.decode("utf-8").strip()
    except Exception:
        body_text = ""

    is_expression = not (body_text.startswith("{") and body_text.endswith("}"))

    if is_expression:
        # Arrow expression body — return exact expression boundaries.
        start_row, start_byte_col = body_node.start_point
        end_row, end_byte_col = body_node.end_point
        return BodyRange(
            start_line=start_row + 1,
            start_col=_bytes_to_char_col(lines[start_row], start_byte_col),
            end_line=end_row + 1,
            end_col=_bytes_to_char_col(lines[end_row], end_byte_col),
            is_expression=True,
        )

    # Brace block — return inner content boundaries (after `{`, before `}`).
    block_start_row, block_start_byte_col = body_node.start_point
    block_end_row, block_end_byte_col = body_node.end_point

    # The statement_block node spans from `{` to `}` inclusive.
    # Inner content starts at column after `{` and ends at column before `}`.
    inner_start_col = _bytes_to_char_col(lines[block_start_row], block_start_byte_col) + 1  # after `{`
    inner_end_col = _bytes_to_char_col(lines[block_end_row], block_end_byte_col) - 1  # before `}`

    # Handle edge case: if `{` is at end of line, inner starts at next line col 0.
    # But we keep the raw column — the splice logic handles this via prefix/suffix.

    return BodyRange(
        start_line=block_start_row + 1,
        start_col=inner_start_col,
        end_line=block_end_row + 1,
        end_col=inner_end_col,
        is_expression=False,
    )


# ---------------------------------------------------------------------------
# Python AST fallback
# ---------------------------------------------------------------------------

def _python_ast_fallback(
    source: str,
    symbol_start_line: int,
    symbol_end_line: int,
) -> Optional[BodyRange]:
    """Extract Python function or class body range using stdlib ast module."""
    import ast

    try:
        tree = ast.parse(source)
    except Exception as exc:
        logger.warning("ast.parse failed on Python source: %s", exc)
        return None

    lines = source.split("\n")
    best_node = None
    best_span = -1

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            n_start = node.lineno
            n_end = getattr(node, "end_lineno", n_start)
            # Find node within target range (1-indexed line bounds from indexer)
            if n_start >= symbol_start_line and n_end <= symbol_end_line:
                if node.body:
                    first_stmt = node.body[0]
                    last_stmt = node.body[-1]
                    s_line = first_stmt.lineno
                    e_line = getattr(last_stmt, "end_lineno", s_line)
                    span = (e_line - s_line + 1) * 1000 + (getattr(last_stmt, "end_col_offset", 0) - first_stmt.col_offset)
                    if span > best_span:
                        best_span = span
                        best_node = node

    if best_node and best_node.body:
        first_stmt = best_node.body[0]
        last_stmt = best_node.body[-1]
        start_line = first_stmt.lineno
        start_col = first_stmt.col_offset
        end_line = getattr(last_stmt, "end_lineno", last_stmt.lineno)
        end_col = getattr(last_stmt, "end_col_offset", len(lines[end_line - 1]))
        return BodyRange(
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
            is_expression=False,
        )

    return None


# ---------------------------------------------------------------------------
# Bracket-matching fallback
# ---------------------------------------------------------------------------

def _bracket_match_fallback(
    source: str,
    symbol_start_line: int,
    symbol_end_line: int,
) -> Optional[BodyRange]:
    """Find function body boundaries using a bracket-matching state machine.

    Known limitations:
    - Cannot handle regex literals (would need a partial lexer).
    - Cannot detect arrow expression bodies (no braces).
    - Explicitly unsupported for JSX/TSX (caller must check).
    - Parameter destructuring ``function foo({ a, b }) { ... }`` will be
      misidentified: the state machine treats the first ``{`` as the body
      opening brace and returns immediately when it finds the matching ``}``
      after the parameters.  Tree-sitter handles this correctly and is
      always preferred; this limitation only affects the fallback path.
    """
    lines = source.split("\n")
    start_idx = symbol_start_line - 1
    end_idx = min(symbol_end_line, len(lines)) - 1

    # State machine flags.
    in_single_quote = False
    in_double_quote = False
    in_template = False
    in_line_comment = False
    in_block_comment = False
    template_depth_stack: list[int] = []  # stack for nested ${} in template literals

    depth = 0
    body_start: Optional[tuple[int, int]] = None  # (line_0idx, col)
    prev_char = ""

    for line_idx in range(start_idx, end_idx + 1):
        line = lines[line_idx]
        i = 0

        if in_line_comment:
            in_line_comment = False  # line comments end at newline

        while i < len(line):
            c = line[i]

            # Handle escape sequences — skip the escaped character.
            is_escaped = False
            if prev_char == "\\":
                # Check if the backslash itself is escaped (count consecutive backslashes).
                backslash_count = 0
                j = i - 1
                while j >= 0 and line[j] == "\\":
                    backslash_count += 1
                    j -= 1
                is_escaped = backslash_count % 2 == 1

            if is_escaped:
                prev_char = ""  # reset so next char isn't considered escaped
                i += 1
                continue

            # Block comment.
            if in_block_comment:
                if c == "*" and i + 1 < len(line) and line[i + 1] == "/":
                    in_block_comment = False
                    prev_char = "/"
                    i += 2
                    continue
                prev_char = c
                i += 1
                continue

            # Line comment.
            if in_line_comment:
                prev_char = c
                i += 1
                continue

            # String states.
            if in_single_quote:
                if c == "'":
                    in_single_quote = False
                prev_char = c
                i += 1
                continue

            if in_double_quote:
                if c == '"':
                    in_double_quote = False
                prev_char = c
                i += 1
                continue

            if in_template:
                if c == "`":
                    in_template = False
                elif c == "$" and i + 1 < len(line) and line[i + 1] == "{":
                    template_depth_stack.append(depth)
                    in_template = False  # exit template mode, enter normal mode for expression
                    depth += 1
                    prev_char = "{"
                    i += 2
                    continue
                prev_char = c
                i += 1
                continue

            # Detect start of strings, comments, templates.
            if c == "'":
                in_single_quote = True
            elif c == '"':
                in_double_quote = True
            elif c == "`":
                in_template = True
            elif c == "/" and i + 1 < len(line):
                next_c = line[i + 1]
                if next_c == "/":
                    in_line_comment = True
                    prev_char = "/"
                    i += 2
                    continue
                elif next_c == "*":
                    in_block_comment = True
                    prev_char = "*"
                    i += 2
                    continue
                # Note: regex disambiguation NOT handled — known limitation.
            elif c == "{":
                depth += 1
                if body_start is None:
                    # This is the opening brace of the function body.
                    body_start = (line_idx, i)
            elif c == "}":
                depth -= 1
                if depth == 0 and body_start is not None:
                    # Found the matching closing brace.
                    return BodyRange(
                        start_line=body_start[0] + 1,
                        start_col=body_start[1] + 1,  # after `{`
                        end_line=line_idx + 1,
                        end_col=i,  # before `}`
                        is_expression=False,
                    )
                # Check if we're returning from a template expression.
                if template_depth_stack and depth == template_depth_stack[-1]:
                    template_depth_stack.pop()
                    in_template = True  # re-enter template literal mode

            prev_char = c
            i += 1

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _gather_indents(sample: list[str]) -> tuple[int, list[int]]:
    """Gather tab counts and space indents from sample lines."""
    tab_count = 0
    space_deltas: list[int] = []
    for line in sample:
        stripped = line.lstrip()
        if stripped:
            leading = line[: len(line) - len(stripped)]
            if leading.startswith("\t"):
                tab_count += 1
            elif leading.startswith(" "):
                space_deltas.append(len(leading))
    return tab_count, space_deltas


def _infer_indent_unit(file_lines: list[str]) -> str:
    """Infer the indentation unit (tab or N spaces) by sampling the file."""
    tab_count, space_deltas = _gather_indents(file_lines[:50])

    if tab_count > len(space_deltas):
        return "\t"

    if space_deltas:
        import statistics
        valid_deltas = [s for s in space_deltas if s >= 2]
        if valid_deltas:
            try:
                return " " * statistics.mode(valid_deltas)
            except Exception:
                return " " * min(valid_deltas)

    return "  "  # Default: 2 spaces for JS/TS.
