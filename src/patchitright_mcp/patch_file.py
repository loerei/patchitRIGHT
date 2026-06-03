"""AST-bounded file editor (patch_file) utilizing workspace, engine, and transaction modules."""

import difflib
from pathlib import Path
from typing import Optional, Union

from jcodemunch_mcp.storage import IndexStore
from .workspace import Workspace
from .engine import PatchEngine
from .transaction import FileTransaction


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
        return target_path.read_text(encoding="utf-8", errors="replace"), None
    except Exception as e:
        return None, {"error": f"Failed to read file: {e}"}


def patch_file(
    target_file: str,
    search_content: Optional[str] = None,
    replace_content: Optional[str] = None,
    folder_filter: Optional[str] = None,
    file_filter: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    symbol_name: Optional[str] = None,
    allow_multiple: bool = False,
    line_filter: Optional[Union[str, int]] = None,
    dry_run: bool = False,
    storage_path: Optional[str] = None,
    patch_content: Optional[str] = None,
) -> dict:
    """Perform a robust search-and-replace or apply a strict unified diff (Fuzz = 0)."""
    try:
        cwd = Path.cwd().resolve()
        workspace = Workspace(cwd, storage_path)
        
        # Safe path resolution and workspace mismatch check
        target_path = workspace.resolve_safe_path(target_file)
        
        # Read file and verify file/folder filters
        file_content, err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
        if err:
            return err

        engine = PatchEngine(file_content, target_file)

        # Handle Unified Diff patch format
        if patch_content is not None:
            try:
                patched_file = engine.apply_unified_patch(patch_content)
            except ValueError as e:
                return {"error": str(e)}
                
            if dry_run:
                diff_text = generate_diff(file_content, patched_file, target_file)
                output = f"```diff\n{diff_text}```\n"
                output += f"- Target file: `{target_file}`\n"
                output += f"- Format: Unified Diff (Strict Fuzz = 0)\n"
                return {
                    "success": True,
                    "dryRun": True,
                    "message": output,
                    "occurrences": 1
                }
                
            try:
                target_path.write_text(patched_file, encoding="utf-8")
            except Exception as e:
                return {"error": f"Failed to write patched file: {e}"}
                
            output = f"- Target file: `{target_file}`\n"
            output += f"- Format: Unified Diff (Strict Fuzz = 0) applied successfully\n"
            return {
                "success": True,
                "dryRun": False,
                "message": output,
                "occurrences": 1
            }

        # Otherwise fallback to classic search/replace
        if search_content is None or replace_content is None:
            return {"error": "Error: Either patch_content OR both search_content and replace_content must be provided."}

        # AST Boundary Resolution
        resolved_start_line, resolved_end_line, err = _resolve_ast_boundaries(
            cwd, target_path, symbol_name, storage_path, start_line, end_line
        )
        if err:
            return err

        try:
            patched_file, occurrences = engine.apply_classic_patch(
                search_content=search_content,
                replace_content=replace_content,
                allow_multiple=allow_multiple,
                start_line=start_line,
                end_line=end_line,
                symbol_boundaries=(resolved_start_line, resolved_end_line),
                symbol_name=symbol_name,
                line_filter=line_filter
            )
        except ValueError as e:
            return {"error": str(e)}

        if dry_run:
            diff_text = generate_diff(file_content, patched_file, target_file)
            output = f"```diff\n{diff_text}```\n"
            output += f"- Target file: `{target_file}`\n"
            output += f"- Match occurrences inside scope: **{occurrences}**\n"
            if symbol_name:
                output += f"- Scope: AST symbol `{symbol_name}` (lines {resolved_start_line}-{resolved_end_line})\n"
            elif start_line or end_line:
                start_disp = resolved_start_line if resolved_start_line is not None else 1
                end_disp = resolved_end_line if resolved_end_line is not None else len(engine.file_lines)
                output += f"- Scope: Line range {start_disp}-{end_disp}\n"

            return {
                "success": True,
                "dryRun": True,
                "message": output,
                "occurrences": occurrences
            }

        try:
            target_path.write_text(patched_file, encoding="utf-8")
        except Exception as e:
            return {"error": f"Failed to write patched file: {e}"}

        output = f"- Target file: `{target_file}`\n"
        output += f"- Replaced occurrences: **{occurrences}**\n"
        if symbol_name:
            output += f"- Scope: AST symbol `{symbol_name}` (lines {resolved_start_line}-{resolved_end_line})\n"
        elif start_line or end_line:
            start_disp = resolved_start_line if resolved_start_line is not None else 1
            end_disp = resolved_end_line if resolved_end_line is not None else len(engine.file_lines)
            output += f"- Scope: Line range {start_disp}-{end_disp}\n"
        if occurrences > 1:
            output += f"⚠️ *Warning:* Replaced {occurrences} identical occurrences.\n"

        return {
            "success": True,
            "dryRun": False,
            "message": output,
            "occurrences": occurrences
        }

    except ValueError as e:
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


def batch_patch_files(
    patches: list[dict],
    dry_run: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Atomically apply a batch of unified diffs across multiple files with Fuzz=0 and rollback support."""
    cwd = Path.cwd().resolve()
    workspace = Workspace(cwd, storage_path)
    
    first_target = patches[0].get("target_file") if patches else None
    if first_target:
        try:
            resolved_path = workspace.resolve_safe_path(first_target)
            workspace_root = workspace.find_workspace_root(resolved_path)
        except Exception:
            workspace_root = cwd
    else:
        workspace_root = cwd
    
    transaction = FileTransaction(workspace_root)
    processed_patches = []
    
    try:
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
            
    except ValueError as e:
        if str(e) == "fatal_context_mismatch":
            return {
                "error": "fatal_context_mismatch",
                "detail": "[FATAL CONTEXT MISMATCH] File resolves outside active workspace."
            }
        return {"error": str(e)}
        
    if dry_run:
        outputs = []
        for item in processed_patches:
            diff_text = generate_diff(item["original_content"], item["patched_content"], item["raw_target"])
            outputs.append(f"```diff\n{diff_text}```\n- Target file: `{item['raw_target']}`")
        return {
            "success": True,
            "dryRun": True,
            "message": "\n\n".join(outputs)
        }
        
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
