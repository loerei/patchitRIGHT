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
    char_count = len(source)
    if char_count > MAX_BYTES_FOR_TREESITTER:
        large_file = True
        byte_count = char_count
    else:
        byte_count = len(source.encode("utf-8"))
        large_file = line_count > MAX_LINES_FOR_TREESITTER or byte_count > MAX_BYTES_FOR_TREESITTER

    if not large_file:
        try:
            result = _try_tree_sitter(source, language, symbol_start_line, symbol_end_line)
            if result is not None:
                return result
        except ImportError:
            logger.warning(
                "tree-sitter-language-pack not available; falling back to bracket matching."
            )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tree-sitter parsing failed (%s); falling back to bracket matching.", exc)
    else:
        logger.info(
            "File exceeds size limits (%d lines, %d bytes); skipping tree-sitter.",
            line_count,
            byte_count,
        )

    # Fallback: bracket matcher.
    if ext in JSX_EXTENSIONS:
        raise ValueError(
            f"Cannot use bracket-matching fallback for JSX/TSX file '{file_path}'. "
            "Install tree-sitter-language-pack or use start_line/end_line manual scoping."
        )

    result = _bracket_match_fallback(source, symbol_start_line, symbol_end_line)
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

        if node.type in FUNCTION_LIKE_TYPES:
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
            ):
                n_start = node.start_point[0]
                n_end = node.end_point[0]
                if n_start >= target_start and n_end <= target_end:
                    if node.child_by_field_name("body") is None:
                        return True
            return any(_has_bodiless_function(c) for c in node.children)

        if _has_bodiless_function(tree.root_node):
            raise ValueError(
                "Cannot use scope 'body' on a symbol without a body "
                "(e.g., abstract method, interface signature, or type declaration)."
            )
        return None

    func_node, body_node = match
    is_expression = body_node.type != "statement_block"

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

def _infer_indent_unit(file_lines: list[str]) -> str:
    """Infer the indentation unit (tab or N spaces) by sampling the file."""
    sample = file_lines[:50]
    tab_count = 0
    space_deltas: list[int] = []

    for line in sample:
        stripped = line.lstrip()
        if not stripped:
            continue
        leading = line[: len(line) - len(stripped)]
        if leading.startswith("\t"):
            tab_count += 1
        elif leading.startswith(" "):
            space_count = len(leading)
            if space_count > 0:
                space_deltas.append(space_count)

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
