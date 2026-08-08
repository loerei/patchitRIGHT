"""AST-bounded file editing interface and execution controller facade for patchitRIGHT."""
import difflib
import hashlib
import os
from pathlib import Path
from typing import Optional, Union

from .diagnostics import (
    _attach_engine_flags,
    _create_error_response,
    _handle_batch_value_error,
    _handle_patch_file_value_error,
)
from .dry_run import (
    _verify_dry_run_hashes,
    apply_last_dry_run,
    batch_patch_files,
)
from .engine import PatchEngine
from .line_matcher import (
    _resolve_ast_boundaries,
    check_replacement_collisions,
    sort_resolved_items_descending,
)
from .patch_applier import (
    _apply_classic_replacement,
    _apply_patch_content,
    _process_single_file_in_memory,
)
from .run_cache import get_cache
from .self_mod_safety import (
    _commit_or_defer_transaction,
    _commit_transaction_with_delay,
    _get_linter_suggestion,
    _write_file_with_delay,
    _write_patched_file,
    run_startup_recovery,
    trigger_jcodemunch_sync,
)
from .transaction import FileTransaction
from .validators import SyntaxValidationError, ValidationService
from .workspace import Workspace

LINTER_WARNINGS_PREFIX = "Linter/format warnings detected in patched file"


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


def _build_modified_file_entry(pf: dict, diff_text: Optional[str] = None) -> dict:
    """Construct modified file entry dictionary for response payload."""
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

    pf["relocated_range"] = relocated_range
    pf["did_you_mean_info"] = did_you_mean_info

    return {
        "target_file": pf["target_file"],
        "occurrences": pf["occurrences"],
        "diff_content": diff_text,
        "relocated_range": relocated_range,
        "did_you_mean_info": did_you_mean_info,
    }


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
            "auto_indent": auto_indent,
            **kwargs
        }]

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

    transaction.write_backups()
    ok, fail_path = transaction.check_optimistic_locking()
    if not ok:
        transaction.rollback()
        transaction.cleanup()
        raw_name = fail_path.name if fail_path else "file"
        return _create_error_response(f"Transaction Aborted (Optimistic Locking Conflict): File '{raw_name}' was modified on disk.")

    if dry_run:
        cache = get_cache()
        entries = [
            {
                "target_path": pf["target_path"],
                "patched_content": pf["patched_content"],
                "exists": pf.get("file_exists", True),
                "target_file": pf["target_file"],
                "occurrences": pf["occurrences"],
                "warnings": pf.get("warnings", []),
                "suggestion": pf.get("suggestion", ""),
            }
            for pf in processed_files
        ]
        run_id = cache.store(entries=entries, original_contents=original_contents)
        transaction.cleanup()

        if not multi_file_mode:
            pf = processed_files[0]
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

            modified_files.append(_build_modified_file_entry(pf, diff_text))
            total_occurrences += pf["occurrences"]

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

        modified_files.append(_build_modified_file_entry(pf))
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
    """Handle dry_run=True logic for write_file tool."""
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

        linter_warnings = []
        bypass_validation = bool(kwargs.get("bypass_validation", False))
        if not bypass_validation:
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

        target_path.parent.mkdir(parents=True, exist_ok=True)

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


def _resolve_workspace_root(patches: list[dict], workspace: Workspace, cwd: Path) -> Path:
    """Resolve single common workspace root for list of patch item dicts."""
    target_paths = []
    for p in patches:
        tf = p.get("target_file")
        if tf:
            try:
                target_paths.append(workspace.resolve_safe_path(tf))
            except ValueError:
                pass
    if not target_paths:
        return cwd
    resolved = target_paths[0].parent.resolve()
    for path in target_paths[1:]:
        common = Path(os.path.commonpath([str(resolved), str(path.parent.resolve())]))
        resolved = common
    return resolved


def _process_patches_list(
    patches: list[dict],
    dry_run: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Process list of patch dictionary payloads."""
    cwd = Path.cwd().resolve()
    workspace = Workspace(cwd, storage_path)

    try:
        workspace_root = _resolve_workspace_root(patches, workspace, cwd)
    except ValueError as e:
        return _handle_batch_value_error(e)

    processed_files = []
    modifications: dict[Path, str] = {}
    transaction = FileTransaction(workspace_root)

    for p in patches:
        tf = p.get("target_file")
        if not tf:
            return _create_error_response("Each patch item in 'files' must specify 'target_file'")

        try:
            target_path = workspace.resolve_safe_path(tf)
        except ValueError as e:
            return _handle_batch_value_error(e)

        folder_filter = p.get("folder_filter")
        file_filter = p.get("file_filter")
        orig_content, file_exists, err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
        if err:
            return _create_error_response(err, target_file=tf)

        if not file_exists and not p.get("patch_content") and not p.get("replacements"):
            return _create_error_response(f"Target file '{tf}' does not exist", target_file=tf)

        bypass_val = bool(p.get("bypass_validation", False))
        pf_res = _process_single_file_in_memory(
            target_file=tf,
            target_path=target_path,
            file_content=orig_content,
            bypass_validation=bypass_val,
            cwd=cwd,
            storage_path=storage_path,
            **p,
        )
        if pf_res[4]:
            return _create_error_response(pf_res[4], target_file=tf)

        patched_content, occurrences, _, _, _, _ = pf_res
        diff = generate_diff(orig_content, patched_content, tf)
        empty_hash = hashlib.sha256(b"").hexdigest()
        norm_orig = orig_content.replace("\r\n", "\n").replace("\r", "")
        orig_hash = hashlib.sha256(norm_orig.encode()).hexdigest() if file_exists else empty_hash

        processed_files.append({
            "target_path": target_path,
            "target_file": tf,
            "patched_content": patched_content,
            "original_hash": orig_hash,
            "exists": file_exists,
            "occurrences": occurrences,
            "diff_content": diff,
            "pf_res": pf_res,
        })
        modifications[target_path] = patched_content
        transaction.register_file(target_path)

    if dry_run:
        return _apply_batch_dry_run(processed_files)

    return _commit_batch_transaction(transaction, modifications, processed_files)


def _apply_batch_dry_run(processed_files: list[dict]) -> dict:
    """Build dry-run preview response and store RunCache entry for batch operations."""
    cache_files = []
    modified_files = []
    tot_occ = 0
    tf_primary = processed_files[0]["target_file"] if processed_files else None
    output = f"Previewing batch patch across **{len(processed_files)}** file(s). Use `apply_last_dry_run(run_id='<id>')` to apply on disk.\n\n"

    for pf in processed_files:
        tf = pf["target_file"]
        diff = pf["diff_content"]
        pf_res = pf["pf_res"]
        occ = pf["occurrences"]
        eng = pf_res[5]

        cache_files.append({
            "target_path": pf["target_path"],
            "target_file": tf,
            "patched_content": pf["patched_content"],
            "original_hash": pf["original_hash"],
            "exists": pf["exists"],
            "occurrences": occ,
            "warnings": pf_res[2],
            "suggestion": pf_res[3],
            "engine": eng,
        })

        output += f"### `{tf}`\n```diff\n{diff}\n```\n\n"
        entry = _build_modified_file_entry(pf, diff)
        modified_files.append(entry)
        tot_occ += occ

    run_id = get_cache().store(cache_files)
    return {
        "success": True,
        "dryRun": True,
        "run_id": run_id,
        "expires_in": get_cache().get_ttl(),
        "target_file": tf_primary,
        "message": output.strip(),
        "diff_content": None,
        "occurrences": tot_occ,
        "relocated_range": None,
        "did_you_mean_info": None,
        "modified_files": modified_files,
    }


def _commit_batch_transaction(
    transaction: FileTransaction,
    modifications: dict[Path, str],
    processed_files: list[dict],
) -> dict:
    """Commit batch transaction atomically across files."""
    transaction.write_backups()
    ok, fail_path = transaction.check_optimistic_locking()
    if not ok:
        transaction.rollback()
        transaction.cleanup()
        fail_name = fail_path.name if fail_path else "file"
        return _create_error_response(f"Optimistic locking failed for '{fail_name}'. File was modified on disk.")

    commit_err = _commit_or_defer_transaction(transaction, modifications)
    if commit_err:
        return _create_error_response(commit_err)

    tf_primary = processed_files[0]["target_file"] if processed_files else None
    modified_files = []
    tot_occ = 0
    output = f"Successfully patched **{len(processed_files)}** file(s).\n\n"

    for pf in processed_files:
        tf = pf["target_file"]
        diff = pf["diff_content"]
        occ = pf["occurrences"]

        output += f"### `{tf}`\n```diff\n{diff}\n```\n\n"
        entry = _build_modified_file_entry(pf, diff)
        modified_files.append(entry)
        tot_occ += occ

    return {
        "success": True,
        "dryRun": False,
        "target_file": tf_primary,
        "message": output.strip(),
        "diff_content": None,
        "occurrences": tot_occ,
        "relocated_range": None,
        "did_you_mean_info": None,
        "modified_files": modified_files,
    }
