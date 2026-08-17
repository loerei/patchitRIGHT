"""In-memory chunk replacement engine and patch application helpers."""
from pathlib import Path
from typing import Optional
from .engine import PatchEngine
from .line_matcher import (
    _resolve_ast_boundaries,
    check_replacement_collisions,
    sort_resolved_items_descending,
)
from .self_mod_safety import _get_linter_suggestion
from .validators import SyntaxValidationError, ValidationService


def _apply_patch_content(
    file_content: str,
    target_file: str,
    patch_content: str,
    bypass_validation: bool = False,
) -> tuple[str, int, list[str], str, Optional[dict], Optional[PatchEngine]]:
    """Apply unified diff patch content via PatchEngine."""
    engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
    try:
        patched_file = engine.apply_unified_patch(patch_content)
        from .symbol_checker import extract_net_diff_declarations, detect_net_omitted_symbols
        deleted_syms = extract_net_diff_declarations(patch_content, target_file)
        diff_sym_warnings = detect_net_omitted_symbols(
            patched_content=patched_file,
            deleted_symbols=deleted_syms,
            filename=target_file,
        )
        linter_warnings = list(diff_sym_warnings) + list(getattr(engine, "linter_warnings", []))
        filtered_warnings = ValidationService.filter_warnings(linter_warnings)
        suggestion = _get_linter_suggestion(target_file) if filtered_warnings else ""
        return patched_file, 1, filtered_warnings, suggestion, None, engine
    except SyntaxValidationError as e:
        return "", 0, [], "", {
            "error": f"Syntax Error: {str(e)}",
            "filename": e.filename,
            "line": e.line,
            "column": e.column
        }, None
    except ValueError as e:
        return "", 0, [], "", {"error": str(e)}, None


def _process_single_file_in_memory(
    target_file: str,
    target_path: Path,
    file_content: str,
    bypass_validation: bool,
    cwd: Path,
    storage_path: Optional[str],
    search_content: Optional[str] = None,
    replace_content: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    patch_content: Optional[str] = None,
    replacements: Optional[list[dict]] = None,
    **kwargs,
) -> tuple[str, int, list[str], str, Optional[dict], Optional[PatchEngine]]:
    """Process single file patch in memory, returning (patched_content, occurrences, warnings, suggestion, error_dict, engine)."""
    from .diagnostics import _handle_patch_file_value_error

    did_you_mean = bool(kwargs.get("did_you_mean", False))
    symbol_name = kwargs.get("symbol_name")
    allow_multiple = bool(kwargs.get("allow_multiple", False))
    line_filter = kwargs.get("line_filter")

    if patch_content is not None:
        return _apply_patch_content(file_content, target_file, patch_content, bypass_validation=bypass_validation)

    if replacements is not None:
        resolved_items = []
        for idx, r in enumerate(replacements):
            is_ins = "insert_content" in r or "insert_line" in r
            if is_ins:
                ins_content = r.get("insert_content")
                if ins_content == "":
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] specifies an empty insert_content."}, None
                if ins_content is None:
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] specifies insert_line but is missing insert_content."}, None
                if "search_content" in r or "replace_content" in r:
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] cannot combine insert_content with search_content or replace_content."}, None
                ins_line = r.get("insert_line")
                if ins_line is None:
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] specifies insert_content but is missing insert_line."}, None
                if "symbol_name" in r:
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] cannot combine insert_content with symbol_name."}, None

                resolved_items.append({
                    "r": r,
                    "is_insertion": True,
                    "insert_line": ins_line,
                    "insert_content": ins_content,
                    "auto_indent": r.get("auto_indent", True),
                    "start_line": ins_line or 1,
                    "end_line": ins_line or 1,
                })
            else:
                scope = r.get("symbol_scope", "boundary")
                sym_name = r.get("symbol_name")
                r_start = r.get("start_line")
                r_end = r.get("end_line")

                body_range = None
                resolved_start = r_start
                resolved_end = r_end

                if scope in ("full", "body"):
                    if not sym_name or "replace_content" not in r:
                        return "", 0, [], "", {"error": f"Error: replacements[{idx}] specifies symbol_scope '{scope}' but is missing symbol_name or replace_content."}, None
                else:
                    if "search_content" not in r or "replace_content" not in r:
                        return "", 0, [], "", {"error": f"Error: replacements[{idx}] is missing search_content or replace_content."}, None

                if sym_name:
                    sym_start, sym_end, sym_err, b_range = _resolve_ast_boundaries(
                        cwd, target_path, sym_name, storage_path, r_start, r_end, scope, file_content
                    )
                    if sym_err:
                        return "", 0, [], "", sym_err, None
                    resolved_start = sym_start
                    resolved_end = sym_end
                    body_range = b_range
                elif "search_content" in r and r["search_content"] in file_content:
                    s_text = r["search_content"]
                    s_char_idx = file_content.find(s_text)
                    match_start_l = file_content[:s_char_idx].count("\n") + 1
                    match_end_l = match_start_l + s_text.count("\n")
                    resolved_start = match_start_l
                    resolved_end = match_end_l

                resolved_items.append({
                    "r": r,
                    "is_insertion": False,
                    "scope": scope,
                    "symbol_name": sym_name,
                    "start_line": resolved_start or 1,
                    "end_line": resolved_end or len(file_content.split("\n")),
                    "body_range": body_range,
                })

        collision_err = check_replacement_collisions(resolved_items)
        if collision_err:
            return "", 0, [], "", collision_err, None

        sorted_resolved_items = sort_resolved_items_descending(resolved_items)

        def run_chain(contents: str, suggest_idx: Optional[int] = None) -> tuple[str, int, list[str], PatchEngine]:
            temp_content = contents
            occurrences_sum = 0
            last_linter_warnings = []
            all_warnings = []
            final_engine = None

            for idx, item in enumerate(sorted_resolved_items):
                r_engine = PatchEngine(temp_content, target_file, bypass_validation=bypass_validation)
                r = item["r"]
                scope = item.get("scope", "boundary")
                sym_name = item.get("symbol_name")
                is_suggest = (suggest_idx is not None and idx == suggest_idx)

                if item.get("is_insertion"):
                    temp_content, occurrences_cnt = r_engine.apply_line_insertion(
                        insert_line=item["insert_line"],
                        insert_content=item["insert_content"],
                        auto_indent=item["auto_indent"],
                        validate=(idx == len(sorted_resolved_items) - 1)
                    )
                elif scope in ("full", "body"):
                    b_range = item["body_range"]
                    start_line_val = item["start_line"]
                    end_line_val = item["end_line"]
                    start_col = 0
                    end_col = 0
                    is_expr = False

                    if scope == "body" and b_range is not None:
                        start_line_val = b_range.start_line
                        start_col = b_range.start_col
                        end_line_val = b_range.end_line
                        end_col = b_range.end_col
                        is_expr = b_range.is_expression

                    temp_content, occurrences_cnt = r_engine.apply_symbol_replacement(
                        replace_content=r["replace_content"],
                        start_line=start_line_val,
                        start_col=start_col,
                        end_line=end_line_val,
                        end_col=end_col,
                        symbol_scope=scope,
                        is_expression=is_expr,
                        validate=(idx == len(sorted_resolved_items) - 1),
                    )
                else:
                    sym_boundaries = None
                    if sym_name:
                        sym_boundaries = (item["start_line"], item["end_line"])

                    temp_content, occurrences_cnt = r_engine.apply_classic_patch(
                        search_content=r["search_content"],
                        replace_content=r["replace_content"],
                        allow_multiple=r.get("allow_multiple", allow_multiple),
                        start_line=r.get("start_line") or item.get("start_line"),
                        end_line=r.get("end_line") or item.get("end_line"),
                        symbol_boundaries=sym_boundaries,
                        symbol_name=sym_name,
                        line_filter=r.get("line_filter"),
                        did_you_mean=is_suggest,
                        validate=(idx == len(sorted_resolved_items) - 1)
                    )

                occurrences_sum += occurrences_cnt
                if getattr(r_engine, "insertion_warnings", None):
                    all_warnings.extend(r_engine.insertion_warnings)
                if getattr(r_engine, "symbol_warnings", None):
                    all_warnings.extend(r_engine.symbol_warnings)
                last_linter_warnings = list(all_warnings) + list(getattr(r_engine, "linter_warnings", []))
                final_engine = r_engine

            if len(sorted_resolved_items) > 1:
                from .symbol_checker import extract_declarations, detect_net_omitted_symbols
                deleted_symbols = set()
                for item in sorted_resolved_items:
                    r = item.get("r", {})
                    if "search_content" in r and r["search_content"]:
                        deleted_symbols.update(extract_declarations(r["search_content"], target_file))
                net_warnings = detect_net_omitted_symbols(
                    patched_content=temp_content,
                    deleted_symbols=deleted_symbols,
                    filename=target_file,
                )
                last_linter_warnings.extend(net_warnings)

            return temp_content, occurrences_sum, last_linter_warnings, final_engine

        try:
            patched_file, occurrences, last_warnings, engine = run_chain(file_content)
            filtered_warnings = ValidationService.filter_warnings(last_warnings)
            suggestion = _get_linter_suggestion(target_file) if filtered_warnings else ""
            return patched_file, occurrences, filtered_warnings, suggestion, None, engine
        except SyntaxValidationError as e:
            return "", 0, [], "", {
                "error": f"Syntax Error: {str(e)}",
                "filename": e.filename,
                "line": e.line,
                "column": e.column
            }, None
        except ValueError as e:
            return "", 0, [], "", _handle_patch_file_value_error(e, target_file), None

    symbol_scope = kwargs.get("symbol_scope", "boundary")
    insert_line = kwargs.get("insert_line")
    insert_content = kwargs.get("insert_content")
    auto_indent = bool(kwargs.get("auto_indent", True)) if kwargs.get("auto_indent") is not None else True

    if insert_content is not None or insert_line is not None:
        if insert_content == "":
            return "", 0, [], "", {"error": "Error: 'insert_content' cannot be empty."}, None
        if insert_content is None:
            return "", 0, [], "", {"error": "Error: 'insert_content' is required when specifying an insertion operation."}, None
        if insert_line is None:
            return "", 0, [], "", {"error": "Error: 'insert_line' is required when specifying an insertion operation."}, None
        if search_content is not None or replace_content is not None or patch_content is not None or start_line is not None or end_line is not None or symbol_name is not None:
            return "", 0, [], "", {"error": "Error: Cannot combine 'insert_content' with 'search_content', 'replace_content', 'patch_content', 'start_line', 'end_line', or 'symbol_name' in a single item."}, None

        engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
        try:
            patched_file, occurrences = engine.apply_line_insertion(
                insert_line=insert_line,
                insert_content=insert_content,
                auto_indent=auto_indent,
                validate=True,
            )
            linter_warnings = ValidationService.filter_warnings(
                list(getattr(engine, "insertion_warnings", [])) + list(getattr(engine, "linter_warnings", []))
            )
            suggestion = _get_linter_suggestion(target_file) if linter_warnings else ""
            return patched_file, occurrences, linter_warnings, suggestion, None, engine
        except SyntaxValidationError as e:
            return "", 0, [], "", {
                "error": f"Syntax Error: {str(e)}",
                "filename": e.filename,
                "line": e.line,
                "column": e.column
            }, None
        except ValueError as e:
            return "", 0, [], "", _handle_patch_file_value_error(e, target_file), None

    if symbol_scope in ("full", "body"):
        if not symbol_name or replace_content is None:
            return "", 0, [], "", {"error": "Error: Both symbol_name and replace_content must be provided when symbol_scope is 'full' or 'body'."}, None
    else:
        if search_content is None or replace_content is None:
            return "", 0, [], "", {"error": "Error: Either replacements, patch_content, OR both search_content and replace_content must be provided."}, None

    from .body_parser import MAX_LINES_FOR_TREESITTER, MAX_BYTES_FOR_TREESITTER
    file_lines_count = len(file_content.split("\n"))
    file_char_count = len(file_content)
    large_file = file_lines_count > MAX_LINES_FOR_TREESITTER or file_char_count > MAX_BYTES_FOR_TREESITTER

    resolved_start_line, resolved_end_line, err, body_range = _resolve_ast_boundaries(
        cwd, target_path, symbol_name, storage_path, start_line, end_line, symbol_scope, file_content
    )
    if err:
        return "", 0, [], "", err, None

    engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
    if large_file and symbol_scope == "body":
        engine.large_file_fallback = True

    try:
        if symbol_scope in ("full", "body"):
            start_l = resolved_start_line
            end_l = resolved_end_line
            start_c = 0
            end_c = 0
            is_expr = False

            if symbol_scope == "body" and body_range is not None:
                start_l = body_range.start_line
                start_c = body_range.start_col
                end_l = body_range.end_line
                end_c = body_range.end_col
                is_expr = body_range.is_expression

            patched_file, occurrences = engine.apply_symbol_replacement(
                replace_content=replace_content,
                start_line=start_l,
                start_col=start_c,
                end_line=end_l,
                end_col=end_c,
                symbol_scope=symbol_scope,
                is_expression=is_expr,
            )
        else:
            sym_boundaries = None
            if symbol_name:
                sym_boundaries = (resolved_start_line, resolved_end_line)

            patched_file, occurrences = engine.apply_classic_patch(
                search_content=search_content,
                replace_content=replace_content,
                allow_multiple=allow_multiple,
                start_line=start_line,
                end_line=end_line,
                symbol_boundaries=sym_boundaries,
                symbol_name=symbol_name,
                line_filter=line_filter,
                did_you_mean=did_you_mean,
                validate=True,
            )
        sym_warnings = getattr(engine, "symbol_warnings", [])
        linter_warnings = list(sym_warnings) + list(getattr(engine, "linter_warnings", []))
        filtered_warnings = ValidationService.filter_warnings(linter_warnings)
        suggestion = _get_linter_suggestion(target_file) if filtered_warnings else ""
        return patched_file, occurrences, filtered_warnings, suggestion, None, engine
    except SyntaxValidationError as e:
        return "", 0, [], "", {
            "error": f"Syntax Error: {str(e)}",
            "filename": e.filename,
            "line": e.line,
            "column": e.column
        }, None
    except ValueError as e:
        return "", 0, [], "", _handle_patch_file_value_error(e, target_file), None

