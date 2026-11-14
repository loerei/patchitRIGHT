"""Dry-run preview generation, SHA256 integrity verification, and RunCache state execution."""
import hashlib
from pathlib import Path
from typing import Optional

from .diagnostics import _create_error_response
from .run_cache import compute_content_hash, get_cache
from .transaction import FileTransaction
from .tx_executor import execute_file_plan, resolve_common_workspace_root


def _verify_dry_run_hashes(files: list[dict]) -> Optional[dict]:
    """Verify that all target files are unchanged since the dry-run."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    for f in files:
        target_path: Path = f["target_path"]
        original_hash: str = f["original_hash"]
        expected_exists: Optional[bool] = f.get("exists")
        is_binary = bool(f.get("is_binary", False))
        if not target_path.exists():
            if expected_exists is False or (expected_exists is None and original_hash == empty_hash):
                continue
            return {"error": f"File '{target_path.name}' does not exist but was expected to exist based on dry-run."}
        if expected_exists is False:
            return {"error": f"File '{target_path.name}' was created after dry-run (was expected to not exist)."}
        try:
            if is_binary:
                current_data = target_path.read_bytes()
            else:
                with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as file_handle:
                    current_data = file_handle.read()
        except Exception as e:
            return {"error": f"Cannot read '{target_path}' for hash check: {e}"}
        current_hash = compute_content_hash(current_data)
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
    cache = get_cache()
    entry = cache.consume(run_id)

    if entry is None:
        return _create_error_response(f"run_id '{run_id}' not found or expired. Re-run with dry_run=true to get a fresh run_id.")

    files = entry["files"]

    error_response = _verify_dry_run_hashes(files)
    if error_response:
        return _create_error_response(error_response)

    cwd = Path.cwd().resolve()
    target_paths = [f["target_path"] for f in files]
    workspace_root = resolve_common_workspace_root(target_paths, cwd)

    transaction = FileTransaction(workspace_root)
    modifications: dict[Path, Optional[str | bytes]] = {}
    processed_files: list[dict] = []

    for f in files:
        target_path: Path = f["target_path"]
        action = f.get("action", "write")
        patched_content = f.get("patched_content")
        transaction.register_file(target_path)
        modifications[target_path] = None if action == "delete" else patched_content
        processed_files.append({
            "target_file": f.get("target_file") or str(target_path),
            "target_path": target_path,
            "patched_content": patched_content,
            "action": action,
            "is_binary": bool(f.get("is_binary", False)),
            "occurrences": f.get("occurrences", 1),
            "relocated_range": f.get("relocated_range"),
            "did_you_mean_info": f.get("did_you_mean_info"),
            "warnings": f.get("warnings", []),
            "suggestion": f.get("suggestion", ""),
        })

    return execute_file_plan(
        processed_files=processed_files,
        transaction=transaction,
        original_contents={},
        modifications=modifications,
        dry_run=False,
        multi_file_mode=(len(files) > 1),
        run_id_applied=run_id,
    )


def batch_patch_files(
    patches: list[dict],
    dry_run: bool = False,
    storage_path: Optional[str] = None,
    **kwargs
) -> dict:
    """Backward-compatible wrapper delegating batch_patch_files to patch_file(files=patches)."""
    from .patch_file import patch_file
    return patch_file(files=patches, dry_run=dry_run, storage_path=storage_path, **kwargs)
