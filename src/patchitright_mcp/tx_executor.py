"""Unified transactional commit, optimistic locking, dry-run preview, and response seam."""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any, Optional, Union

from .diagnostics import _attach_engine_flags, _create_error_response
from .run_cache import get_cache
from .self_mod_safety import _commit_or_defer_transaction
from .transaction import FileTransaction


def resolve_common_workspace_root(paths: list[Path], cwd: Path) -> Path:
    """Resolve a single common ancestor directory for a list of target paths."""
    if not paths:
        return cwd
    parent_dirs = [p.parent.resolve() for p in paths]
    if len(parent_dirs) == 1:
        return parent_dirs[0]
    try:
        return Path(os.path.commonpath([str(d) for d in parent_dirs]))
    except ValueError:
        return cwd


def generate_diff(
    original: Union[str, bytes],
    modified: Optional[Union[str, bytes]],
    filename: str,
) -> str:
    """Generate a unified diff representation for text, binary, or deletion changes."""
    if isinstance(original, bytes) or isinstance(modified, bytes):
        if modified is None:
            orig_len = len(original) if isinstance(original, bytes) else len(original.encode("utf-8"))
            return f"Binary file deleted: {filename} ({orig_len} bytes)\n"
        new_len = len(modified) if isinstance(modified, bytes) else len(modified.encode("utf-8"))
        return f"Binary file modified/copied: {filename} ({new_len} bytes)\n"

    original_lines = original.splitlines(keepends=True)
    if modified is None:
        diff = difflib.unified_diff(
            original_lines,
            [],
            fromfile=f"a/{filename}",
            tofile="/dev/null",
            n=3,
        )
        return "".join(diff)

    modified_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    )
    return "".join(diff)


def build_modified_file_entry(pf: dict[str, Any], diff_text: Optional[str] = None) -> dict[str, Any]:
    """Construct standardized modified_files entry dictionary for response payload."""
    eng = pf.get("engine")
    relocated_range = pf.get("relocated_range")
    if eng and getattr(eng, "is_relocated", False):
        relocated_range = {
            "start_line": getattr(eng, "relocated_start_line", 1),
            "end_line": getattr(eng, "relocated_end_line", 1),
        }

    did_you_mean_info = pf.get("did_you_mean_info")
    if eng and getattr(eng, "is_did_you_mean_applied", False):
        did_you_mean_info = {
            "applied": True,
            "similarity": getattr(eng, "s_ratio", 0.0),
            "start_line": getattr(eng, "did_you_mean_start_line", None),
            "end_line": getattr(eng, "did_you_mean_end_line", None),
        }

    pf["relocated_range"] = relocated_range
    pf["did_you_mean_info"] = did_you_mean_info

    entry: dict[str, Any] = {
        "target_file": pf["target_file"],
        "occurrences": pf.get("occurrences", 1),
        "diff_content": diff_text,
        "relocated_range": relocated_range,
        "did_you_mean_info": did_you_mean_info,
    }
    if pf.get("action") == "delete":
        entry["action"] = "delete"
    return entry


def _format_single_commit_message(pf: dict[str, Any], run_id: Optional[str] = None) -> str:
    tf = pf["target_file"]
    if run_id is not None:
        return f"Applied cached patch (run_id={run_id}) for `{tf}`.\n"

    action = pf.get("action", "patch")
    if action == "delete":
        return f"Successfully deleted `{tf}`.\n"
    if action == "create":
        return f"Successfully created `{tf}`.\n"
    if action == "overwrite":
        return f"Successfully overwritten `{tf}`.\n"

    eng = pf.get("engine")
    if getattr(eng, "is_did_you_mean_applied", False):
        s_ratio = getattr(eng, "s_ratio", 0.0)
        ratio_pct = round(s_ratio * 100)
        msg = f"Patched 1 occurrence in `{tf}` (applied via 'did_you_mean' fallback, similarity {ratio_pct}%).\n"
    elif getattr(eng, "is_relocated", False):
        r_start = getattr(eng, "relocated_start_line", 1)
        r_end = getattr(eng, "relocated_end_line", 1)
        msg = f"Patched {pf.get('occurrences', 1)} occurrence(s) in `{tf}` (relocated to lines {r_start}-{r_end}).\n"
    else:
        msg = f"Successfully patched `{tf}`.\n"

    if getattr(eng, "indentation_adjusted", False):
        delta = getattr(eng, "indent_delta", "")
        delta_str = f" ({delta})" if delta else ""
        msg += f"Note: replace_content indentation was automatically aligned{delta_str}.\n"
    return msg


def _format_multi_commit_message(
    processed_files: list[dict[str, Any]],
    run_id: Optional[str] = None,
) -> str:
    if run_id is not None:
        msg = f"Applied cached patch (run_id={run_id}). Wrote **{len(processed_files)}** file(s).\n"
    else:
        msg = f"Successfully patched **{len(processed_files)}** file(s):\n"
    for pf in processed_files:
        verb = "deleted" if pf.get("action") == "delete" else "updated"
        msg += f"- `{pf['target_file']}` {verb}.\n"
    return msg


def _collect_warnings(
    processed_files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    structured_warnings: list[dict[str, Any]] = []
    structured_suggestions: list[dict[str, str]] = []
    for pf in processed_files:
        warnings = pf.get("warnings")
        if warnings:
            structured_warnings.append({"file": pf["target_file"], "warnings": warnings})
            structured_suggestions.append({"file": pf["target_file"], "suggestion": pf.get("suggestion", "")})
    return structured_warnings, structured_suggestions


def _preview_single_write_or_patch(pf: dict[str, Any]) -> tuple[str, str]:
    file_content = pf.get("file_content", "")
    patched_content = pf.get("patched_content")
    tf = pf["target_file"]
    action = pf.get("action", "patch")

    diff_text = generate_diff(file_content, patched_content, tf)
    if action == "create" and isinstance(patched_content, str):
        output = f"Preview of creating new file `{tf}`:\n```\n{patched_content}\n```\n"
    elif action == "delete":
        output = f"Preview of deleting `{tf}`:\n```diff\n{diff_text}```\n"
    else:
        output = f"```diff\n{diff_text}```\n"
    return diff_text, output


def execute_file_plan(
    processed_files: list[dict[str, Any]],
    transaction: FileTransaction,
    original_contents: dict[str, Union[str, bytes]],
    modifications: dict[Path, Optional[Union[str, bytes]]],
    dry_run: bool = False,
    multi_file_mode: bool = False,
    skipped_files: Optional[list[dict[str, str]]] = None,
    run_id_applied: Optional[str] = None,
) -> dict[str, Any]:
    """Execute optimistic locking, dry-run caching, or transactional commit for processed files."""
    transaction.write_backups()
    ok, fail_path = transaction.check_optimistic_locking()
    if not ok:
        transaction.rollback()
        transaction.cleanup()
        raw_name = fail_path.name if fail_path else "file"
        return _create_error_response(
            f"Transaction Aborted (Optimistic Locking Conflict): File '{raw_name}' was modified on disk."
        )

    use_multi = multi_file_mode or len(processed_files) > 1 or bool(skipped_files)

    if dry_run:
        cache = get_cache()
        entries = []
        for pf in processed_files:
            patched_val = pf.get("patched_content")
            cache_action = "delete" if patched_val is None else "write"
            entries.append({
                "target_path": pf["target_path"],
                "patched_content": patched_val,
                "action": cache_action,
                "is_binary": bool(pf.get("is_binary", isinstance(patched_val, bytes))),
                "exists": pf.get("file_exists", True),
                "target_file": pf["target_file"],
                "occurrences": pf.get("occurrences", 1),
                "relocated_range": pf.get("relocated_range"),
                "did_you_mean_info": pf.get("did_you_mean_info"),
                "warnings": pf.get("warnings", []),
                "suggestion": pf.get("suggestion", ""),
            })
        run_id = cache.store(entries=entries, original_contents=original_contents)
        transaction.cleanup()

        if not use_multi and processed_files:
            pf = processed_files[0]
            diff_text, msg = _preview_single_write_or_patch(pf)
            res: dict[str, Any] = {
                "success": True,
                "dryRun": True,
                "target_file": pf["target_file"],
                "message": msg,
                "diff_content": diff_text,
                "occurrences": pf.get("occurrences", 1),
                "modified_files": None,
                "run_id": run_id,
                "expires_in": cache.get_ttl(),
            }
            return _attach_engine_flags(res, pf)

        msg = f"Dry-run preview for **{len(processed_files)}** file(s):\n"
        modified_files: list[dict[str, Any]] = []
        total_occurrences = 0
        for pf in processed_files:
            diff_text = generate_diff(pf.get("file_content", ""), pf.get("patched_content"), pf["target_file"])
            msg += f"\n### `{pf['target_file']}`\n```diff\n{diff_text}```\n"
            modified_files.append(build_modified_file_entry(pf, diff_text))
            total_occurrences += pf.get("occurrences", 1)

        if skipped_files:
            msg += f"\nSkipped **{len(skipped_files)}** unmatched file(s).\n"

        structured_warnings, structured_suggestions = _collect_warnings(processed_files)
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
        if skipped_files:
            res["skipped_files"] = skipped_files
        if structured_warnings:
            res["warnings"] = structured_warnings
            res["suggestions"] = structured_suggestions
        return res

    commit_err = _commit_or_defer_transaction(transaction, modifications)
    if commit_err:
        return _create_error_response(commit_err)

    if not use_multi and processed_files:
        pf = processed_files[0]
        msg = _format_single_commit_message(pf, run_id=run_id_applied)
        res = {
            "success": True,
            "dryRun": False,
            "target_file": pf["target_file"],
            "message": msg,
            "diff_content": None,
            "occurrences": pf.get("occurrences", 1),
            "relocated_range": pf.get("relocated_range"),
            "did_you_mean_info": pf.get("did_you_mean_info"),
            "modified_files": None,
        }
        return _attach_engine_flags(res, pf)

    msg = _format_multi_commit_message(processed_files, run_id=run_id_applied)
    if skipped_files:
        msg += f"Skipped **{len(skipped_files)}** unmatched file(s).\n"

    modified_files = [build_modified_file_entry(pf) for pf in processed_files]
    total_occurrences = sum(pf.get("occurrences", 1) for pf in processed_files)
    structured_warnings, structured_suggestions = _collect_warnings(processed_files)

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
    if skipped_files:
        res["skipped_files"] = skipped_files
    if structured_warnings:
        res["warnings"] = structured_warnings
        res["suggestions"] = structured_suggestions
    return res
