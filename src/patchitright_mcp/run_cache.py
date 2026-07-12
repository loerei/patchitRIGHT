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
from typing import Optional


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
            patched_content: str = e["patched_content"]

            # Find the original content for this file (match by path name as fallback)
            original_text = original_contents.get(
                str(target_path),
                original_contents.get(target_path.name, ""),
            )
            # Normalize newlines to LF for robust hash comparison
            norm_original = original_text.replace("\r\n", "\n").replace("\r", "")
            original_hash = hashlib.sha256(norm_original.encode()).hexdigest()

            enriched.append({
                "target_path": target_path,
                "patched_content": patched_content,
                "original_hash": original_hash,
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
