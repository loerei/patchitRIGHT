"""AST-bounded file editor (patch_file)."""

import difflib
import os
import time
from pathlib import Path
from typing import Optional, Union

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.security import validate_path


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


def _apply_line_filters(
    target_slice: str,
    norm_search: str,
    start_idx: int,
    line_filter: Optional[Union[str, int]],
) -> Optional[dict]:
    """Assert lineFilter substring or numeric line index checks."""
    if line_filter is None:
        return None

    is_numeric = False
    try:
        assert_line_num = int(line_filter)
        is_numeric = True
    except (ValueError, TypeError):
        pass

    if is_numeric:
        match_index = target_slice.find(norm_search)
        before_match = target_slice[:match_index]
        lines_before_match = before_match.count("\n")
        actual_start_line = start_idx + 1 + lines_before_match
        if actual_start_line != assert_line_num:
            return {
                "error": f"Error: lineFilter assertion failed! The search content starts at line {actual_start_line}, but lineFilter asserted line {assert_line_num}."
            }
    else:
        if str(line_filter) not in target_slice:
            return {
                "error": f"Error: lineFilter assertion failed! The target scope does not contain the substring '{line_filter}'."
            }
    return None


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


def _find_closest_match(
    file_lines: list[str],
    start_idx: int,
    end_idx: int,
    norm_search: str,
) -> Optional[tuple[int, int, str, float]]:
    """Find the slice of lines in file_lines[start_idx:end_idx+1] that is most similar to norm_search."""
    import difflib
    search_lines = norm_search.split("\n")
    n = len(search_lines)
    
    best_ratio = 0.0
    best_slice = None
    best_range = None
    
    for window_size in (n, max(1, n - 1), n + 1):
        for i in range(start_idx, min(end_idx - window_size + 2, len(file_lines))):
            candidate_slice = "\n".join(file_lines[i : i + window_size])
            ratio = difflib.SequenceMatcher(None, candidate_slice, norm_search).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_slice = candidate_slice
                best_range = (i + 1, i + window_size)
                
    if best_ratio >= 0.5 and best_range and best_slice:
        return best_range[0], best_range[1], best_slice, best_ratio
    return None


def _slice_and_check_occurrences(
    file_lines: list[str],
    start_idx: int,
    end_idx: int,
    norm_search: str,
    allow_multiple: bool,
    symbol_name: Optional[str],
) -> tuple[Optional[str], Optional[dict]]:
    """Slice target content and verify occurrence counts within scope."""
    target_slice = "\n".join(file_lines[start_idx:end_idx + 1])
    occurrences = target_slice.count(norm_search)

    if occurrences == 0:
        first_lines = "\n".join(norm_search.split("\n")[:3])
        err_msg = f"Error: Search content not found inside the specified scope (lines {start_idx + 1} to {end_idx + 1})!\nFirst 3 lines of search block:\n{first_lines}"
        if symbol_name:
            err_msg += f"\nAST Scope: Symbol '{symbol_name}' at lines {start_idx + 1}-{end_idx + 1}"
            
        suggestion = _find_closest_match(file_lines, start_idx, end_idx, norm_search)
        if suggestion:
            s_start, s_end, s_text, s_ratio = suggestion
            err_msg += f"\n\nDid you mean (lines {s_start} to {s_end}, similarity {s_ratio:.0%}):\n{s_text}"
            
        return None, {"error": err_msg}

    if not allow_multiple and occurrences > 1:
        return None, {
            "error": (
                f"Error: Search content occurs {occurrences} times within the specified scope (lines {start_idx + 1} to {end_idx + 1}). "
                "To replace all, set 'allow_multiple: true'."
            )
        }

    return target_slice, None


def _resolve_allowed_base_dir(
    target_file: str,
    base_dir: str,
    storage_path: Optional[str]
) -> str:
    """Resolve the allowed base directory, using indexed repo source_root if available."""
    try:
        from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
        temp_resolved = os.path.abspath(os.path.join(base_dir, target_file))
        repo_res = resolve_repo_fn(temp_resolved, storage_path)
        if repo_res.get("found") and "source_root" in repo_res:
            return os.path.abspath(repo_res["source_root"])
    except Exception:
        pass
    return base_dir


def _parse_unified_diff(patch_str: str) -> list[dict]:
    """Parse unified diff string into a list of hunk dicts."""
    import re
    hunks = []
    lines = patch_str.splitlines()
    
    hunk_header_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
    
    current_hunk = None
    
    for line in lines:
        match = hunk_header_re.match(line)
        if match:
            old_start = int(match.group(1))
            old_lines = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_lines = int(match.group(4)) if match.group(4) else 1
            
            current_hunk = {
                'old_start': old_start,
                'old_lines': old_lines,
                'new_start': new_start,
                'new_lines': new_lines,
                'lines': []
            }
            hunks.append(current_hunk)
            continue
            
        if current_hunk is not None:
            if line.startswith('-'):
                current_hunk['lines'].append(('-', line[1:]))
            elif line.startswith('+'):
                current_hunk['lines'].append(('+', line[1:]))
            elif line.startswith(' '):
                current_hunk['lines'].append((' ', line[1:]))
            elif line == '':
                current_hunk['lines'].append((' ', ''))
            else:
                if line.startswith('--- ') or line.startswith('+++ ') or line.startswith('diff '):
                    current_hunk = None
                else:
                    current_hunk['lines'].append((' ', line))
                    
    return hunks


def _find_closest_hunk_match(file_lines: list[str], hunk_old_lines: list[str], start_search: int) -> Optional[tuple[int, float]]:
    """Locate the closest matching line block in file_lines for hunk_old_lines."""
    import difflib
    search_str = "\n".join(hunk_old_lines)
    n = len(hunk_old_lines)
    if n == 0:
        return None
        
    best_ratio = 0.0
    best_line = None
    
    search_range = range(max(0, start_search - 20), min(len(file_lines) - n + 1, start_search + 20))
    if not list(search_range):
        search_range = range(len(file_lines) - n + 1)
        
    for i in search_range:
        candidate = "\n".join(file_lines[i : i + n])
        ratio = difflib.SequenceMatcher(None, candidate, search_str).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_line = i + 1
            
    if best_ratio < 0.5:
        for i in range(len(file_lines) - n + 1):
            candidate = "\n".join(file_lines[i : i + n])
            ratio = difflib.SequenceMatcher(None, candidate, search_str).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_line = i + 1
                
    if best_ratio >= 0.3 and best_line is not None:
        return best_line, best_ratio
    return None


def _apply_unified_patch(file_lines: list[str], patch_str: str) -> tuple[Optional[list[str]], Optional[dict]]:
    """Strictly apply a unified diff with Fuzz = 0."""
    hunks = _parse_unified_diff(patch_str)
    if not hunks:
        return None, {"error": "Error: No valid unified diff hunks found in patch_content."}
        
    file_lines = list(file_lines)
    offset = 0
    
    hunk_index = 0
    for hunk in hunks:
        hunk_index += 1
        expected_old_lines = [l_content for l_type, l_content in hunk['lines'] if l_type in (' ', '-')]
        expected_pos = hunk['old_start'] - 1 + offset
        
        match_success = True
        if expected_pos < 0 or expected_pos + len(expected_old_lines) > len(file_lines):
            match_success = False
        else:
            for idx, expected_line in enumerate(expected_old_lines):
                if file_lines[expected_pos + idx] != expected_line:
                    match_success = False
                    break
                    
        if not match_success:
            first_few_old = "\n".join(expected_old_lines[:3])
            err_msg = (
                f"Error: Unified Diff hunk #{hunk_index} failed to match strictly at line {hunk['old_start']} "
                f"(adjusted to line {expected_pos + 1} with cumulative offset {offset})!\n"
                f"First 3 lines of expected old hunk:\n{first_few_old}"
            )
            
            suggestion = _find_closest_hunk_match(file_lines, expected_old_lines, expected_pos)
            if suggestion:
                s_line, s_ratio = suggestion
                s_text = "\n".join(file_lines[s_line - 1 : s_line - 1 + len(expected_old_lines)])
                err_msg += f"\n\nDid you mean (line {s_line}, similarity {s_ratio:.0%}):\n{s_text}"
                
            return None, {"error": err_msg}
            
        new_hunk_lines = []
        for l_type, l_content in hunk['lines']:
            if l_type in (' ', '+'):
                new_hunk_lines.append(l_content)
                
        file_lines[expected_pos : expected_pos + len(expected_old_lines)] = new_hunk_lines
        hunk_offset = len(new_hunk_lines) - len(expected_old_lines)
        offset += hunk_offset
        
    return file_lines, None


def _patch_file_impl(
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
    """Internal implementation of patch_file."""
    cwd = Path.cwd().resolve()
    base_dir = os.path.abspath(cwd)
    base_dir = _resolve_allowed_base_dir(target_file, base_dir, storage_path)

    # --- Context Mismatch Guard & Path Traversal Protection ---
    resolved_path = os.path.abspath(os.path.join(base_dir, target_file))
    if not os.path.isabs(target_file):
        if not resolved_path.startswith(base_dir + os.sep) and resolved_path != base_dir:
            raise ValueError("fatal_context_mismatch")

    target_path = Path(resolved_path)

    # --- Filter Checks & File Read ---
    file_content, err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
    if err:
        return err

    is_crlf = "\r\n" in file_content
    norm_file = file_content.replace("\r\n", "\n")
    file_lines = norm_file.split("\n")

    # Handle Unified Diff patch format
    if patch_content is not None:
        norm_patch = patch_content.replace("\r\n", "\n")
        patched_lines, err = _apply_unified_patch(file_lines, norm_patch)
        if err:
            return err
            
        patched_file = "\n".join(patched_lines)
        if is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")
            
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
            
        # Write patched file
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

    norm_search = search_content.replace("\r\n", "\n")
    norm_replace = replace_content.replace("\r\n", "\n")

    # --- Boundary Resolution ---
    resolved_start_line, resolved_end_line, err = _resolve_ast_boundaries(
        cwd, target_path, symbol_name, storage_path, start_line, end_line
    )
    if err:
        return err

    # Determine line boundaries (1-indexed inclusive to 0-indexed slice)
    start_idx = (resolved_start_line - 1) if resolved_start_line is not None else 0
    end_idx = (resolved_end_line - 1) if resolved_end_line is not None else len(file_lines) - 1

    start_idx = max(0, min(start_idx, len(file_lines) - 1))
    end_idx = max(start_idx, min(end_idx, len(file_lines) - 1))

    # Slice target content inside scope boundary and verify occurrences
    target_slice, err = _slice_and_check_occurrences(
        file_lines, start_idx, end_idx, norm_search, allow_multiple, symbol_name
    )
    if err:
        return err

    occurrences = target_slice.count(norm_search)

    # --- Line Filter Assertion ---
    err = _apply_line_filters(target_slice, norm_search, start_idx, line_filter)
    if err:
        return err

    # Apply replacement
    patched_slice = target_slice.replace(norm_search, norm_replace)

    before_part = "\n".join(file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
    after_part = "\n" + "\n".join(file_lines[end_idx + 1:]) if end_idx < len(file_lines) - 1 else ""
    patched_file = before_part + patched_slice + after_part

    if is_crlf:
        patched_file = patched_file.replace("\n", "\r\n")

    if dry_run:
        diff_text = generate_diff(file_content, patched_file, target_file)
        output = f"```diff\n{diff_text}```\n"
        output += f"- Target file: `{target_file}`\n"
        output += f"- Match occurrences inside scope: **{occurrences}**\n"
        if symbol_name:
            output += f"- Scope: AST symbol `{symbol_name}` (lines {start_idx + 1}-{end_idx + 1})\n"
        elif start_line or end_line:
            output += f"- Scope: Line range {start_idx + 1}-{end_idx + 1}\n"

        return {
            "success": True,
            "dryRun": True,
            "message": output,
            "occurrences": occurrences
        }

    # Write patched file
    try:
        target_path.write_text(patched_file, encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to write patched file: {e}"}

    output = f"- Target file: `{target_file}`\n"
    output += f"- Replaced occurrences: **{occurrences}**\n"
    if symbol_name:
        output += f"- Scope: AST symbol `{symbol_name}` (lines {start_idx + 1}-{end_idx + 1})\n"
    elif start_line or end_line:
        output += f"- Scope: Line range {start_idx + 1}-{end_idx + 1}\n"
    if occurrences > 1:
        output += f"⚠️ *Warning:* Replaced {occurrences} identical occurrences.\n"

    return {
        "success": True,
        "dryRun": False,
        "message": output,
        "occurrences": occurrences
    }


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
        # Path validation at the entry point to satisfy static taint-analysis engines
        cwd = Path.cwd().resolve()
        base_dir = os.path.abspath(cwd)
        base_dir = _resolve_allowed_base_dir(target_file, base_dir, storage_path)

        resolved_path = os.path.abspath(os.path.join(base_dir, target_file))
        if not os.path.isabs(target_file):
            if not resolved_path.startswith(base_dir + os.sep) and resolved_path != base_dir:
                raise ValueError("fatal_context_mismatch")

        return _patch_file_impl(
            target_file=target_file,
            search_content=search_content,
            replace_content=replace_content,
            folder_filter=folder_filter,
            file_filter=file_filter,
            start_line=start_line,
            end_line=end_line,
            symbol_name=symbol_name,
            allow_multiple=allow_multiple,
            line_filter=line_filter,
            dry_run=dry_run,
            storage_path=storage_path,
            patch_content=patch_content,
        )
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


def _get_backup_path(target_path: Path, base_dir: Path) -> Path:
    """Resolve a safe, collision-free backup path inside .patchitRIGHT/backups/."""
    target_abs = Path(os.path.abspath(target_path))
    base_abs = Path(os.path.abspath(base_dir))
    backup_root = base_dir / ".patchitRIGHT" / "backups"
    try:
        target_norm = Path(os.path.normcase(str(target_abs)))
        base_norm = Path(os.path.normcase(str(base_abs)))
        # Perform relative check on normalized paths to avoid case mismatch on Windows
        _ = target_norm.relative_to(base_norm)
        rel_parts = target_abs.parts[len(base_abs.parts):]
        return backup_root / "relative" / Path(*rel_parts)
    except ValueError:
        parts = list(target_path.parts)
        if parts and (parts[0].endswith(":\\") or parts[0].endswith(":/")):
            drive = parts[0][0]
            parts[0] = drive
        elif parts and (parts[0] == "/" or parts[0] == "\\"):
            parts = parts[1:]
        return backup_root / "absolute" / Path(*parts)


def _restore_single_backup(bak_path: Path, target_path: Path) -> None:
    """Restore target file from .bak with timestamps check."""
    if not bak_path.exists():
        return
    if not target_path.exists():
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(bak_path.read_bytes())
        except Exception:
            pass
        return
    try:
        bak_mtime = bak_path.stat().st_mtime
        target_mtime = target_path.stat().st_mtime
        if target_mtime <= bak_mtime + 2:
            target_path.write_bytes(bak_path.read_bytes())
    except Exception:
        pass


def run_startup_recovery(workspace_path: Path) -> None:
    """Scan for dirty backups in .patchitRIGHT/backups and restore them safely."""
    backup_root = workspace_path / ".patchitRIGHT" / "backups"
    if not backup_root.exists():
        return
        
    try:
        # 1. Recover relative backups
        rel_root = backup_root / "relative"
        if rel_root.exists():
            for root, _, files in os.walk(rel_root):
                for file in files:
                    bak_path = Path(root) / file
                    rel_file_path = bak_path.relative_to(rel_root)
                    target_path = workspace_path / rel_file_path
                    _restore_single_backup(bak_path, target_path)
                    
        # 2. Recover absolute backups
        abs_root = backup_root / "absolute"
        if abs_root.exists():
            for root, _, files in os.walk(abs_root):
                for file in files:
                    bak_path = Path(root) / file
                    rel_file_path = bak_path.relative_to(abs_root)
                    parts = list(rel_file_path.parts)
                    if len(parts) > 0:
                        if len(parts[0]) == 1 and parts[0].isalpha():
                            drive = parts[0] + ":\\"
                            target_path = Path(drive) / Path(*parts[1:])
                        else:
                            target_path = Path("/") / Path(*parts)
                        _restore_single_backup(bak_path, target_path)
                        
        # Clean up the hidden backup folder structure completely
        import shutil
        shutil.rmtree(workspace_path / ".patchitRIGHT", ignore_errors=True)
    except Exception:
        pass


def _find_workspace_root(path: Path) -> Path:
    """Walk up to locate a project root using common anchor files."""
    current = path.resolve()
    if current.is_file():
        current = current.parent
    anchors = {".git", ".gitignore", "pyproject.toml", "package.json", "go.mod", "cargo.toml", ".patchitRIGHT"}
    for parent in [current] + list(current.parents):
        if any((parent / anchor).exists() for anchor in anchors):
            return parent
    return current


def batch_patch_files(
    patches: list[dict],
    dry_run: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Atomically apply a batch of unified diffs across multiple files with Fuzz=0 and rollback support."""
    import hashlib
    cwd = Path.cwd().resolve()
    base_dir = os.path.abspath(cwd)
    
    # Resolve the active workspace root using the first target file to prevent
    # backup pollution inside the global MCP server directory.
    first_target = patches[0].get("target_file") if patches else None
    if first_target:
        temp_base = _resolve_allowed_base_dir(first_target, base_dir, storage_path)
        resolved_path = os.path.abspath(os.path.join(temp_base, first_target))
        base_dir_path = _find_workspace_root(Path(resolved_path))
    else:
        base_dir_path = Path(base_dir)
    
    processed_patches = []
    
    try:
        for p in patches:
            raw_target = p.get("target_file")
            patch_content = p.get("patch_content")
            if not raw_target or not patch_content:
                return {"error": "Error: Each patch in patches array must have target_file and patch_content."}
                
            temp_base = _resolve_allowed_base_dir(raw_target, base_dir, storage_path)
            resolved_path = os.path.abspath(os.path.join(temp_base, raw_target))
            if not os.path.isabs(raw_target):
                if not resolved_path.startswith(temp_base + os.sep) and resolved_path != temp_base:
                    raise ValueError("fatal_context_mismatch")
                    
            target_path = Path(resolved_path)
            if not target_path.exists():
                return {"error": f"Error: Target file not found at {raw_target}."}
                
            try:
                original_bytes = target_path.read_bytes()
                original_hash = hashlib.sha256(original_bytes).hexdigest()
                original_content = original_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n")
            except Exception as e:
                return {"error": f"Error: Failed to read file {raw_target}: {e}."}
                
            is_crlf = "\r\n" in original_content
            norm_original = original_content.replace("\r\n", "\n")
            file_lines = norm_original.split("\n")
            
            norm_patch = patch_content.replace("\r\n", "\n")
            patched_lines, err = _apply_unified_patch(file_lines, norm_patch)
            if err:
                return {
                    "error": f"Validation failed for file {raw_target}: {err['error']}",
                    "detail": err.get("error")
                }
                
            patched_content = "\n".join(patched_lines)
            if is_crlf:
                patched_content = patched_content.replace("\n", "\r\n")
                
            bak_path = _get_backup_path(target_path, base_dir_path)
            
            processed_patches.append({
                "target_path": target_path,
                "raw_target": raw_target,
                "original_content": original_content,
                "original_hash": original_hash,
                "bak_path": bak_path,
                "patched_content": patched_content,
                "is_crlf": is_crlf
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
    backups_created = []
    try:
        for item in processed_patches:
            # Enforce parent directory creation for backups
            item["bak_path"].parent.mkdir(parents=True, exist_ok=True)
            item["bak_path"].write_bytes(item["target_path"].read_bytes())
            backups_created.append(item)
    except Exception as e:
        for b in backups_created:
            try:
                b["bak_path"].unlink()
            except Exception:
                pass
        return {"error": f"Failed to create backup files during transaction: {e}"}
        
    # Optimistic Locking Check
    mismatch_detected = False
    mismatch_file = ""
    for item in processed_patches:
        current_bytes = item["target_path"].read_bytes()
        current_hash = hashlib.sha256(current_bytes).hexdigest()
        if current_hash != item["original_hash"]:
            mismatch_detected = True
            mismatch_file = item["raw_target"]
            break
            
    if mismatch_detected:
        for b in backups_created:
            try:
                b["bak_path"].unlink()
            except Exception:
                pass
        return {
            "error": (
                f"Transaction Aborted (Optimistic Locking Conflict): "
                f"File '{mismatch_file}' was modified by an external process during the transaction."
            )
        }
        
    # Commit Phase
    writes_succeeded = True
    failed_write_file = ""
    failed_write_error = ""
    
    written_files = []
    for item in processed_patches:
        try:
            item["target_path"].write_text(item["patched_content"], encoding="utf-8")
            written_files.append(item)
        except Exception as e:
            writes_succeeded = False
            failed_write_file = item["raw_target"]
            failed_write_error = str(e)
            break
            
    if not writes_succeeded:
        # ROLLBACK PHASE
        for item in written_files:
            try:
                item["target_path"].write_text(item["original_content"], encoding="utf-8")
            except Exception:
                pass
        for item in backups_created:
            try:
                if item["bak_path"].exists():
                    item["target_path"].write_bytes(item["bak_path"].read_bytes())
            except Exception:
                pass
        # Cleanup backups
        import shutil
        shutil.rmtree(base_dir_path / ".patchitRIGHT", ignore_errors=True)
        return {"error": f"Commit failed during write of '{failed_write_file}': {failed_write_error}. Rolled back transaction safely."}
        
    # Clean up backups on success
    import shutil
    shutil.rmtree(base_dir_path / ".patchitRIGHT", ignore_errors=True)
            
    output = f"Transaction applied successfully. Patched **{len(processed_patches)}** files.\n"
    for item in processed_patches:
        output += f"- `{item['raw_target']}` updated successfully.\n"
        
    return {
        "success": True,
        "dryRun": False,
        "message": output
    }
