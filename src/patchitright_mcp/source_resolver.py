"""Source file reading, partial line/symbol slicing, anchor verification, and cut/move helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .line_matcher import _resolve_ast_boundaries
from .validators import SyntaxValidationError, ValidationService
from .workspace import Workspace


@dataclass
class ResolvedSource:
    """Result of resolving and optionally slicing content from source_file."""

    source_file: str
    source_path: Path
    content: Union[str, bytes]
    original_content: Union[str, bytes]
    is_sliced: bool
    is_binary: bool
    source_modified_content: Optional[str] = None
    source_action: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    suggestion: str = ""


def resolve_source_slice(
    workspace: Workspace,
    cwd: Path,
    source_file: str,
    source_start_line: Optional[int] = None,
    source_end_line: Optional[int] = None,
    source_symbol: Optional[str] = None,
    source_start_anchor: Optional[str] = None,
    source_end_anchor: Optional[str] = None,
    delete_source: bool = False,
    cut_source: bool = False,
    allow_binary: bool = False,
    storage_path: Optional[str] = None,
    bypass_validation: bool = False,
) -> tuple[Optional[ResolvedSource], Optional[dict]]:
    """Load source_file and optionally extract a guarded line range or AST symbol slice."""
    norm_source = os.path.normpath(source_file)
    try:
        source_path = workspace.resolve_safe_path(norm_source)
    except ValueError as err:
        from .diagnostics import _handle_patch_file_value_error
        return None, _handle_patch_file_value_error(err, norm_source)

    if not source_path.exists():
        return None, {"error": f"Source file not found at {source_path}.", "target_file": norm_source}
    if source_path.is_dir():
        return None, {"error": f"Source path '{norm_source}' is a directory, not a file.", "target_file": norm_source}

    if delete_source and cut_source:
        return None, {"error": "Error: Cannot combine 'delete_source=true' with 'cut_source=true'."}

    has_line_bounds = source_start_line is not None or source_end_line is not None
    has_symbol = bool(source_symbol)
    if has_line_bounds and has_symbol:
        return None, {"error": "Error: Cannot combine 'source_symbol' with 'source_start_line' or 'source_end_line'."}

    if has_line_bounds and (source_start_line is None or source_end_line is None):
        return None, {"error": "Error: Both 'source_start_line' and 'source_end_line' must be provided together."}

    is_sliced = has_line_bounds or has_symbol
    if not is_sliced and (source_start_anchor is not None or source_end_anchor is not None):
        return None, {
            "error": "Error: 'source_start_anchor' and 'source_end_anchor' require 'source_start_line'/'source_end_line' or 'source_symbol'."
        }

    if delete_source and is_sliced:
        return None, {
            "error": (
                "Error: Cannot use 'delete_source=true' with a partial slice. "
                "Use 'cut_source=true' to remove only the extracted slice, or omit slice parameters to move the whole file."
            )
        }
    if cut_source and not is_sliced:
        return None, {
            "error": (
                "Error: Cannot use 'cut_source=true' without a slice ('source_start_line'/'source_end_line' or 'source_symbol'). "
                "Use 'delete_source=true' to move/delete the entire source file."
            )
        }

    try:
        with open(source_path, "rb") as f:
            raw_bytes = f.read()
    except Exception as err:
        return None, {"error": f"Failed to read source_file '{norm_source}': {err}"}

    is_binary_raw = False
    decoded_text: Optional[str] = None
    if b"\x00" in raw_bytes:
        is_binary_raw = True
    else:
        try:
            decoded_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            if allow_binary and not is_sliced:
                is_binary_raw = True
            else:
                decoded_text = raw_bytes.decode("utf-8", errors="replace")

    if is_binary_raw:
        if not allow_binary or is_sliced or cut_source:
            return None, {"error": f"Error: Cannot read or slice binary source_file '{norm_source}' as text."}
        return ResolvedSource(
            source_file=norm_source,
            source_path=source_path,
            content=raw_bytes,
            original_content=raw_bytes,
            is_sliced=False,
            is_binary=True,
            source_modified_content=None,
            source_action="delete" if delete_source else None,
        ), None

    assert decoded_text is not None
    is_crlf = "\r\n" in decoded_text
    norm_text = decoded_text.replace("\r\n", "\n").replace("\r", "")

    if not is_sliced:
        return ResolvedSource(
            source_file=norm_source,
            source_path=source_path,
            content=decoded_text,
            original_content=decoded_text,
            is_sliced=False,
            is_binary=False,
            source_modified_content=None,
            source_action="delete" if delete_source else None,
        ), None

    lines = norm_text.split("\n")
    total_lines = len(lines)

    if has_symbol:
        assert source_symbol is not None
        sym_start, sym_end, sym_err, _ = _resolve_ast_boundaries(
            cwd=cwd,
            target_path=source_path,
            symbol_name=source_symbol,
            storage_path=storage_path,
            start_line=None,
            end_line=None,
            symbol_scope="full",
            file_content=norm_text,
        )
        if sym_err:
            return None, sym_err
        assert sym_start is not None and sym_end is not None
        start_l, end_l = sym_start, sym_end
    else:
        assert source_start_line is not None and source_end_line is not None
        start_l, end_l = source_start_line, source_end_line

    if start_l < 1 or end_l < start_l:
        return None, {"error": f"Error: Invalid source line range {start_l}-{end_l} in '{norm_source}'."}
    if end_l > total_lines:
        return None, {
            "error": f"Error: Source line range {start_l}-{end_l} exceeds total lines ({total_lines}) in '{norm_source}'."
        }

    start_line_text = lines[start_l - 1]
    end_line_text = lines[end_l - 1]
    if source_start_anchor is not None and source_start_anchor not in start_line_text:
        return None, {
            "error": (
                f"Error: source_start_anchor mismatch at line {start_l} of '{norm_source}'. "
                f"Expected '{source_start_anchor}', found: {start_line_text!r}"
            )
        }
    if source_end_anchor is not None and source_end_anchor not in end_line_text:
        return None, {
            "error": (
                f"Error: source_end_anchor mismatch at line {end_l} of '{norm_source}'. "
                f"Expected '{source_end_anchor}', found: {end_line_text!r}"
            )
        }

    extracted = "\n".join(lines[start_l - 1 : end_l])
    if is_crlf:
        extracted = extracted.replace("\n", "\r\n")

    source_modified_content: Optional[str] = None
    source_warnings: list[str] = []
    source_suggestion = ""
    if cut_source:
        remaining_lines = lines[: start_l - 1] + lines[end_l:]
        remaining_text = "\n".join(remaining_lines)
        if is_crlf:
            remaining_text = remaining_text.replace("\n", "\r\n")

        if not bypass_validation:
            validator = ValidationService()
            try:
                validator.validate_file(norm_source, remaining_text, decoded_text)
            except SyntaxValidationError as err:
                return None, {
                    "error": f"Syntax Error in source_file '{norm_source}' after cut_source: {err}",
                    "filename": err.filename,
                    "line": err.line,
                    "column": err.column,
                }
            raw_warnings = validator.lint_file(norm_source, remaining_text)
            source_warnings = ValidationService.filter_warnings(raw_warnings)
            if source_warnings:
                from .self_mod_safety import _get_linter_suggestion
                source_suggestion = _get_linter_suggestion(norm_source)

        source_modified_content = remaining_text

    return ResolvedSource(
        source_file=norm_source,
        source_path=source_path,
        content=extracted,
        original_content=decoded_text,
        is_sliced=True,
        is_binary=False,
        source_modified_content=source_modified_content,
        source_action="patch" if cut_source else None,
        warnings=source_warnings,
        suggestion=source_suggestion,
    ), None
