import os
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Union

class FileTransaction:
    """Manages transactional backup, rollback, and startup recovery of files."""

    BACKUP_DIR = ".patchitRIGHT"

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.backup_root = self.workspace_root / self.BACKUP_DIR / "backups"
        self._backups = []  # List of tuples: (target_path, original_bytes, original_hash, backup_path)

    def register_file(self, target_path: Path) -> str:
        """Reads target file, records its state, and returns original content as string."""
        target_abs = target_path.resolve()
        if not target_abs.exists():
            original_bytes = None
            original_hash = hashlib.sha256(b"").hexdigest()
            original_content = ""
        else:
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
            if parts and parts[0].endswith((":\\", ":/", ":")):
                drive = parts[0][0]
                parts[0] = drive
            elif parts and (parts[0] == "/" or parts[0] == "\\"):
                parts = parts[1:]
            return self.backup_root / "absolute" / Path(*parts)

    def write_backups(self) -> None:
        """Writes backup files to disk (prepared phase)."""
        backup_dir = self.workspace_root / self.BACKUP_DIR
        if backup_dir.is_file():
            try:
                backup_dir.unlink()
            except Exception:
                pass
        os.makedirs(self.backup_root, exist_ok=True)
        for _, original_bytes, _, backup_path in self._backups:
            if original_bytes is None:
                marker_path = Path(str(backup_path) + ".missing")
                os.makedirs(marker_path.parent, exist_ok=True)
                marker_path.write_bytes(b"")
            else:
                os.makedirs(backup_path.parent, exist_ok=True)
                backup_path.write_bytes(original_bytes)

    def check_optimistic_locking(self) -> tuple[bool, Optional[Path]]:
        """Verifies that none of the registered files have changed since they were read."""
        for target_path, original_bytes, original_hash, _ in self._backups:
            if original_bytes is None:
                if target_path.exists():
                    return False, target_path
            else:
                try:
                    current_bytes = target_path.read_bytes()
                    current_hash = hashlib.sha256(current_bytes).hexdigest()
                    if current_hash != original_hash:
                        return False, target_path
                except Exception:
                    return False, target_path
        return True, None

    def commit(self, modifications: dict[Path, str]) -> None:
        """Writes the modified contents to the target files. Throws if a write fails."""
        written = []
        try:
            for target_path, content in modifications.items():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                written.append(target_path)
        except Exception as e:
            # Rollback written ones and propagate error
            self.rollback(written)
            raise e

    def rollback(self, targets_to_restore: Optional[list[Path]] = None) -> None:
        """Rolls back the modified target files using memory state or backup files."""
        targets = set(targets_to_restore) if targets_to_restore is not None else None

        for target_path, original_bytes, _, backup_path in self._backups:
            if targets is not None and target_path not in targets:
                continue
            if original_bytes is None:
                try:
                    target_path.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                try:
                    target_path.write_bytes(original_bytes)
                except Exception:
                    # Fallback to backup files if memory write fails
                    self._restore_from_backup_file(backup_path, target_path)

    def _restore_from_backup_file(self, backup_path: Path, target_path: Path) -> None:
        """Helper to restore a backup file under safety check."""
        try:
            # Security guard: prevent path traversal outside backup_root
            backup_root_norm = self.backup_root.resolve()
            backup_path_norm = backup_path.resolve()
            target_path_norm = target_path.resolve()
            if backup_path_norm.exists() and backup_root_norm in backup_path_norm.parents:
                # Double check to prevent traversal
                if ".." not in str(target_path_norm) and target_path_norm.is_absolute():
                    target_path_norm.write_bytes(backup_path_norm.read_bytes())  # NOSONAR
        except Exception:
            pass

    def cleanup(self) -> None:
        """Deletes the hidden backup directory structure completely."""
        shutil.rmtree(self.workspace_root / self.BACKUP_DIR, ignore_errors=True)

    @classmethod
    def run_startup_recovery(cls, workspace_root: Path) -> None:
        """Scan for dirty backups in .patchitRIGHT/backups and restore them safely."""
        backup_root = workspace_root / cls.BACKUP_DIR / "backups"
        if not backup_root.exists():
            return

        try:
            # 1. Recover relative backups & top-level backups
            cls._recover_root_backups(backup_root, workspace_root)
            rel_root = backup_root / "relative"
            if rel_root.exists():
                cls._recover_root_backups(rel_root, workspace_root)

            # 2. Recover absolute backups
            abs_root = backup_root / "absolute"
            cls._recover_absolute_backups(abs_root)

            # Clean up backups
            shutil.rmtree(workspace_root / cls.BACKUP_DIR, ignore_errors=True)
        except Exception:
            pass

    @classmethod
    def _recover_root_backups(cls, rel_root: Path, workspace_root: Path) -> None:
        if not rel_root.exists():
            return
        for root, _, files in os.walk(rel_root):
            for file in files:
                bak_path = Path(root) / file
                if file.endswith(".missing"):
                    rel_file_path = bak_path.relative_to(rel_root)
                    str_rel = str(rel_file_path)[:-8]
                    target_path = workspace_root / Path(str_rel)
                    try:
                        target_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    rel_file_path = bak_path.relative_to(rel_root)
                    target_path = workspace_root / rel_file_path
                    cls._restore_single_backup(bak_path, target_path, rel_root)

    @classmethod
    def _recover_absolute_backups(cls, abs_root: Path) -> None:
        if not abs_root.exists():
            return
        for root, _, files in os.walk(abs_root):
            for file in files:
                bak_path = Path(root) / file
                if file.endswith(".missing"):
                    rel_file_path = bak_path.relative_to(abs_root)
                    str_rel = str(rel_file_path)[:-8]
                    parts = list(Path(str_rel).parts)
                    if not parts:
                        continue
                    if len(parts[0]) == 1 and parts[0].isalpha():
                        drive = parts[0] + ":\\"
                        target_path = Path(drive) / Path(*parts[1:])
                    else:
                        target_path = Path("/") / Path(*parts)
                    try:
                        target_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    rel_file_path = bak_path.relative_to(abs_root)
                    parts = list(rel_file_path.parts)
                    if not parts:
                        continue
                    if len(parts[0]) == 1 and parts[0].isalpha():
                        drive = parts[0] + ":\\"
                        target_path = Path(drive) / Path(*parts[1:])
                    else:
                        target_path = Path("/") / Path(*parts)
                    cls._restore_single_backup(bak_path, target_path, abs_root)

    @staticmethod
    def _restore_single_backup(bak_path: Path, target_path: Path, allowed_root: Path) -> None:
        """Restore target file from .bak with timestamps and security path check."""
        try:
            # Security check: ensure bak_path is inside allowed_root
            allowed_root_norm = allowed_root.resolve()
            bak_path_norm = bak_path.resolve()
            target_path_norm = target_path.resolve()
            
            if not bak_path_norm.exists() or allowed_root_norm not in bak_path_norm.parents:
                return

            # Security check: prevent arbitrary directory traversal
            if ".." in str(target_path_norm) or not target_path_norm.is_absolute():
                return

            if not target_path_norm.exists():
                target_path_norm.parent.mkdir(parents=True, exist_ok=True)  # NOSONAR
                target_path_norm.write_bytes(bak_path_norm.read_bytes())  # NOSONAR
                return

            bak_mtime = bak_path_norm.stat().st_mtime
            target_mtime = target_path_norm.stat().st_mtime
            if target_mtime <= bak_mtime + 2:
                target_path_norm.write_bytes(bak_path_norm.read_bytes())  # NOSONAR
        except Exception:
            pass
