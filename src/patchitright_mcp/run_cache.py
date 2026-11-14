"""In-memory cache for dry-run patch results.

Stores computed patches keyed by a short random run_id so that
`apply_last_dry_run` can commit the result without the caller
having to resend the full payload.

Each entry is single-use (consumed on apply) and expires after `ttl` seconds.
There is no persistence across server restarts — this is intentional.
"""

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


def compute_content_hash(content: Union[str, bytes]) -> str:
    """Compute SHA-256 hash, normalizing text line endings to LF."""
    if isinstance(content, bytes):
        return hashlib.sha256(content).hexdigest()
    norm = content.replace("\r\n", "\n").replace("\r", "")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class _CacheEntry:
    files: list[dict]       # [{target_path, patched_content, original_hash}]
    created_at: float = field(default_factory=time.monotonic)


class RunCache:
    """Thread-safe in-memory store for dry-run patch results.

    Parameters
    ----------
    ttl:
        Seconds before an entry is considered expired. Default 300.
    """

    TTL_DEFAULT = 300

    def __init__(self, ttl: int = TTL_DEFAULT) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._store: dict[str, _CacheEntry] = {}

    def get_ttl(self) -> int:
        """Return the TTL config for cached items."""
        return self._ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(
        self,
        entries: list[dict],   # [{target_path: Path, patched_content: str}]
        original_contents: dict[str, str],  # {filename_key: original_text}
    ) -> str:
        """Cache patch results and return a unique run_id.

        Parameters
        ----------
        entries:
            List of dicts with keys ``target_path`` (Path) and
            ``patched_content`` (str).
        original_contents:
            Mapping from an arbitrary key (typically the raw target file string)
            to the original file text at the time of the dry-run.  Used to
            compute the ``original_hash`` guard that `consume` exposes.

        Returns
        -------
        str
            A short hex run_id that can be passed to :meth:`consume`.
        """
        run_id = secrets.token_hex(6)  # 12-char hex, plenty for session scope

        enriched: list[dict] = []
        for e in entries:
            target_path: Path = e["target_path"]
            patched_content: Optional[Union[str, bytes]] = e.get("patched_content")
            action = e.get("action") or ("delete" if patched_content is None else "write")
            is_binary = bool(e.get("is_binary", isinstance(patched_content, bytes)))

            # Find the original content for this file (match by full path, target_file, or name fallback)
            tf_key = e.get("target_file")
            if str(target_path) in original_contents:
                original_data: Union[str, bytes] = original_contents[str(target_path)]
            elif tf_key and tf_key in original_contents:
                original_data = original_contents[tf_key]
            else:
                original_data = original_contents.get(target_path.name, "")

            if isinstance(original_data, bytes):
                is_binary = True
            original_hash = compute_content_hash(original_data)

            enriched.append({
                "target_path": target_path,
                "patched_content": patched_content,
                "action": action,
                "is_binary": is_binary,
                "original_hash": original_hash,
                "exists": bool(e.get("exists", target_path.exists())),
                "target_file": tf_key,
                "occurrences": e.get("occurrences", 0),
                "relocated_range": e.get("relocated_range"),
                "did_you_mean_info": e.get("did_you_mean_info"),
                "symbol_warnings": e.get("symbol_warnings", []),
                "warnings": e.get("warnings", []),
                "suggestion": e.get("suggestion", ""),
            })

        with self._lock:
            self._store[run_id] = _CacheEntry(files=enriched)

        return run_id

    def consume(self, run_id: str) -> Optional[dict]:
        """Pop and return the cached entry for *run_id*, or None if missing/expired.

        The entry is removed on first access (single-use).

        Returns
        -------
        dict | None
            ``{"files": [{target_path, patched_content, original_hash}]}``
            or ``None`` when the run_id is unknown or has expired.
        """
        with self._lock:
            entry = self._store.pop(run_id, None)

        if entry is None:
            return None

        if time.monotonic() - entry.created_at > self._ttl:
            return None

        return {"files": entry.files}


# Module-level singleton shared across the server process.
_global_cache = RunCache()


def get_cache() -> RunCache:
    """Return the process-global RunCache instance."""
    return _global_cache
