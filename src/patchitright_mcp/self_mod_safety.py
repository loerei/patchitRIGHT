"""Self-modification safety, delayed background writers, and jcodemunch background sync."""
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Union
from .transaction import FileTransaction

logger = logging.getLogger(__name__)
_SHUTDOWN_EVENT = threading.Event()


def get_commit_delay() -> float:
    """Get configurable self-modification commit delay in seconds (default 0.5s)."""
    env_delay = os.environ.get("PATCHITRIGHT_COMMIT_DELAY")
    if env_delay is not None:
        try:
            return max(0.0, float(env_delay))
        except ValueError:
            logger.info("Invalid PATCHITRIGHT_COMMIT_DELAY value %r; falling back to default 0.5s", env_delay)
    return 0.5


def set_shutdown_event() -> None:
    """Signal background worker threads to cancel pending operations on server shutdown."""
    _SHUTDOWN_EVENT.set()


def trigger_jcodemunch_sync(file_paths: Union[Path, list[Path]], storage_path: Optional[str] = None) -> None:
    """Trigger jcodemunch file index update using direct python import or subprocess fallback."""
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
                    from jcodemunch_mcp.tools.index_file import index_file as jm_index_file
                    jm_index_file(path=abs_path, use_ai_summaries=False, storage_path=storage_path)
                except ImportError:
                    import subprocess
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
                    )
            except Exception as e:
                print(f"[PATCHITRIGHT] Warning: Failed to trigger jcodemunch sync for {path}: {e}", file=sys.stderr)

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception as e:
        print(f"[PATCHITRIGHT] Warning: Failed to spawn jcodemunch sync thread: {e}", file=sys.stderr)


def _get_linter_suggestion(target_file: str) -> str:
    """Get formatting/linter recommendation based on target file extension."""
    suffix = Path(target_file).suffix.lower()
    if suffix in (".js", ".ts", ".jsx", ".tsx", ".json"):
        return "You can run `npx --offline @biomejs/biome check --write` on this file to automatically fix lint/format warnings."
    elif suffix == ".py":
        return "You can run `ruff check --fix` on this file to automatically fix lint warnings."
    return ""


def _write_file_with_delay(path: Path, content: str, delay: Optional[float] = None) -> None:
    """Write content to path after a short delay on a background thread."""
    actual_delay = delay if delay is not None else get_commit_delay()

    def worker():
        if actual_delay > 0 and _SHUTDOWN_EVENT.wait(actual_delay):
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            trigger_jcodemunch_sync(path)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception as e:
        print(f"[PATCHITRIGHT] Warning: Failed to spawn jcodemunch sync thread: {e}", file=sys.stderr)


def _write_patched_file(target_path: Path, content: str) -> None:
    """Write patched content to target_path, delaying if it's a self-modification."""
    try:
        is_self_mod = target_path.resolve().is_relative_to(Path(__file__).parent.resolve())
    except Exception:
        is_self_mod = False

    if is_self_mod:
        _write_file_with_delay(target_path, content)
    else:
        with open(target_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        trigger_jcodemunch_sync(target_path)


def _commit_transaction_with_delay(transaction: FileTransaction, modifications: dict[Path, str], delay: Optional[float] = None) -> None:
    """Commit transaction after a short delay on a background thread for self-modification."""
    actual_delay = delay if delay is not None else get_commit_delay()

    def worker():
        if actual_delay > 0 and _SHUTDOWN_EVENT.wait(actual_delay):
            return
        try:
            transaction.commit(modifications)
            transaction.cleanup()
            for target_path in modifications.keys():
                trigger_jcodemunch_sync(target_path)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass


def _commit_or_defer_transaction(transaction: FileTransaction, modifications: dict[Path, str]) -> Optional[dict]:
    """Commit transaction atomically or schedule deferred commit for self-modifications."""
    is_self_mod = False
    try:
        self_dir = Path(__file__).parent.resolve()
        is_self_mod = any(p.resolve().is_relative_to(self_dir) for p in modifications.keys())
    except Exception:
        is_self_mod = False

    if is_self_mod:
        _commit_transaction_with_delay(transaction, modifications)
    else:
        try:
            transaction.commit(modifications)
            transaction.cleanup()
            for p in modifications.keys():
                trigger_jcodemunch_sync(p)
        except Exception as e:
            return {"error": f"Failed to commit transaction: {e}"}
    return None


def run_startup_recovery(workspace_root: Optional[Path] = None, workspace_path: Optional[Path] = None) -> None:
    """Run transaction recovery logic upon startup."""
    ws = workspace_root or workspace_path or Path.cwd().resolve()
    FileTransaction.run_startup_recovery(ws)
