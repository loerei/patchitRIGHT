"""AST-bounded file editor (patch_file) utilizing workspace, engine, and transaction modules."""

import difflib
import hashlib
import os
from pathlib import Path
from typing import Optional, Union

from jcodemunch_mcp.storage import IndexStore
from .workspace import Workspace
from .engine import PatchEngine
from .transaction import FileTransaction
from .run_cache import get_cache
from .body_parser import BodyRange
from .validators import SyntaxValidationError

LINTER_WARNINGS_PREFIX = "\n*Linter Warnings:*\n"


def trigger_jcodemunch_sync(file_paths: Union[Path, list[Path]], storage_path: Optional[str] = None) -> None:
    """Trigger jcodemunch file index update using direct python import or subprocess fallback."""
    import os
    import threading

    # Read environment variable to check if sync is enabled
    enabled = os.environ.get("PATCHITRIGHT_SYNC_JCODEMUNCH", "").lower() in ("true", "1", "yes")
    if not enabled:
        return

    if isinstance(file_paths, Path):
        file_paths = [file_paths]

    def worker():
        for path in file_paths:
            try:
                abs_path = str(path.resolve())
                try:
                    # Attempt direct python import
                    from jcodemunch_mcp.tools.index_file import index_file as jm_index_file
                    jm_index_file(path=abs_path, use_ai_summaries=False, storage_path=storage_path)
                except ImportError:
                    # Fallback to subprocess
                    import subprocess
                    import sys
                    cmd = ["jcodemunch-mcp", "index-file", abs_path]
                    if storage_path:
                        cmd.extend(["--db", storage_path])

                    startupinfo = None
                    if sys.platform == "win32":
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                    subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        startupinfo=startupinfo,
                        shell=sys.platform == "win32"
                    )
            except Exception as e:
                import sys
                print(f"[PATCHITRIGHT] Warning: Failed to trigger jcodemunch sync for {path}: {e}", file=sys.stderr)

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception as e:
        import sys
        print(f"[PATCHITRIGHT] Warning: Failed to spawn jcodemunch sync thread: {e}", file=sys.stderr)


def _get_linter_suggestion(target_file: str) -> str:
    suffix = Path(target_file).suffix.lower()
    if suffix in (".js", ".ts", ".jsx", ".tsx", ".json"):
        return "You can run `npx --offline @biomejs/biome check --write` on this file to automatically fix lint/format warnings."
    elif suffix == ".py":
        return "You can run `ruff check --fix` on this file to automatically fix lint warnings."
    return ""


def _write_file_with_delay(path: Path, content: str, delay: float = 0.5) -> None:
    """Write content to path after a short delay on a background thread.

    This prevents process watchers from killing the MCP server before the JSON-RPC
    response has been sent.
    """
    import threading
    import time

    def worker():
        time.sleep(delay)
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            trigger_jcodemunch_sync(path)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception as e:
        import sys
        print(f"[PATCHITRIGHT] Warning: Failed to spawn jcodemunch sync thread: {e}", file=sys.stderr)


def _write_patched_file(target_path: Path, content: str) -> None:
    """Write patched content to target_path, delaying if it's a self-modification."""
    try:
        is_self_mod = target_path.resolve().is_relative_to(Path(__file__).parent.resolve())
    except Exception:
        is_self_mod = False

    if is_self_mod:
        _write_file_with_delay(target_path, content, delay=0.5)
    else:
        with open(target_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        trigger_jcodemunch_sync(target_path)


def _commit_transaction_with_delay(transaction: FileTransaction, modifications: dict[Path, str], delay: float = 0.5) -> None:
    """Commit transaction after a short delay on a background thread for self-modification.

    Pre-write cleanup of .patchitRIGHT/backups is executed before writing content
    so process hot-reloading restarting the MCP server does not leave dirty backups on disk
    that trigger run_startup_recovery() rollback loops.
    """
    import threading
    import time

    def worker():
        time.sleep(delay)
        try:
            # Clean up backups BEFORE committing to prevent recovery loops on dev reload
            transaction.cleanup()
            for target_path, content in modifications.items():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                trigger_jcodemunch_sync(target_path)
        except Exception as e:
            from .logger import log_step
            log_step(f"_commit_transaction_with_delay: background commit failed for {list(modifications.keys())}: {e}")

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass


def generate_diff(original: str, modified: str, filename: str) -> str:
    """Generate a unified diff representation of changes."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3
    )
    return "".join(diff)


def _resolve_ast_boundaries(
    cwd: Path,
    target_path: Path,
    symbol_name: Optional[str],
    storage_path: Optional[str],
    start_line: Optional[int],
    end_line: Optional[int],
    symbol_scope: str = "boundary",
    file_content: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], Optional[dict], Optional["BodyRange"]]:
    """Resolve start and end lines for AST symbolName boundary."""
    if not symbol_name:
        return start_line, end_line, None, None

    import re

    try:
        from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
        from jcodemunch_mcp.storage import IndexStore
        repo_res = resolve_repo_fn(str(target_path), storage_path)
        if not repo_res.get("found"):
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed. Call index_folder first to resolve symbols."}, None
        
        repo_id = repo_res.get("repo")
        store = IndexStore(base_path=storage_path)
        index = store.load_index(*repo_id.split("/", 1)) if repo_id and "/" in repo_id else None
        if not repo_res.get("found") or not index:
            if file_content is None and target_path.exists():
                with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                    file_content = f.read()
            if file_content:
                lines = file_content.split("\n")
                pattern = re.compile(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(symbol_name)}\b")
                found_line = None
                for idx, line in enumerate(lines, 1):
                    if pattern.search(line):
                        found_line = idx
                        break
                if found_line:
                    indent = len(lines[found_line - 1]) - len(lines[found_line - 1].lstrip())
                    end_l = len(lines)
                    for idx in range(found_line, len(lines)):
                        l = lines[idx]
                        if l.strip() and not l.strip().startswith("#"):
                            curr_indent = len(l) - len(l.lstrip())
                            if curr_indent <= indent and idx + 1 > found_line:
                                end_l = idx
                                break
                    body_range = None
                    try:
                        from .body_parser import get_body_range
                        body_range = get_body_range(file_content, str(target_path), found_line, end_l)
                    except Exception:
                        pass
                    return found_line, end_l, None, body_range
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed and symbol '{symbol_name}' could not be matched."}, None
            
        source_root = Path(repo_res.get("source_root") or index.source_root or cwd).resolve()
        try:
            rel_file_path = str(target_path.relative_to(source_root)).replace("\\", "/")
        except ValueError:
            rel_file_path = str(target_path.relative_to(cwd)).replace("\\", "/")
            
        matched_symbols = []
        for sym in index.symbols:
            if sym.get("name") == symbol_name and sym.get("file") == rel_file_path:
                matched_symbols.append(sym)
                
        if not matched_symbols:
            return None, None, {"error": f"Symbol '{symbol_name}' not found in file '{rel_file_path}'."}, None
            
        symbol = matched_symbols[0]
        sym_start, sym_end = symbol["line"], symbol["end_line"]

        if symbol_scope == "body":
            if file_content is None:
                with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                    file_content = f.read()
            from .body_parser import get_body_range
            body_range = get_body_range(file_content, str(target_path), sym_start, sym_end)
            return body_range.start_line, body_range.end_line, None, body_range

        return sym_start, sym_end, None, None
    except Exception as e:
        return None, None, {"error": f"Error resolving symbolName '{symbol_name}': {e}"}, None


def _read_file_and_check_filters(
    target_path: Path,
    cwd: Path,
    folder_filter: Optional[str],
    file_filter: Optional[str],
) -> tuple[str, bool, Optional[dict]]:
    """Verify filters and read original file content (returning file_content, file_exists, error_dict)."""
    if folder_filter:
        resolved_folder = (cwd / folder_filter).resolve()
        try:
            target_path.relative_to(resolved_folder)
        except ValueError:
            return "", False, {"error": f"Target file does not reside inside folder_filter '{folder_filter}'"}

    if file_filter:
        file_name = target_path.name
        if file_filter not in file_name:
            return "", False, {"error": f"Target file name '{file_name}' does not match file_filter '{file_filter}'"}

    if not target_path.exists():
        return "", False, None

    try:
        with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as f:
                        return f.read(), True, None
    except Exception as e:
        return "", True, {"error": f"Failed to read file: {e}"}


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
    did_you_mean = bool(kwargs.get("did_you_mean", False))
    symbol_name = kwargs.get("symbol_name")
    allow_multiple = bool(kwargs.get("allow_multiple", False))
    line_filter = kwargs.get("line_filter")

    # Handle Unified Diff patch format
    if patch_content is not None:
        engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
        try:
            patched_file = engine.apply_unified_patch(patch_content)
            linter_warnings = getattr(engine, "linter_warnings", [])
            suggestion = _get_linter_suggestion(target_file) if linter_warnings else ""
            return patched_file, 1, linter_warnings, suggestion, None, engine
        except SyntaxValidationError as e:
            return "", 0, [], "", {
                "error": f"Syntax Error: {str(e)}",
                "filename": e.filename,
                "line": e.line,
                "column": e.column
            }, None
        except ValueError as e:
            return "", 0, [], "", {"error": str(e)}, None

    # Handle Multiple Replacements
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
                sym_name = r.get("symbol_name")
                ins_pos = r.get("insert_position", "before")
                if ins_line is not None and sym_name is not None:
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] cannot specify both insert_line and symbol_name."}, None
                if ins_line is None and sym_name is None:
                    return "", 0, [], "", {"error": f"Error: replacements[{idx}] must specify either insert_line or symbol_name."}, None

                target_line = ins_line
                if sym_name:
                    sym_start, sym_end, sym_err, b_range = _resolve_ast_boundaries(
                        cwd, target_path, sym_name, storage_path, None, None, "boundary", file_content
                    )
                    if sym_err:
                        return "", 0, [], "", sym_err, None
                    if ins_pos == "before":
                        file_lines = file_content.split("\n")
                        actual_line = sym_start or 1
                        while actual_line > 1 and file_lines[actual_line - 2].strip().startswith("@"):
                            actual_line -= 1
                        target_line = actual_line
                    elif ins_pos == "after":
                        target_line = sym_end
                    elif ins_pos in ("start", "end"):
                        if b_range is None:
                            return "", 0, [], "", {"error": f"Error: Cannot resolve body range for symbol '{sym_name}'."}, None
                        target_line = b_range.start_line if ins_pos == "start" else b_range.end_line

                resolved_items.append({
                    "r": r,
                    "is_insertion": True,
                    "insert_line": target_line,
                    "insert_content": ins_content,
                    "insert_position": ins_pos,
                    "auto_indent": r.get("auto_indent", True),
                    "start_line": target_line or 1,
                    "end_line": target_line or 1,
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

        repl_items = [x for x in resolved_items if not x.get("is_insertion")]
        sorted_repl = sorted(repl_items, key=lambda x: x["start_line"])
        for i in range(len(sorted_repl) - 1):
            curr = sorted_repl[i]
            nxt = sorted_repl[i+1]
            if curr["end_line"] >= nxt["start_line"]:
                return "", 0, [], "", {"error": f"Error: Overlapping replacements detected between lines {curr['start_line']}-{curr['end_line']} and {nxt['start_line']}-{nxt['end_line']}."}, None

        # Check overlap of insertions strictly inside active replacement bodies
        ins_items = [x for x in resolved_items if x.get("is_insertion")]
        for ins in ins_items:
            ins_l = ins["insert_line"]
            for repl in repl_items:
                if repl["start_line"] < ins_l < repl["end_line"]:
                    return "", 0, [], "", {"error": f"Error: Cannot insert code inside an active replacement range (lines {repl['start_line']}-{repl['end_line']})."}, None

        # Sort descending by target line; tie-breaking: replacements before insertions (weight 1 > weight 0 with reverse=True)
        sorted_resolved_items = sorted(
            resolved_items,
            key=lambda x: (x["start_line"], 1 if not x.get("is_insertion") else 0),
            reverse=True
        )

        def run_chain(contents: str, suggest_idx: Optional[int] = None) -> tuple[str, int, list[str], PatchEngine]:
            temp_content = contents
            occurrences_sum = 0
            last_linter_warnings = []
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
                        insert_position=item["insert_position"],
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
                    )
                else:
                    sym_boundaries = None
                    if sym_name:
                        sym_boundaries = (item["start_line"], item["end_line"])

                    temp_content, occurrences_cnt = r_engine.apply_classic_patch(
                        search_content=r["search_content"],
                        replace_content=r["replace_content"],
                        allow_multiple=r.get("allow_multiple", allow_multiple),
                        start_line=r.get("start_line"),
                        end_line=r.get("end_line"),
                        symbol_boundaries=sym_boundaries,
                        symbol_name=sym_name,
                        line_filter=r.get("line_filter"),
                        did_you_mean=is_suggest,
                        validate=(idx == len(sorted_resolved_items) - 1)
                    )

                occurrences_sum += occurrences_cnt
                last_linter_warnings = getattr(r_engine, "linter_warnings", [])
                final_engine = r_engine

            return temp_content, occurrences_sum, last_linter_warnings, final_engine

        try:
            patched_file, occurrences, last_warnings, engine = run_chain(file_content)
            suggestion = _get_linter_suggestion(target_file) if last_warnings else ""
            return patched_file, occurrences, last_warnings, suggestion, None, engine
        except SyntaxValidationError as e:
            return "", 0, [], "", {
                "error": f"Syntax Error: {str(e)}",
                "filename": e.filename,
                "line": e.line,
                "column": e.column
            }, None
        except ValueError as e:
            return "", 0, [], "", _handle_patch_file_value_error(e, target_file), None

    # Classic search/replace or AST symbol replacement
    symbol_scope = kwargs.get("symbol_scope", "boundary")

    # Handle Line-Based Insertion mode
    insert_line = kwargs.get("insert_line")
    insert_content = kwargs.get("insert_content")
    insert_position = kwargs.get("insert_position", "before")
    auto_indent = bool(kwargs.get("auto_indent", True)) if kwargs.get("auto_indent") is not None else True

    if insert_content is not None or insert_line is not None:
        if insert_content == "":
            return "", 0, [], "", {"error": "Error: 'insert_content' cannot be empty."}, None
        if insert_content is None:
            return "", 0, [], "", {"error": "Error: 'insert_content' is required when specifying an insertion operation."}, None
        if search_content is not None or replace_content is not None or patch_content is not None or start_line is not None or end_line is not None:
            return "", 0, [], "", {"error": "Error: Cannot combine 'insert_content' with 'search_content', 'replace_content', 'patch_content', 'start_line', or 'end_line' in a single item."}, None
        if insert_line is not None and symbol_name is not None:
            return "", 0, [], "", {"error": "Error: Cannot specify both 'insert_line' and 'symbol_name' in a single insertion operation."}, None
        if insert_line is None and symbol_name is None:
            return "", 0, [], "", {"error": "Error: Either 'insert_line' or 'symbol_name' must be provided for insertion."}, None
        if insert_line is not None and insert_position in ("start", "end"):
            return "", 0, [], "", {"error": "Error: Positions 'start' and 'end' require a symbol_name."}, None

        target_insert_line = insert_line
        if symbol_name:
            sym_start, sym_end, sym_err, b_range = _resolve_ast_boundaries(
                cwd, target_path, symbol_name, storage_path, None, None, symbol_scope, file_content
            )
            if sym_err:
                return "", 0, [], "", sym_err, None
            if insert_position == "before":
                # Decorator detection
                file_lines = file_content.split("\n")
                actual_line = sym_start or 1
                while actual_line > 1 and file_lines[actual_line - 2].strip().startswith("@"):
                    actual_line -= 1
                target_insert_line = actual_line
            elif insert_position == "after":
                target_insert_line = sym_end
            elif insert_position in ("start", "end"):
                if b_range is None:
                    return "", 0, [], "", {"error": f"Error: Cannot resolve body range for symbol '{symbol_name}' to insert at '{insert_position}'."}, None
                if insert_position == "start":
                    target_insert_line = b_range.start_line
                else:
                    target_insert_line = b_range.end_line

        engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
        try:
            patched_file, occurrences = engine.apply_line_insertion(
                insert_line=target_insert_line,
                insert_content=insert_content,
                insert_position=insert_position if insert_line is not None else "before",
                auto_indent=auto_indent,
                validate=True,
            )
            linter_warnings = getattr(engine, "linter_warnings", [])
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
        linter_warnings = getattr(engine, "linter_warnings", [])
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


def patch_file(  # noqa: C901 # NOSONAR
    target_file: Optional[str] = None,
    search_content: Optional[str] = None,
    replace_content: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: bool = False,
    patch_content: Optional[str] = None,
    replacements: Optional[list[dict]] = None,
    files: Optional[list[dict]] = None,
    patches: Optional[list[dict]] = None,
    insert_line: Optional[int] = None,
    insert_content: Optional[str] = None,
    insert_position: Optional[str] = "before",
    auto_indent: bool = True,
    **kwargs,
) -> dict:
    """Perform a robust search-and-replace or apply a strict unified diff (Fuzz = 0) across single or multiple files."""
    files_param = files or patches
    symbol_name_arg = kwargs.get("symbol_name")
    single_file_args = [target_file, search_content, replace_content, patch_content, replacements, symbol_name_arg, insert_content, insert_line]
    has_single_file_args = any(arg is not None for arg in single_file_args)

    if files_param is not None and has_single_file_args:
        return _create_error_response("Error: Cannot provide both 'files' array and top-level single-file edit parameters.")

    if files_param is None and target_file is None:
        return _create_error_response("Error: Either 'target_file' or 'files' must be provided.")

    multi_file_mode = files_param is not None
    if multi_file_mode:
        file_items = files_param
    else:
        file_items = [{
            "target_file": target_file,
            "search_content": search_content,
            "replace_content": replace_content,
            "start_line": start_line,
            "end_line": end_line,
            "patch_content": patch_content,
            "replacements": replacements,
            "insert_line": insert_line,
            "insert_content": insert_content,
            "insert_position": insert_position,
            "auto_indent": auto_indent,
            **kwargs
        }]

    # Normalize file items
    for item in file_items:
        if "target_file" in item and item["target_file"]:
            item["target_file"] = os.path.normpath(item["target_file"])

    storage_path = kwargs.get("storage_path")
    bypass_validation = bool(kwargs.get("bypass_validation", False))
    cwd = Path.cwd().resolve()
    workspace = Workspace(cwd, storage_path)

    try:
        workspace_root = _resolve_workspace_root(file_items, workspace, cwd)
    except Exception as e:
        return _handle_patch_file_value_error(e, file_items[0].get("target_file", ""))

    # Duplicate target path check via resolved absolute paths
    resolved_paths: list[Path] = []
    for item in file_items:
        tf = item.get("target_file")
        if not tf:
            return _create_error_response("Error: 'target_file' is required for each item in 'files' array.")
        try:
            rp = workspace.resolve_safe_path(tf)
            resolved_paths.append(rp)
        except Exception as e:
            return _handle_patch_file_value_error(e, tf)

    norm_resolved = [os.path.normcase(str(rp)) for rp in resolved_paths]
    if len(norm_resolved) != len(set(norm_resolved)):
        return _create_error_response("Error: Duplicate resolved target_file paths found in 'files' batch. For multiple edits in the same file, use 'replacements' within a single item.")

    transaction = FileTransaction(workspace_root)
    processed_files: list[dict] = []
    modifications: dict[Path, str] = {}
    original_contents: dict[str, str] = {}

    for item, target_path in zip(file_items, resolved_paths):
        tf = item["target_file"]
        folder_filter = item.get("folder_filter", kwargs.get("folder_filter"))
        file_filter = item.get("file_filter", kwargs.get("file_filter"))

        file_content, file_exists, err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
        if err:
            return err

        # Missing file check
        item_patch_content = item.get("patch_content")
        is_creation_diff = item_patch_content is not None and ("old_start=0" in item_patch_content or "@@ -0,0" in item_patch_content or "@@ -0 " in item_patch_content)

        if not file_exists and not is_creation_diff:
            return _create_error_response(f"Target file not found at {target_path}. Use write_file to create new files or supply a valid patch_content creation diff.", target_file=tf)

        transaction.register_file(target_path)
        original_contents[str(target_path)] = file_content
        original_contents[tf] = file_content

        item_kwargs = {**kwargs, **item}
        for k in ["target_file", "target_path", "file_content", "bypass_validation", "cwd", "storage_path"]:
            item_kwargs.pop(k, None)

        patched_content, occurrences, warnings, suggestion, err, engine = _process_single_file_in_memory(
            target_file=tf,
            target_path=target_path,
            file_content=file_content,
            bypass_validation=bypass_validation,
            cwd=cwd,
            storage_path=storage_path,
            **item_kwargs
        )
        if err:
            if multi_file_mode:
                err_msg = err.get("error", str(err)) if isinstance(err, dict) else str(err)
                return _create_error_response(f"Validation failed for file {tf}: {err_msg}", target_file=tf, detail=err_msg)
            return _create_error_response(err, target_file=tf)

        modifications[target_path] = patched_content
        processed_files.append({
            "target_file": tf,
            "target_path": target_path,
            "patched_content": patched_content,
            "file_content": file_content,
            "file_exists": file_exists,
            "occurrences": occurrences,
            "warnings": warnings,
            "suggestion": suggestion,
            "engine": engine,
        })

    # Phase 2: Transactional Execution
    transaction.write_backups()
    ok, fail_path = transaction.check_optimistic_locking()
    if not ok:
        transaction.rollback()
        transaction.cleanup()
        raw_name = fail_path.name if fail_path else "file"
        return _create_error_response(f"Transaction Aborted (Optimistic Locking Conflict): File '{raw_name}' was modified on disk.")

    if dry_run:
        cache = get_cache()
        entries = [{"target_path": pf["target_path"], "patched_content": pf["patched_content"], "exists": pf.get("file_exists", True), "target_file": pf["target_file"], "occurrences": pf["occurrences"]} for pf in processed_files]
        run_id = cache.store(entries=entries, original_contents=original_contents)
        transaction.cleanup()

        if not multi_file_mode:
            pf = processed_files[0]
            eng = pf.get("engine")
            diff_text = generate_diff(pf["file_content"], pf["patched_content"], pf["target_file"])
            msg = f"```diff\n{diff_text}```\n"
            res = {
                "success": True,
                "dryRun": True,
                "target_file": pf["target_file"],
                "message": msg,
                "diff_content": diff_text,
                "occurrences": pf["occurrences"],
                "modified_files": None,
                "run_id": run_id,
                "expires_in": cache.get_ttl(),
            }
            return _attach_engine_flags(res, pf)

        msg = f"Dry-run preview for **{len(processed_files)}** file(s):\n"
        structured_warnings = []
        structured_suggestions = []
        modified_files = []
        total_occurrences = 0
        for pf in processed_files:
            diff_text = generate_diff(pf["file_content"], pf["patched_content"], pf["target_file"])
            msg += f"\n### `{pf['target_file']}`\n```diff\n{diff_text}```\n"
            if pf["warnings"]:
                structured_warnings.append({"file": pf["target_file"], "warnings": pf["warnings"]})
                structured_suggestions.append({"file": pf["target_file"], "suggestion": pf["suggestion"]})

            eng = pf.get("engine")
            relocated_range = None
            if eng and getattr(eng, "is_relocated", False):
                relocated_range = {
                    "start_line": getattr(eng, "relocated_start_line", 1),
                    "end_line": getattr(eng, "relocated_end_line", 1),
                }

            did_you_mean_info = None
            if eng and getattr(eng, "is_did_you_mean_applied", False):
                did_you_mean_info = {
                    "applied": True,
                    "similarity": getattr(eng, "s_ratio", 0.0),
                    "start_line": getattr(eng, "did_you_mean_start_line", None),
                    "end_line": getattr(eng, "did_you_mean_end_line", None),
                }

            modified_files.append({
                "target_file": pf["target_file"],
                "occurrences": pf["occurrences"],
                "diff_content": diff_text,
                "relocated_range": relocated_range,
                "did_you_mean_info": did_you_mean_info,
            })
            total_occurrences += pf["occurrences"]

            pf["relocated_range"] = relocated_range
            pf["did_you_mean_info"] = did_you_mean_info

        res = {
            "success": True,
            "dryRun": True,
            "target_file": processed_files[0]["target_file"] if processed_files else None,
            "message": msg,
            "diff_content": None,
            "occurrences": total_occurrences,
            "relocated_range": None,
            "did_you_mean_info": None,
            "modified_files": modified_files,
            "run_id": run_id,
            "expires_in": cache.get_ttl(),
        }
        if structured_warnings:
            res["warnings"] = structured_warnings
            res["suggestions"] = structured_suggestions
        return res

    commit_err = _commit_or_defer_transaction(transaction, modifications)
    if commit_err:
        return _create_error_response(commit_err)

    if not multi_file_mode:
        pf = processed_files[0]
        eng = pf.get("engine")
        is_did_you_mean = getattr(eng, "is_did_you_mean_applied", False)
        is_relocated = getattr(eng, "is_relocated", False)

        if is_did_you_mean:
            s_ratio = getattr(eng, "s_ratio", 0.0)
            ratio_pct = round(s_ratio * 100)
            msg = f"Patched 1 occurrence in `{pf['target_file']}` (applied via 'did_you_mean' fallback, similarity {ratio_pct}%).\n"
        elif is_relocated:
            r_start = getattr(eng, "relocated_start_line", 1)
            r_end = getattr(eng, "relocated_end_line", 1)
            msg = f"Patched {pf['occurrences']} occurrence(s) in `{pf['target_file']}` (relocated to lines {r_start}-{r_end}).\n"
        else:
            msg = f"Successfully patched `{pf['target_file']}`.\n"

        res = {
            "success": True,
            "dryRun": False,
            "target_file": pf["target_file"],
            "message": msg,
            "diff_content": None,
            "occurrences": pf["occurrences"],
            "modified_files": None,
        }
        return _attach_engine_flags(res, pf)

    msg = f"Successfully patched **{len(processed_files)}** file(s):\n"
    structured_warnings = []
    structured_suggestions = []
    modified_files = []
    total_occurrences = 0
    for pf in processed_files:
        msg += f"- `{pf['target_file']}` updated.\n"
        if pf["warnings"]:
            structured_warnings.append({"file": pf["target_file"], "warnings": pf["warnings"]})
            structured_suggestions.append({"file": pf["target_file"], "suggestion": pf["suggestion"]})

        eng = pf.get("engine")
        relocated_range = None
        if eng and getattr(eng, "is_relocated", False):
            relocated_range = {
                "start_line": getattr(eng, "relocated_start_line", 1),
                "end_line": getattr(eng, "relocated_end_line", 1),
            }

        did_you_mean_info = None
        if eng and getattr(eng, "is_did_you_mean_applied", False):
            did_you_mean_info = {
                "applied": True,
                "similarity": getattr(eng, "s_ratio", 0.0),
                "start_line": getattr(eng, "did_you_mean_start_line", None),
                "end_line": getattr(eng, "did_you_mean_end_line", None),
            }

        modified_files.append({
            "target_file": pf["target_file"],
            "occurrences": pf["occurrences"],
            "diff_content": None,
            "relocated_range": relocated_range,
            "did_you_mean_info": did_you_mean_info,
        })
        total_occurrences += pf["occurrences"]

    res = {
        "success": True,
        "dryRun": False,
        "target_file": processed_files[0]["target_file"] if processed_files else None,
        "message": msg,
        "diff_content": None,
        "occurrences": total_occurrences,
        "relocated_range": None,
        "did_you_mean_info": None,
        "modified_files": modified_files,
    }
    if structured_warnings:
        res["warnings"] = structured_warnings
        res["suggestions"] = structured_suggestions
    return res


def _write_file_dry_run(
    target_file: str,
    target_path: Path,
    code_content: str,
    original_content: str,
    file_exists: bool,
    linter_warnings: list[str],
) -> dict:
    """Handle the dry_run=True logic for write_file."""
    if file_exists:
        diff_text = generate_diff(original_content, code_content, target_file)
        output = f"```diff\n{diff_text}```\n"
    else:
        diff_text = generate_diff("", code_content, target_file)
        output = f"Preview of creating new file `{target_file}`:\n```\n{code_content}\n```\n"
    
    cache = get_cache()
    run_id = cache.store(
        entries=[{
            "target_path": target_path,
            "patched_content": code_content,
            "exists": file_exists,
            "target_file": target_file,
            "occurrences": 1,
            "relocated_range": None,
            "did_you_mean_info": None,
        }],
        original_contents={str(target_path): original_content, target_file: original_content},
    )
    res = {
        "success": True,
        "dryRun": True,
        "target_file": target_file,
        "message": output,
        "diff_content": diff_text,
        "occurrences": 1,
        "relocated_range": None,
        "did_you_mean_info": None,
        "modified_files": None,
        "run_id": run_id,
        "expires_in": cache.get_ttl(),
    }
    if linter_warnings:
        res["warnings"] = linter_warnings
        res["suggestion"] = _get_linter_suggestion(target_file)
    return res


def write_file(
    target_file: str,
    code_content: str,
    allow_overwrite: bool = False,
    dry_run: bool = False,
    **kwargs,
) -> dict:
    """Create or overwrite a file with syntax validation and linting."""
    storage_path = kwargs.get("storage_path")
    try:
        target_file = os.path.normpath(target_file)
        cwd = Path.cwd().resolve()
        workspace = Workspace(cwd, storage_path)
        target_path = workspace.resolve_safe_path(target_file)

        # File existence check
        file_exists = target_path.exists()
        original_content = ""
        if file_exists:
            if not allow_overwrite:
                return _create_error_response(
                    f"File already exists at '{target_file}'. To overwrite it, set 'allow_overwrite' to true.",
                    target_file=target_file,
                )
            try:
                with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as f:
                    original_content = f.read()
            except Exception as e:
                return _create_error_response(f"Failed to read existing file: {e}", target_file=target_file)

        # Run validation and linting
        linter_warnings = []
        bypass_validation = bool(kwargs.get("bypass_validation", False))
        if not bypass_validation:
            from .validators import ValidationService
            validator = ValidationService()
            validator.validate_file(target_file, code_content, original_content)
            linter_warnings = validator.lint_file(target_file, code_content)

        if dry_run:
            return _write_file_dry_run(
                target_file=target_file,
                target_path=target_path,
                code_content=code_content,
                original_content=original_content,
                file_exists=file_exists,
                linter_warnings=linter_warnings
            )

        # Ensure parent directory exists before writing
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Actual write
        try:
            _write_patched_file(target_path, code_content)
        except Exception as e:
            return _create_error_response(f"Failed to write file: {e}", target_file=target_file)

        output = f"Successfully overwritten `{target_file}`.\n" if file_exists else f"Successfully created `{target_file}`.\n"
        res = {
            "success": True,
            "dryRun": False,
            "target_file": target_file,
            "message": output,
            "diff_content": None,
            "occurrences": 1,
            "relocated_range": None,
            "did_you_mean_info": None,
            "modified_files": None,
        }
        if linter_warnings:
            res["warnings"] = linter_warnings
            res["suggestion"] = _get_linter_suggestion(target_file)
        return res

    except SyntaxValidationError as e:
        return _create_error_response(
            f"Syntax Error: {str(e)}",
            target_file=target_file,
            filename=e.filename,
            line=e.line,
            column=e.column
        )
    except ValueError as e:
        return _handle_patch_file_value_error(e, target_file)


def _apply_patch_content(
    engine: PatchEngine,
    patch_content: str,
    dry_run: bool,
    target_file: str,
    target_path: Path,
    file_content: str,
) -> dict:
    try:
        patched_file = engine.apply_unified_patch(patch_content)
    except SyntaxValidationError as e:
        return {
            "error": f"Syntax Error: {str(e)}",
            "filename": e.filename,
            "line": e.line,
            "column": e.column
        }
    except ValueError as e:
        return {"error": str(e)}
        
    linter_warnings = getattr(engine, "linter_warnings", [])
    if dry_run:
        diff_text = generate_diff(file_content, patched_file, target_file)
        output = f"```diff\n{diff_text}```\n"
        output += f"- Target file: `{target_file}`\n"
        output += "- Format: Unified Diff (Strict Fuzz = 0)\n"
        cache = get_cache()
        run_id = cache.store(
            entries=[{"target_path": target_path, "patched_content": patched_file}],
            original_contents={str(target_path): file_content, target_file: file_content},
        )
        res = {
            "success": True,
            "dryRun": True,
            "message": output,
            "occurrences": 1,
            "run_id": run_id,
            "expires_in": cache.get_ttl(),
        }
        if linter_warnings:
            res["warnings"] = linter_warnings
            res["suggestion"] = _get_linter_suggestion(target_file)
        return res
        
    try:
        _write_patched_file(target_path, patched_file)
    except Exception as e:
        return {"error": f"Failed to write patched file: {e}"}
        
    output = f"- Target file: `{target_file}`\n"
    output += "- Format: Unified Diff (Strict Fuzz = 0) applied successfully\n"
    res = {
        "success": True,
        "dryRun": False,
        "message": output,
        "occurrences": 1,
    }
    if linter_warnings:
        res["warnings"] = linter_warnings
        res["suggestion"] = _get_linter_suggestion(target_file)
    return res


def _apply_classic_replacement(  # NOSONAR
    dry_run: bool,
    file_content: str,
    patched_file: str,
    target_file: str,
    target_path: Path,
    occurrences: int,
    symbol_name: Optional[str],
    resolved_start_line: Optional[int],
    resolved_end_line: Optional[int],
    start_line: Optional[int],
    end_line: Optional[int],
    engine: PatchEngine,
) -> dict:
    is_did_you_mean_applied = getattr(engine, "is_did_you_mean_applied", False)
    is_relocated = getattr(engine, "is_relocated", False)
    s_ratio = getattr(engine, "s_ratio", 0.0)
    ratio_pct = round(s_ratio * 100)
    if is_did_you_mean_applied:
        resolved_start_line = engine.did_you_mean_start_line
        resolved_end_line = engine.did_you_mean_end_line
    elif is_relocated:
        resolved_start_line = engine.relocated_start_line
        resolved_end_line = engine.relocated_end_line

    linter_warnings = getattr(engine, "linter_warnings", [])
    indentation_adjusted = getattr(engine, "indentation_adjusted", False)
    indent_delta = getattr(engine, "indent_delta", "")
    newline_padded = getattr(engine, "newline_padded", False)
    large_file_fallback = getattr(engine, "large_file_fallback", False)

    if dry_run:
        diff_text = generate_diff(file_content, patched_file, target_file)
        output = f"```diff\n{diff_text}```\n"
        output += f"- Target file: `{target_file}`\n"
        if is_did_you_mean_applied:
            output += "- Match occurrences inside scope: **1** (applied via 'did_you_mean' fallback)\n"
        else:
            output += f"- Match occurrences inside scope: **{occurrences}**\n"
        if symbol_name:
            output += f"- Scope: AST symbol `{symbol_name}` (lines {resolved_start_line}-{resolved_end_line})\n"
        elif start_line or end_line or is_did_you_mean_applied or is_relocated:
            start_disp = resolved_start_line if resolved_start_line is not None else 1
            end_disp = resolved_end_line if resolved_end_line is not None else len(engine.file_lines)
            output += f"- Scope: Line range {start_disp}-{end_disp}\n"
        if is_did_you_mean_applied:
            output += "*Note:* Exact search content not found, but closest match (similarity {}%) was matched via 'did_you_mean' flag.\n".format(ratio_pct)
        elif is_relocated:
            output += f"*Note:* Search content was relocated from the specified range to lines {resolved_start_line}-{resolved_end_line} (exact unique match found).\n"
        cache = get_cache()
        run_id = cache.store(
            entries=[{"target_path": target_path, "patched_content": patched_file}],
            original_contents={str(target_path): file_content, target_file: file_content},
        )
        res = {
            "success": True,
            "dryRun": True,
            "message": output,
            "occurrences": occurrences,
            "run_id": run_id,
            "expires_in": cache.get_ttl(),
        }
        if indentation_adjusted:
            res["indentation_adjusted"] = True
            res["indent_delta"] = indent_delta
        if newline_padded:
            res["newline_padded"] = True
        if large_file_fallback:
            res["large_file_fallback"] = True
        if linter_warnings:
            res["warnings"] = linter_warnings
            res["suggestion"] = _get_linter_suggestion(target_file)
        return res

    try:
        _write_patched_file(target_path, patched_file)
    except Exception as e:
        return {"error": f"Failed to write patched file: {e}"}

    output = f"- Target file: `{target_file}`\n"
    if is_did_you_mean_applied:
        output += "- Replaced occurrences: **1** (applied via 'did_you_mean' fallback)\n"
    else:
        output += f"- Replaced occurrences: **{occurrences}**\n"
    if symbol_name:
        output += f"- Scope: AST symbol `{symbol_name}` (lines {resolved_start_line}-{resolved_end_line})\n"
    elif start_line or end_line or is_did_you_mean_applied or is_relocated:
        start_disp = resolved_start_line if resolved_start_line is not None else 1
        end_disp = resolved_end_line if resolved_end_line is not None else len(engine.file_lines)
        output += f"- Scope: Line range {start_disp}-{end_disp}\n"
    if is_did_you_mean_applied:
        output += "*Note:* Exact search content not found, but closest match (similarity {}%) was matched via 'did_you_mean' flag.\n".format(ratio_pct)
    elif is_relocated:
        output += f"*Note:* Search content was relocated from the specified range to lines {resolved_start_line}-{resolved_end_line} (exact unique match found).\n"
    elif occurrences > 1:
        output += f"*Warning:* Replaced {occurrences} identical occurrences.\n"
    res = {
        "success": True,
        "dryRun": False,
        "message": output,
        "occurrences": occurrences,
    }
    if indentation_adjusted:
        res["indentation_adjusted"] = True
        res["indent_delta"] = indent_delta
    if newline_padded:
        res["newline_padded"] = True
    if large_file_fallback:
        res["large_file_fallback"] = True
    if linter_warnings:
        res["warnings"] = linter_warnings
        res["suggestion"] = _get_linter_suggestion(target_file)
    return res


def _create_error_response(
    error_msg: Union[str, dict],
    target_file: Optional[str] = None,
    detail: Optional[str] = None,
    **extra_fields,
) -> dict:
    """Format standardized error response dictionary with consistent schema fallbacks."""
    if isinstance(error_msg, dict):
        base_err = dict(error_msg)
        tf = base_err.get("target_file", target_file)
    else:
        base_err = {"error": str(error_msg)}
        tf = target_file
        if detail:
            base_err["detail"] = detail

    base_err.update(extra_fields)
    base_err.setdefault("target_file", tf)
    base_err.setdefault("diff_content", None)
    base_err.setdefault("relocated_range", None)
    base_err.setdefault("did_you_mean_info", None)
    base_err.setdefault("modified_files", None)
    return base_err


def _attach_engine_flags(res: dict, pf: dict) -> dict:
    """Attach linter warnings, suggestions, and engine execution flags to response dictionary."""
    if pf.get("warnings"):
        res["warnings"] = pf["warnings"]
        res["suggestion"] = pf["suggestion"]
    eng = pf.get("engine")
    if eng:
        if getattr(eng, "indentation_adjusted", False):
            res["indentation_adjusted"] = True
            res["indent_delta"] = getattr(eng, "indent_delta", "")
        if getattr(eng, "newline_padded", False):
            res["newline_padded"] = True
        if getattr(eng, "large_file_fallback", False):
            res["large_file_fallback"] = True
        if getattr(eng, "is_relocated", False):
            res["is_relocated"] = True
            res["relocated_range"] = {
                "start_line": getattr(eng, "relocated_start_line", 1),
                "end_line": getattr(eng, "relocated_end_line", 1),
            }
        else:
            res.setdefault("relocated_range", None)

        if getattr(eng, "is_did_you_mean_applied", False):
            res["is_did_you_mean_applied"] = True
            res["did_you_mean_info"] = {
                "applied": True,
                "similarity": getattr(eng, "s_ratio", 0.0),
                "start_line": getattr(eng, "did_you_mean_start_line", None),
                "end_line": getattr(eng, "did_you_mean_end_line", None),
            }
        else:
            res.setdefault("did_you_mean_info", None)
    else:
        res.setdefault("relocated_range", None)
        res.setdefault("did_you_mean_info", None)
    return res


def _commit_or_defer_transaction(transaction: FileTransaction, modifications: dict[Path, str]) -> Optional[dict]:
    """Commit transaction atomically or schedule deferred commit for self-modifications."""
    is_self_mod = False
    try:
        self_dir = Path(__file__).parent.resolve()
        is_self_mod = any(p.resolve().is_relative_to(self_dir) for p in modifications.keys())
    except Exception:
        is_self_mod = False

    if is_self_mod:
        _commit_transaction_with_delay(transaction, modifications, delay=0.5)
    else:
        try:
            transaction.commit(modifications)
            transaction.cleanup()
            for p in modifications.keys():
                trigger_jcodemunch_sync(p)
        except Exception as e:
            return {"error": f"Failed to commit transaction: {e}"}
    return None


def _handle_patch_file_value_error(e: ValueError, target_file: str) -> dict:
    if str(e) == "fatal_context_mismatch":
        cwd = Path.cwd().resolve()
        return _create_error_response(
            "fatal_context_mismatch",
            target_file=target_file,
            detail=(
                f"[FATAL CONTEXT MISMATCH]\n"
                f"Relative path '{target_file}' resolves outside the active MCP workspace '{cwd}'.\n\n"
                "Relative paths are restricted to the active workspace to prevent cross-repo drift.\n"
                "To fix:\n"
                "1. Use an absolute path to target a file outside the current workspace.\n"
                "2. Or ensure the terminal shell is CD'ed to the correct repository.\n"
            ),
        )
    res = _create_error_response(str(e), target_file=target_file)
    if getattr(e, "run_id", None):
        res["run_id"] = getattr(e, "run_id")
        res["expires_in"] = get_cache().get_ttl()
        res["message"] = str(e)
    return res


def run_startup_recovery(workspace_root: Optional[Path] = None, workspace_path: Optional[Path] = None) -> None:
    ws = workspace_root or workspace_path or Path.cwd().resolve()
    FileTransaction.run_startup_recovery(ws)


def _verify_dry_run_hashes(files: list[dict]) -> Optional[dict]:
    """Verify that all target files are unchanged since the dry-run."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    for f in files:
        target_path: Path = f["target_path"]
        original_hash: str = f["original_hash"]
        expected_exists: Optional[bool] = f.get("exists")
        if not target_path.exists():
            if expected_exists is False or (expected_exists is None and original_hash == empty_hash):
                # File did not exist and still does not exist, which is expected for new file creation
                continue
            return {"error": f"File '{target_path.name}' does not exist but was expected to exist based on dry-run."}
        if expected_exists is False:
            return {"error": f"File '{target_path.name}' was created after dry-run (was expected to not exist)."}
        try:
            with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as file_handle:
                current_text = file_handle.read()
        except Exception as e:
            return {"error": f"Cannot read '{target_path}' for hash check: {e}"}
        # Normalize newlines to LF for robust hash comparison
        norm_current = current_text.replace("\r\n", "\n").replace("\r", "")
        current_hash = hashlib.sha256(norm_current.encode()).hexdigest()
        if current_hash != original_hash:
            return {
                "error": (
                    f"File '{target_path.name}' was modified after the dry-run "
                    "(hash mismatch). Re-run with dry_run=true to preview the updated diff."
                )
            }
    return None


def apply_last_dry_run(run_id: str) -> dict:
    """Commit the patch cached under *run_id* from a previous dry-run call.

    No payload resend required — the caller only supplies the run_id.

    Guards:
    - Returns an error dict if run_id is unknown or has expired (TTL).
    - Returns an error dict if any target file has changed since the dry-run
      (hash mismatch), leaving all files untouched.
    """
    cache = get_cache()
    entry = cache.consume(run_id)

    if entry is None:
        return _create_error_response(f"run_id '{run_id}' not found or expired. Re-run with dry_run=true to get a fresh run_id.")

    files = entry["files"]

    # Hash guard: verify all files are unchanged before writing any
    error_response = _verify_dry_run_hashes(files)
    if error_response:
        return _create_error_response(error_response)

    # Transactional execution
    file_items = [{"target_file": str(f["target_path"])} for f in files]
    cwd = Path.cwd().resolve()
    workspace = Workspace(cwd)
    workspace_root = _resolve_workspace_root(file_items, workspace, cwd)
    
    transaction = FileTransaction(workspace_root)
    modifications: dict[Path, str] = {}
    for f in files:
        target_path: Path = f["target_path"]
        patched_content: str = f["patched_content"]
        transaction.register_file(target_path)
        modifications[target_path] = patched_content

    transaction.write_backups()
    ok, fail_path = transaction.check_optimistic_locking()
    if not ok:
        transaction.rollback()
        transaction.cleanup()
        return _create_error_response(f"Optimistic locking failed for '{fail_path.name if fail_path else 'file'}'. File was modified on disk.")

    commit_err = _commit_or_defer_transaction(transaction, modifications)
    if commit_err:
        return _create_error_response(commit_err)

    if len(files) == 1:
        f = files[0]
        tf = f.get("target_file") or str(f["target_path"])
        occ = f.get("occurrences", 1)
        rel_range = f.get("relocated_range")
        dym_info = f.get("did_you_mean_info")
        res = {
            "success": True,
            "dryRun": False,
            "target_file": tf,
            "message": f"Applied cached patch (run_id={run_id}) for `{tf}`.\n",
            "diff_content": None,
            "occurrences": occ,
            "relocated_range": rel_range,
            "did_you_mean_info": dym_info,
            "modified_files": None,
        }
    else:
        tf_primary = files[0].get("target_file") or str(files[0]["target_path"]) if files else None
        modified_files = []
        tot_occ = 0
        output = f"Applied cached patch (run_id={run_id}). Wrote **{len(files)}** file(s).\n"
        for f in files:
            tf = f.get("target_file") or str(f["target_path"])
            occ = f.get("occurrences", 1)
            output += f"- `{tf}` updated.\n"
            modified_files.append({
                "target_file": tf,
                "occurrences": occ,
                "diff_content": None,
                "relocated_range": f.get("relocated_range"),
                "did_you_mean_info": f.get("did_you_mean_info"),
            })
            tot_occ += occ
        res = {
            "success": True,
            "dryRun": False,
            "target_file": tf_primary,
            "message": output,
            "diff_content": None,
            "occurrences": tot_occ,
            "relocated_range": None,
            "did_you_mean_info": None,
            "modified_files": modified_files,
        }
    return res


def batch_patch_files(
    patches: list[dict],
    dry_run: bool = False,
    storage_path: Optional[str] = None,
    **kwargs
) -> dict:
    """Backward-compatible wrapper delegating batch_patch_files to patch_file(files=patches)."""
    return patch_file(files=patches, dry_run=dry_run, storage_path=storage_path, **kwargs)


def _resolve_workspace_root(patches: list[dict], workspace: Workspace, cwd: Path) -> Path:
    first_target = patches[0].get("target_file") if patches else None
    if first_target:
        try:
            resolved_path = workspace.resolve_safe_path(first_target)
            return workspace.find_workspace_root(resolved_path)
        except Exception:
            return cwd
    return cwd


def _process_patches_list(
    patches: list[dict],
    workspace: Workspace,
    transaction: FileTransaction,
    processed_patches: list[dict],
    bypass_validation: bool = False,
) -> Optional[dict]:
    for p in patches:
        raw_target = p.get("target_file")
        patch_content = p.get("patch_content")
        if not raw_target or not patch_content:
            return {"error": "Error: Each patch in patches array must have target_file and patch_content."}
            
        target_path = workspace.resolve_safe_path(raw_target)
        if not target_path.exists():
            return {"error": f"Error: Target file not found at {raw_target}."}
            
        original_content = transaction.register_file(target_path)
        
        engine = PatchEngine(original_content, raw_target, bypass_validation=bypass_validation)
        try:
            patched_content = engine.apply_unified_patch(patch_content)
        except ValueError as err:
            return {
                "error": f"Validation failed for file {raw_target}: {str(err)}",
                "detail": str(err)
            }
            
        processed_patches.append({
            "target_path": target_path,
            "raw_target": raw_target,
            "original_content": original_content,
            "patched_content": patched_content
        })
    return None


def _handle_batch_value_error(e: ValueError) -> dict:
    if str(e) == "fatal_context_mismatch":
        return {
            "error": "fatal_context_mismatch",
            "detail": "[FATAL CONTEXT MISMATCH] File resolves outside active workspace."
        }
    return {"error": str(e)}


def _apply_batch_dry_run(processed_patches: list[dict]) -> dict:
    outputs = []
    cache_entries = []
    original_contents: dict[str, str] = {}
    for item in processed_patches:
        diff_text = generate_diff(item["original_content"], item["patched_content"], item["raw_target"])
        outputs.append(f"```diff\n{diff_text}```\n- Target file: `{item['raw_target']}`")
        cache_entries.append({
            "target_path": item["target_path"],
            "patched_content": item["patched_content"],
        })
        original_contents[str(item["target_path"])] = item["original_content"]
        original_contents[item["raw_target"]] = item["original_content"]
    cache = get_cache()
    run_id = cache.store(entries=cache_entries, original_contents=original_contents)
    return {
        "success": True,
        "dryRun": True,
        "message": "\n\n".join(outputs),
        "run_id": run_id,
        "expires_in": cache.get_ttl(),
    }


def _commit_batch_transaction(transaction: FileTransaction, processed_patches: list[dict]) -> dict:
    # Backup Phase
    try:
        transaction.write_backups()
    except Exception as e:
        transaction.cleanup()
        return {"error": f"Failed to create backup files during transaction: {e}"}
        
    # Optimistic Locking Check
    ok, conflict_path = transaction.check_optimistic_locking()
    if not ok:
        transaction.cleanup()
        raw_name = ""
        for item in processed_patches:
            if item["target_path"] == conflict_path:
                raw_name = item["raw_target"]
                break
        if not raw_name and conflict_path:
            raw_name = conflict_path.name
        return {
            "error": (
                f"Transaction Aborted (Optimistic Locking Conflict): "
                f"File '{raw_name}' was modified by an external process during the transaction."
            )
        }
        
    # Commit Phase
    modifications = {item["target_path"]: item["patched_content"] for item in processed_patches}
    try:
        transaction.commit(modifications)
    except Exception as e:
        transaction.cleanup()
        return {"error": f"Commit failed during write of transaction: {e}. Rolled back transaction safely."}
        
    # Clean up backups on success
    transaction.cleanup()

    # Trigger jcodemunch sync for all patched files
    trigger_jcodemunch_sync(list(modifications.keys()))
            
    output = f"Transaction applied successfully. Patched **{len(processed_patches)}** files.\n"
    for item in processed_patches:
        output += f"- `{item['raw_target']}` updated successfully.\n"
        
    return {
        "success": True,
        "dryRun": False,
        "message": output
    }
