import functools
import os
from pathlib import Path
from typing import Optional


@functools.lru_cache(maxsize=256)
def _cached_resolve_allowed_base_dir(cwd_str: str, target_file_norm: str, storage_path: Optional[str] = None) -> str:
    """Module-level LRU cached helper for resolving allowed base directory string."""
    try:
        from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
        temp_resolved = os.path.abspath(os.path.join(cwd_str, target_file_norm))
        repo_res = resolve_repo_fn(temp_resolved, storage_path)
        if repo_res.get("found") and "source_root" in repo_res:
            return os.path.abspath(repo_res["source_root"])
    except Exception:
        pass
    return cwd_str


def clear_workspace_cache() -> None:
    """Clear the workspace base directory LRU cache."""
    _cached_resolve_allowed_base_dir.cache_clear()


class Workspace:
    """Manages path safe-resolution, context mismatch checks, and root directory discovery."""

    def __init__(self, cwd: Path, storage_path: Optional[str] = None):
        self.cwd = cwd.resolve()
        self.storage_path = storage_path

    @property
    def base_dir(self) -> Path:
        return Path(self.storage_path).resolve() if self.storage_path else self.cwd

    def resolve_allowed_base_dir(self, target_file: str) -> Path:
        """Resolve the allowed base directory, using indexed repo source_root if available (LRU cached)."""
        cwd_str = str(self.cwd)
        tf_norm = os.path.normpath(target_file)
        resolved_str = _cached_resolve_allowed_base_dir(cwd_str, tf_norm, self.storage_path)
        return Path(resolved_str)

    def resolve_safe_path(self, target_file: str) -> Path:
        """Resolve the target path and check context mismatch constraint for relative paths."""
        # Validate input to prevent path traversal vulnerability (pythonsecurity:S2083)
        if ".." in target_file or "/../" in target_file or "\\..\\" in target_file:
            raise ValueError("fatal_context_mismatch")

        base_dir = self.resolve_allowed_base_dir(target_file)
        
        # Resolve all symlinks and directory traversal sequences first to get the canonical path
        resolved_path = Path(os.path.realpath(os.path.join(base_dir, target_file)))

        # Guard relative paths from escaping the active workspace
        if not os.path.isabs(target_file):
            try:
                # Windows path case-insensitivity relative-to checks can be touchy.
                # Use normcase to compare normalized path structures.
                resolved_norm = Path(os.path.normcase(str(resolved_path)))
                base_norm = Path(os.path.normcase(str(base_dir)))
                resolved_norm.relative_to(base_norm)
            except ValueError:
                # Fallback checking string-based starts
                base_str = str(base_dir) + os.sep
                res_str = str(resolved_path)
                if not res_str.startswith(base_str) and res_str != str(base_dir):
                    raise ValueError("fatal_context_mismatch")
        return resolved_path

    def find_workspace_root(self, path: Path) -> Path:
        """Walk up to locate a project root using common anchor files."""
        current = path.resolve()
        if current.is_file() or not current.exists():
            current = current.parent
        anchors = {".git", ".gitignore", "pyproject.toml", "package.json", "go.mod", "cargo.toml", ".patchitRIGHT"}
        search_parents = [current]
        for p in current.parents:
            if p == self.cwd or p == p.parent:
                search_parents.append(p)
                break
            if p.name.lower() in ("temp", "tmp", "users") or p == Path.home():
                break
            search_parents.append(p)
        for parent in search_parents:
            if any((parent / anchor).exists() for anchor in anchors):
                return parent
        return current
