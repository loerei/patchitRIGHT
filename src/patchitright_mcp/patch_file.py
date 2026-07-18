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

    try:
        from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
        repo_res = resolve_repo_fn(str(target_path), storage_path)
        if not repo_res.get("found"):
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed. Call index_folder first to resolve symbols."}, None
        
        repo_id = repo_res["repo"]
        owner, name = repo_id.split("/", 1)
        
        store = IndexStore(base_path=storage_path)
        index = store.load_index(owner, name)
        if not index:
            return None, None, {"error": f"Index for '{repo_id}' could not be loaded."}, None
            
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
            from .body_parser import get_body_range, BodyRange
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
) -> tuple[Optional[str], Optional[dict]]:
    """Verify filters and read the original file content."""
    if folder_filter:
        resolved_folder = (cwd / folder_filter).resolve()
        try:
            target_path.relative_to(resolved_folder)
        except ValueError:
            return None, {"error": f"Target file does not reside inside folder_filter '{folder_filter}'"}

    if file_filter:
        file_name = target_path.name
        if file_filter not in file_name:
            return None, {"error": f"Target file name '{file_name}' does not match file_filter '{file_filter}'"}

    if not target_path.exists():
        return None, {"error": f"Target file not found at {target_path}"}

    try:
        with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as f:
            return f.read(), None
    except Exception as e:
        return None, {"error": f"Failed to read file: {e}"}


def patch_file(  # noqa: C901 # NOSONAR
    target_file: str,
    search_content: Optional[str] = None,
    replace_content: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: bool = False,
    patch_content: Optional[str] = None,
    replacements: Optional[list[dict]] = None,
    **kwargs,
) -> dict:
    """Perform a robust search-and-replace or apply a strict unified diff (Fuzz = 0)."""
    did_you_mean = bool(kwargs.get("did_you_mean", False))
    bypass_validation = bool(kwargs.get("bypass_validation", False))
    storage_path = kwargs.get("storage_path")
    folder_filter = kwargs.get("folder_filter")
    file_filter = kwargs.get("file_filter")
    symbol_name = kwargs.get("symbol_name")
    allow_multiple = bool(kwargs.get("allow_multiple", False))
    line_filter = kwargs.get("line_filter")
    try:
        target_file = os.path.normpath(target_file)
        cwd = Path.cwd().resolve()
        workspace = Workspace(cwd, storage_path)
        
        # Safe path resolution and workspace mismatch check
        target_path = workspace.resolve_safe_path(target_file)
        
        # Read file and verify file/folder filters
        file_content, err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
        if err:
            return err

        # Handle Unified Diff patch format
        if patch_content is not None:
            engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
            return _apply_patch_content(
                engine, patch_content, dry_run, target_file, target_path, file_content
            )

        # Handle Multiple Replacements (Multi-patch)
        if replacements is not None:
            # Upfront resolution and validation of all replacements
            resolved_items = []
            for idx, r in enumerate(replacements):
                scope = r.get("symbol_scope", "boundary")
                sym_name = r.get("symbol_name")
                r_start = r.get("start_line")
                r_end = r.get("end_line")

                body_range = None
                resolved_start = r_start
                resolved_end = r_end

                if scope in ("full", "body"):
                    if not sym_name or "replace_content" not in r:
                        return {"error": f"Error: replacements[{idx}] specifies symbol_scope '{scope}' but is missing symbol_name or replace_content."}
                else:
                    if "search_content" not in r or "replace_content" not in r:
                        return {"error": f"Error: replacements[{idx}] is missing search_content or replace_content."}

                if sym_name:
                    sym_start, sym_end, sym_err, b_range = _resolve_ast_boundaries(
                        cwd, target_path, sym_name, storage_path, r_start, r_end, scope, file_content
                    )
                    if sym_err:
                        return sym_err
                    resolved_start = sym_start
                    resolved_end = sym_end
                    body_range = b_range
                
                resolved_items.append({
                    "r": r,
                    "scope": scope,
                    "symbol_name": sym_name,
                    "start_line": resolved_start or 1,
                    "end_line": resolved_end or len(file_content.split("\n")),
                    "body_range": body_range,
                })

            # Check overlap of resolved items
            sorted_by_start = sorted(resolved_items, key=lambda x: x["start_line"])
            for i in range(len(sorted_by_start) - 1):
                curr = sorted_by_start[i]
                nxt = sorted_by_start[i+1]
                if curr["end_line"] >= nxt["start_line"]:
                    return {"error": f"Error: Overlapping replacements detected between lines {curr['start_line']}-{curr['end_line']} and {nxt['start_line']}-{nxt['end_line']}."}

            # Sort bottom-up based on start_line (descending) to prevent line-drift
            sorted_resolved_items = sorted(resolved_items, key=lambda x: x["start_line"], reverse=True)

            # Helper to run the chain of replacements
            def run_chain(contents: str, suggest_idx: Optional[int] = None) -> tuple[str, int, list[str], PatchEngine]:
                temp_content = contents
                occurrences_sum = 0
                last_linter_warnings = []
                final_engine = None

                # Keep track of aggregated metadata across chain
                any_adjusted = False
                all_deltas = []
                any_padded = False
                any_large_fallback = False

                for idx, item in enumerate(sorted_resolved_items):
                    r_engine = PatchEngine(temp_content, target_file, bypass_validation=bypass_validation)
                    r = item["r"]
                    scope = item["scope"]
                    sym_name = item["symbol_name"]
                    is_suggest = (suggest_idx is not None and idx == suggest_idx)

                    # Check for large file fallback
                    large_file = len(temp_content.split("\n")) > 50_000 or len(temp_content.encode("utf-8")) > 5_000_000
                    if large_file and scope == "body":
                        any_large_fallback = True

                    if scope in ("full", "body"):
                        b_range = item["body_range"]
                        start_line = item["start_line"]
                        end_line = item["end_line"]
                        start_col = 0
                        end_col = 0
                        is_expr = False
                        
                        if scope == "body" and b_range is not None:
                            start_line = b_range.start_line
                            start_col = b_range.start_col
                            end_line = b_range.end_line
                            end_col = b_range.end_col
                            is_expr = b_range.is_expression
                        
                        temp_content, occ = r_engine.apply_symbol_replacement(
                            replace_content=r["replace_content"],
                            start_line=start_line,
                            start_col=start_col,
                            end_line=end_line,
                            end_col=end_col,
                            symbol_scope=scope,
                            is_expression=is_expr,
                        )
                    else:
                        sym_boundaries = None
                        if sym_name:
                            sym_boundaries = (item["start_line"], item["end_line"])
                        
                        temp_content, occ = r_engine.apply_classic_patch(
                            search_content=r["search_content"],
                            replace_content=r["replace_content"],
                            allow_multiple=r.get("allow_multiple", allow_multiple),
                            start_line=r.get("start_line"),
                            end_line=r.get("end_line"),
                            symbol_boundaries=sym_boundaries,
                            symbol_name=sym_name,
                            line_filter=r.get("line_filter"),
                            did_you_mean=is_suggest or did_you_mean,
                            validate=(idx == len(sorted_resolved_items) - 1) and (suggest_idx is None)
                        )

                    occurrences_sum += occ
                    
                    if getattr(r_engine, "indentation_adjusted", False):
                        any_adjusted = True
                        if r_engine.indent_delta:
                            all_deltas.append(r_engine.indent_delta)
                    if getattr(r_engine, "newline_padded", False):
                        any_padded = True
                    if getattr(r_engine, "large_file_fallback", False):
                        any_large_fallback = True

                    if idx == len(sorted_resolved_items) - 1:
                        last_linter_warnings = r_engine.linter_warnings
                        final_engine = r_engine

                if final_engine:
                    final_engine.indentation_adjusted = any_adjusted
                    final_engine.indent_delta = ", ".join(all_deltas)
                    final_engine.newline_padded = any_padded
                    final_engine.large_file_fallback = any_large_fallback

                return temp_content, occurrences_sum, last_linter_warnings, final_engine

            try:
                patched_file, occurrences, last_warnings, engine = run_chain(file_content)
            except ValueError as e:
                # suggestions fallback
                if not did_you_mean:
                    try:
                        # Find which step failed, and run suggestions for it
                        temp_content = file_content
                        failed_idx = None
                        for idx, item in enumerate(sorted_resolved_items):
                            r_engine = PatchEngine(temp_content, target_file, bypass_validation=bypass_validation)
                            r = item["r"]
                            scope = item["scope"]
                            sym_name = item["symbol_name"]
                            
                            try:
                                if scope in ("full", "body"):
                                    b_range = item["body_range"]
                                    start_line = item["start_line"]
                                    end_line = item["end_line"]
                                    start_col = 0
                                    end_col = 0
                                    is_expr = False
                                    
                                    if scope == "body" and b_range is not None:
                                        start_line = b_range.start_line
                                        start_col = b_range.start_col
                                        end_line = b_range.end_line
                                        end_col = b_range.end_col
                                        is_expr = b_range.is_expression
                                    
                                    temp_content, _ = r_engine.apply_symbol_replacement(
                                        replace_content=r["replace_content"],
                                        start_line=start_line,
                                        start_col=start_col,
                                        end_line=end_line,
                                        end_col=end_col,
                                        symbol_scope=scope,
                                        is_expression=is_expr,
                                    )
                                else:
                                    sym_boundaries = None
                                    if sym_name:
                                        sym_boundaries = (item["start_line"], item["end_line"])
                                    
                                    temp_content, _ = r_engine.apply_classic_patch(
                                        search_content=r["search_content"],
                                        replace_content=r["replace_content"],
                                        allow_multiple=r.get("allow_multiple", allow_multiple),
                                        start_line=r.get("start_line"),
                                        end_line=r.get("end_line"),
                                        symbol_boundaries=sym_boundaries,
                                        symbol_name=sym_name,
                                        line_filter=r.get("line_filter"),
                                        did_you_mean=False,
                                        validate=False
                                    )
                            except ValueError:
                                failed_idx = idx
                                break

                        if failed_idx is not None:
                            suggested_patched_file, _, _, _ = run_chain(file_content, suggest_idx=failed_idx)
                            cache = get_cache()
                            run_id = cache.store(
                                entries=[{"target_path": target_path, "patched_content": suggested_patched_file}],
                                original_contents={str(target_path): file_content, target_file: file_content},
                            )
                            return {
                                "error": str(e),
                                "run_id": run_id,
                                "expires_in": cache.get_ttl(),
                                "message": f"To apply the suggestion above directly, call apply_last_dry_run with run_id: '{run_id}'"
                            }
                    except Exception:
                        pass
                return {"error": str(e)}

            engine.linter_warnings = last_warnings
            return _apply_classic_replacement(
                dry_run, file_content, patched_file, target_file, target_path, occurrences,
                None, None, None, None, None, engine
            )

        # Otherwise fallback to classic search/replace or symbol replacement
        symbol_scope = kwargs.get("symbol_scope", "boundary")
        if symbol_scope in ("full", "body"):
            if not symbol_name or replace_content is None:
                return {"error": "Error: Both symbol_name and replace_content must be provided when symbol_scope is 'full' or 'body'."}
        else:
            if search_content is None or replace_content is None:
                return {"error": "Error: Either replacements, patch_content, OR both search_content and replace_content must be provided."}

        # AST Boundary Resolution
        resolved_start_line, resolved_end_line, err, body_range = _resolve_ast_boundaries(
            cwd, target_path, symbol_name, storage_path, start_line, end_line, symbol_scope, file_content
        )
        if err:
            return err

        from .body_parser import MAX_LINES_FOR_TREESITTER, MAX_BYTES_FOR_TREESITTER
        file_lines_count = len(file_content.split("\n"))
        file_char_count = len(file_content)
        if file_char_count > MAX_BYTES_FOR_TREESITTER:
            large_file = True
        else:
            large_file = file_lines_count > MAX_LINES_FOR_TREESITTER or len(file_content.encode("utf-8")) > MAX_BYTES_FOR_TREESITTER

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
                patched_file, occurrences = engine.apply_classic_patch(
                    search_content=search_content,
                    replace_content=replace_content,
                    allow_multiple=allow_multiple,
                    start_line=start_line,
                    end_line=end_line,
                    symbol_boundaries=(resolved_start_line, resolved_end_line),
                    symbol_name=symbol_name,
                    line_filter=line_filter,
                    did_you_mean=did_you_mean
                )
        except SyntaxValidationError as e:
            return {
                "error": f"Syntax Error: {str(e)}",
                "filename": e.filename,
                "line": e.line,
                "column": e.column
            }
        except ValueError as e:
            if symbol_scope in ("full", "body"):
                return {"error": str(e)}
            if not did_you_mean:
                try:
                    suggest_engine = PatchEngine(file_content, target_file, bypass_validation=bypass_validation)
                    suggested_patched_file, _ = suggest_engine.apply_classic_patch(
                        search_content=search_content,
                        replace_content=replace_content,
                        allow_multiple=allow_multiple,
                        start_line=start_line,
                        end_line=end_line,
                        symbol_boundaries=(resolved_start_line, resolved_end_line),
                        symbol_name=symbol_name,
                        line_filter=line_filter,
                        did_you_mean=True
                    )
                    cache = get_cache()
                    run_id = cache.store(
                        entries=[{"target_path": target_path, "patched_content": suggested_patched_file}],
                        original_contents={str(target_path): file_content, target_file: file_content},
                    )
                    return {
                        "error": str(e),
                        "run_id": run_id,
                        "expires_in": cache.get_ttl(),
                        "message": f"To apply the suggestion above directly, call apply_last_dry_run with run_id: '{run_id}'"
                    }
                except Exception:
                    pass
            return {"error": str(e)}

        return _apply_classic_replacement(
            dry_run, file_content, patched_file, target_file, target_path, occurrences,
            symbol_name, resolved_start_line, resolved_end_line, start_line, end_line, engine
        )

    except SyntaxValidationError as e:
        return {
            "error": f"Syntax Error: {str(e)}",
            "filename": e.filename,
            "line": e.line,
            "column": e.column
        }
    except ValueError as e:
        return _handle_patch_file_value_error(e, target_file)


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
        output += f"- Target file: `{target_file}` (Overwriting existing file)\n"
    else:
        output = f"Preview of creating new file `{target_file}`:\n"
        output += f"```\n{code_content}\n```\n"
    
    cache = get_cache()
    run_id = cache.store(
        entries=[{"target_path": target_path, "patched_content": code_content}],
        original_contents={str(target_path): original_content, target_file: original_content},
    )
    res = {
        "success": True,
        "dryRun": True,
        "message": output,
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
                return {
                    "error": f"File already exists at '{target_file}'. To overwrite it, set 'allow_overwrite' to true."
                }
            try:
                with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as f:
                    original_content = f.read()
            except Exception as e:
                return {"error": f"Failed to read existing file: {e}"}

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
            return {"error": f"Failed to write file: {e}"}

        output = f"- Target file: `{target_file}` "
        output += "overwritten successfully\n" if file_exists else "created successfully\n"
        res = {
            "success": True,
            "dryRun": False,
            "message": output,
        }
        if linter_warnings:
            res["warnings"] = linter_warnings
            res["suggestion"] = _get_linter_suggestion(target_file)
        return res

    except SyntaxValidationError as e:
        return {
            "error": f"Syntax Error: {str(e)}",
            "filename": e.filename,
            "line": e.line,
            "column": e.column
        }
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


def _handle_patch_file_value_error(e: ValueError, target_file: str) -> dict:
    if str(e) == "fatal_context_mismatch":
        cwd = Path.cwd().resolve()
        return {
            "error": "fatal_context_mismatch",
            "detail": (
                f"[FATAL CONTEXT MISMATCH]\n"
                f"Relative path '{target_file}' resolves outside the active MCP workspace '{cwd}'.\n\n"
                "Relative paths are restricted to the active workspace to prevent cross-repo drift.\n"
                "To fix:\n"
                "1. Use an absolute path to target a file outside the current workspace.\n"
                "2. Or ensure the terminal shell is CD'ed to the correct repository.\n"
            )
        }
    return {"error": str(e)}


def run_startup_recovery(workspace_path: Path) -> None:
    """Scan for dirty backups in .patchitRIGHT/backups and restore them safely."""
    FileTransaction.run_startup_recovery(workspace_path)


def _verify_dry_run_hashes(files: list[dict]) -> Optional[dict]:
    """Verify that all target files are unchanged since the dry-run."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    for f in files:
        target_path: Path = f["target_path"]
        original_hash: str = f["original_hash"]
        if not target_path.exists():
            if original_hash == empty_hash:
                # File did not exist and still does not exist, which is expected for new file creation
                continue
            return {"error": f"File '{target_path.name}' does not exist but was expected to exist based on dry-run."}
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
        return {"error": f"run_id '{run_id}' not found or expired. Re-run with dry_run=true to get a fresh run_id."}

    files = entry["files"]

    # Hash guard: verify all files are unchanged before writing any
    error_response = _verify_dry_run_hashes(files)
    if error_response:
        return error_response

    # All guards passed — write all files
    applied: list[str] = []
    for f in files:
        target_path = f["target_path"]
        patched_content = f["patched_content"]
        try:
            _write_patched_file(target_path, patched_content)
        except Exception as e:
            return {"error": f"Failed to write '{target_path}': {e}"}
        applied.append(str(target_path))

    output = f"Applied cached patch (run_id={run_id}). Wrote **{len(applied)}** file(s).\n"
    for path in applied:
        output += f"- `{path}` updated.\n"

    return {
        "success": True,
        "dryRun": False,
        "message": output,
    }


def batch_patch_files(
    patches: list[dict],
    dry_run: bool = False,
    storage_path: Optional[str] = None,
    **kwargs
) -> dict:
    """Atomically apply a batch of unified diffs across multiple files with Fuzz=0 and rollback support."""
    for p in patches:
        if "target_file" in p:
            p["target_file"] = os.path.normpath(p["target_file"])

    cwd = Path.cwd().resolve()
    workspace = Workspace(cwd, storage_path)
    workspace_root = _resolve_workspace_root(patches, workspace, cwd)
    
    transaction = FileTransaction(workspace_root)
    processed_patches = []
    
    bypass_validation = bool(kwargs.get("bypass_validation", False))
    
    try:
        err = _process_patches_list(patches, workspace, transaction, processed_patches, bypass_validation=bypass_validation)
        if err:
            return err
    except ValueError as e:
        return _handle_batch_value_error(e)
        
    if dry_run:
        return _apply_batch_dry_run(processed_patches)
        
    return _commit_batch_transaction(transaction, processed_patches)


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
