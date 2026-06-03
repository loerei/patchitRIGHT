import os
from pathlib import Path
from typing import Optional

class Workspace:
    """Manages path safe-resolution, context mismatch checks, and root directory discovery."""

    def __init__(self, cwd: Path, storage_path: Optional[str] = None):
        self.cwd = cwd.resolve()
        self.storage_path = storage_path

    def resolve_allowed_base_dir(self, target_file: str) -> Path:
        """Resolve the allowed base directory, using indexed repo source_root if available."""
        base_dir = self.cwd
        try:
            from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
            temp_resolved = os.path.abspath(os.path.join(base_dir, target_file))
            repo_res = resolve_repo_fn(temp_resolved, self.storage_path)
            if repo_res.get("found") and "source_root" in repo_res:
                return Path(os.path.abspath(repo_res["source_root"]))
        except Exception:
            pass
        return base_dir

    def resolve_safe_path(self, target_file: str) -> Path:
        """Resolve the target path and check context mismatch constraint for relative paths."""
        base_dir = self.resolve_allowed_base_dir(target_file)
        resolved_path = Path(os.path.abspath(os.path.join(base_dir, target_file)))

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
        if current.is_file():
            current = current.parent
        anchors = {".git", ".gitignore", "pyproject.toml", "package.json", "go.mod", "cargo.toml", ".patchitRIGHT"}
        for parent in [current] + list(current.parents):
            if any((parent / anchor).exists() for anchor in anchors):
                return parent
        return current
