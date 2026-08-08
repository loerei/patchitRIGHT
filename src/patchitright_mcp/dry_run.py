"""Dry-run preview generation, SHA256 integrity verification, and RunCache state execution."""
import hashlib
from pathlib import Path
from typing import Optional, Union

from .diagnostics import _create_error_response
from .run_cache import get_cache
from .self_mod_safety import _commit_or_defer_transaction
from .transaction import FileTransaction
from .workspace import Workspace


def _verify_dry_run_hashes(files: list[dict]) -> Optional[dict]:
    """Verify that all target files are unchanged since the dry-run."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    for f in files:
        target_path: Path = f["target_path"]
        original_hash: str = f["original_hash"]
        expected_exists: Optional[bool] = f.get("exists")
        if not target_path.exists():
            if expected_exists is False or (expected_exists is None and original_hash == empty_hash):
                continue
            return {"error": f"File '{target_path.name}' does not exist but was expected to exist based on dry-run."}
        if expected_exists is False:
            return {"error": f"File '{target_path.name}' was created after dry-run (was expected to not exist)."}
        try:
            with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as file_handle:
                current_text = file_handle.read()
        except Exception as e:
            return {"error": f"Cannot read '{target_path}' for hash check: {e}"}
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
    """Commit the patch cached under run_id from a previous dry-run call."""
    from .patch_file import _resolve_workspace_root

    cache = get_cache()
    entry = cache.consume(run_id)

    if entry is None:
        return _create_error_response(f"run_id '{run_id}' not found or expired. Re-run with dry_run=true to get a fresh run_id.")

    files = entry["files"]

    error_response = _verify_dry_run_hashes(files)
    if error_response:
        return _create_error_response(error_response)

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
        if f.get("warnings"):
            res["warnings"] = f["warnings"]
            res["suggestion"] = f.get("suggestion", "")
        return res
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
    from .patch_file import patch_file
    return patch_file(files=patches, dry_run=dry_run, storage_path=storage_path, **kwargs)
