"""AST-bounded file editor (patch_file) utilizing workspace, engine, and transaction modules."""

import difflib
import hashlib
import os
from pathlib import Path
from typing import Optional

from jcodemunch_mcp.storage import IndexStore
from .workspace import Workspace
from .engine import PatchEngine
from .transaction import FileTransaction
from .run_cache import get_cache
from .validators import SyntaxValidationError

LINTER_WARNINGS_PREFIX = "\n*Linter Warnings:*\n"


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
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


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
) -> tuple[Optional[int], Optional[int], Optional[dict]]:
    """Resolve start and end lines for AST symbolName boundary."""
    if not symbol_name:
        return start_line, end_line, None

    try:
        from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
        repo_res = resolve_repo_fn(str(cwd), storage_path)
        if not repo_res.get("found"):
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed. Call index_folder first to resolve symbols."}
        
        repo_id = repo_res["repo"]
        owner, name = repo_id.split("/", 1)
        
        store = IndexStore(base_path=storage_path)
        index = store.load_index(owner, name)
        if not index:
            return None, None, {"error": f"Index for '{repo_id}' could not be loaded."}
            
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
            return None, None, {"error": f"Symbol '{symbol_name}' not found in file '{rel_file_path}'."}
            
        symbol = matched_symbols[0]
        return symbol["line"], symbol["end_line"], None
    except Exception as e:
        return None, None, {"error": f"Error resolving symbolName '{symbol_name}': {e}"}


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
            engine = PatchEngine(file_content, target_file)
            return _apply_patch_content(
                engine, patch_content, dry_run, target_file, target_path, file_content
            )

        # Handle Multiple Replacements (Multi-patch)
        if replacements is not None:
            for r in replacements:
                if "search_content" not in r or "replace_content" not in r:
                    return {"error": "Error: Each entry in replacements must have search_content and replace_content."}

            # Sort bottom-up based on start_line (descending) to prevent line-drift
            sorted_replacements = sorted(
                replacements,
                key=lambda r: r.get("start_line") or 1,
                reverse=True
            )

            # Helper to run the chain of replacements
            def run_chain(contents: str, suggest_idx: Optional[int] = None) -> tuple[str, int]:
                temp_content = contents
                occurrences_sum = 0
                for idx, r in enumerate(sorted_replacements):
                    r_engine = PatchEngine(temp_content, target_file)
                    sym_name = r.get("symbol_name")
                    r_start = r.get("start_line")
                    r_end = r.get("end_line")
                    r_symbol_boundaries = None
                    if sym_name:
                        sym_start, sym_end, sym_err = _resolve_ast_boundaries(
                            cwd, target_path, sym_name, storage_path, r_start, r_end
                        )
                        if not sym_err:
                            r_symbol_boundaries = (sym_start, sym_end)

                    is_suggest = (suggest_idx is not None and idx == suggest_idx)
                    temp_content, occ = r_engine.apply_classic_patch(
                        search_content=r["search_content"],
                        replace_content=r["replace_content"],
                        allow_multiple=r.get("allow_multiple", allow_multiple),
                        start_line=r_start,
                        end_line=r_end,
                        symbol_boundaries=r_symbol_boundaries,
                        symbol_name=sym_name,
                        line_filter=r.get("line_filter"),
                        did_you_mean=is_suggest or did_you_mean
                    )
                    occurrences_sum += occ
                return temp_content, occurrences_sum

            try:
                patched_file, occurrences = run_chain(file_content)
            except ValueError as e:
                if not did_you_mean:
                    try:
                        # Find which step failed, and run suggestions for it
                        temp_content = file_content
                        failed_idx = None
                        for idx, r in enumerate(sorted_replacements):
                            r_engine = PatchEngine(temp_content, target_file)
                            sym_name = r.get("symbol_name")
                            r_start = r.get("start_line")
                            r_end = r.get("end_line")
                            r_symbol_boundaries = None
                            if sym_name:
                                sym_start, sym_end, sym_err = _resolve_ast_boundaries(
                                    cwd, target_path, sym_name, storage_path, r_start, r_end
                                )
                                if not sym_err:
                                    r_symbol_boundaries = (sym_start, sym_end)
                            try:
                                temp_content, _ = r_engine.apply_classic_patch(
                                    search_content=r["search_content"],
                                    replace_content=r["replace_content"],
                                    allow_multiple=r.get("allow_multiple", allow_multiple),
                                    start_line=r_start,
                                    end_line=r_end,
                                    symbol_boundaries=r_symbol_boundaries,
                                    symbol_name=sym_name,
                                    line_filter=r.get("line_filter"),
                                    did_you_mean=False
                                )
                            except ValueError:
                                failed_idx = idx
                                break

                        if failed_idx is not None:
                            suggested_patched_file, _ = run_chain(file_content, suggest_idx=failed_idx)
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

            engine = PatchEngine(patched_file, target_file)
            return _apply_classic_replacement(
                dry_run, file_content, patched_file, target_file, target_path, occurrences,
                symbol_name, None, None, None, None, engine
            )

        # Otherwise fallback to classic search/replace
        if search_content is None or replace_content is None:
            return {"error": "Error: Either replacements, patch_content, OR both search_content and replace_content must be provided."}

        # AST Boundary Resolution
        resolved_start_line, resolved_end_line, err = _resolve_ast_boundaries(
            cwd, target_path, symbol_name, storage_path, start_line, end_line
        )
        if err:
            return err

        engine = PatchEngine(file_content, target_file)
        try:
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
            if not did_you_mean:
                try:
                    suggest_engine = PatchEngine(file_content, target_file)
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
    for f in files:
        target_path: Path = f["target_path"]
        original_hash: str = f["original_hash"]
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
    
    try:
        err = _process_patches_list(patches, workspace, transaction, processed_patches)
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
    patches: list[dict], workspace: Workspace, transaction: FileTransaction, processed_patches: list[dict]
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
        
        engine = PatchEngine(original_content, raw_target)
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
            
    output = f"Transaction applied successfully. Patched **{len(processed_patches)}** files.\n"
    for item in processed_patches:
        output += f"- `{item['raw_target']}` updated successfully.\n"
        
    return {
        "success": True,
        "dryRun": False,
        "message": output
    }
