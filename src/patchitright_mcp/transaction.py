import os
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Union

class FileTransaction:
    """Manages transactional backup, rollback, and startup recovery of files."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.backup_root = self.workspace_root / ".patchitRIGHT" / "backups"
        self._backups = []  # List of tuples: (target_path, original_bytes, original_hash, backup_path)

    def register_file(self, target_path: Path) -> str:
        """Reads target file, records its state, and returns original content as string."""
        target_abs = target_path.resolve()
        original_bytes = target_abs.read_bytes()
        original_hash = hashlib.sha256(original_bytes).hexdigest()
        original_content = original_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n")

        backup_path = self._get_backup_path(target_abs)
        self._backups.append((target_abs, original_bytes, original_hash, backup_path))

        return original_content

    def _get_backup_path(self, target_path: Path) -> Path:
        """Resolve a safe, collision-free backup path inside .patchitRIGHT/backups/."""
        target_abs = target_path.resolve()
        try:
            target_norm = Path(os.path.normcase(str(target_abs)))
            base_norm = Path(os.path.normcase(str(self.workspace_root)))
            # Check relative structure
            _ = target_norm.relative_to(base_norm)
            rel_parts = target_abs.parts[len(self.workspace_root.parts):]
            return self.backup_root / "relative" / Path(*rel_parts)
        except ValueError:
            parts = list(target_path.parts)
            if parts and (parts[0].endswith(":\\") or parts[0].endswith(":/") or parts[0].endswith(":")):
                drive = parts[0][0]
                parts[0] = drive
            elif parts and (parts[0] == "/" or parts[0] == "\\"):
                parts = parts[1:]
            return self.backup_root / "absolute" / Path(*parts)

    def write_backups(self) -> None:
        """Writes backup files to disk (prepared phase)."""
        for _, original_bytes, _, backup_path in self._backups:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(original_bytes)

    def check_optimistic_locking(self) -> tuple[bool, Optional[Path]]:
        """Verifies that none of the registered files have changed since they were read."""
        for target_path, _, original_hash, _ in self._backups:
            current_bytes = target_path.read_bytes()
            current_hash = hashlib.sha256(current_bytes).hexdigest()
            if current_hash != original_hash:
                return False, target_path
        return True, None

    def commit(self, modifications: dict[Path, str]) -> None:
        """Writes the modified contents to the target files. Throws if a write fails."""
        written = []
        try:
            for target_path, content in modifications.items():
                target_path.write_text(content, encoding="utf-8")
                written.append(target_path)
        except Exception as e:
            # Rollback written ones and propagate error
            self.rollback(written)
            raise e

    def rollback(self, targets_to_restore: Optional[list[Path]] = None) -> None:
        """Rolls back the modified target files using memory state or backup files."""
        targets = set(targets_to_restore) if targets_to_restore is not None else None

        for target_path, original_bytes, _, backup_path in self._backups:
            if targets is None or target_path in targets:
                try:
                    target_path.write_bytes(original_bytes)
                except Exception:
                    # Fallback to backup files if memory write fails
                    try:
                        if backup_path.exists():
                            target_path.write_bytes(backup_path.read_bytes())
                    except Exception:
                        pass

    def cleanup(self) -> None:
        """Deletes the hidden backup directory structure completely."""
        shutil.rmtree(self.workspace_root / ".patchitRIGHT", ignore_errors=True)

    @classmethod
    def run_startup_recovery(cls, workspace_root: Path) -> None:
        """Scan for dirty backups in .patchitRIGHT/backups and restore them safely."""
        backup_root = workspace_root / ".patchitRIGHT" / "backups"
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
                        target_path = workspace_root / rel_file_path
                        cls._restore_single_backup(bak_path, target_path)

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
                            cls._restore_single_backup(bak_path, target_path)

            # Clean up backups
            shutil.rmtree(workspace_root / ".patchitRIGHT", ignore_errors=True)
        except Exception:
            pass

    @staticmethod
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
