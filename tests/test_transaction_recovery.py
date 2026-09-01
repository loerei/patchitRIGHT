"""Unit tests for transaction rollback, crash recovery, and self-modification safety."""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
import pytest

from patchitright_mcp.transaction import FileTransaction
from patchitright_mcp.self_mod_safety import (
    get_commit_delay,
    set_shutdown_event,
    trigger_jcodemunch_sync,
    _get_linter_suggestion,
    _write_file_with_delay,
    _write_patched_file,
    _commit_transaction_with_delay,
    _commit_or_defer_transaction,
    run_startup_recovery,
)


def test_get_backup_path_relative_and_absolute(tmp_path):
    """FileTransaction resolves correct relative and absolute backup paths."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    tx = FileTransaction(ws)

    # 1. Relative path within workspace
    rel_file = ws / "src" / "index.ts"
    rel_bak = tx._get_backup_path(rel_file)
    assert tx.backup_root / "relative" / "src" / "index.ts" == rel_bak

    # 2. Absolute path outside workspace (drive letter)
    ext_file = tmp_path / "external" / "other.ts"
    ext_bak = tx._get_backup_path(ext_file)
    assert "absolute" in ext_bak.parts

    # 3. POSIX-style root path
    posix_path = Path("/etc/config.json")
    posix_bak = tx._get_backup_path(posix_path)
    assert "absolute" in posix_bak.parts


def test_write_backups_cleans_file_collision_and_writes_markers(tmp_path):
    """write_backups cleans up if .patchitRIGHT is a file and writes .missing markers."""
    ws = tmp_path / "ws"
    ws.mkdir()

    # Create .patchitRIGHT as a file to trigger cleanup
    bad_dir = ws / FileTransaction.BACKUP_DIR
    bad_dir.write_text("collision file")

    tx = FileTransaction(ws)
    non_existent = ws / "created_file.txt"
    existing = ws / "existing.txt"
    existing.write_text("orig")

    tx.register_file(non_existent)
    tx.register_file(existing)

    tx.write_backups()
    assert tx.backup_root.exists()

    missing_marker = Path(str(tx._get_backup_path(non_existent)) + FileTransaction.MISSING_MARKER_SUFFIX)
    assert missing_marker.exists()
    assert tx._get_backup_path(existing).read_text() == "orig"


def test_optimistic_locking_scenarios(tmp_path):
    """check_optimistic_locking detects concurrent creation, modification, and file errors."""
    ws = tmp_path / "ws_opt"
    ws.mkdir()
    tx = FileTransaction(ws)

    file_new = ws / "new.txt"
    file_mod = ws / "mod.txt"
    file_mod.write_text("v1")

    tx.register_file(file_new)
    tx.register_file(file_mod)

    # 1. Initial state is valid
    ok, err_path = tx.check_optimistic_locking()
    assert ok is True
    assert err_path is None

    # 2. File created concurrently
    file_new.write_text("created concurrently")
    ok, err_path = tx.check_optimistic_locking()
    assert ok is False
    assert err_path == file_new.resolve()
    file_new.unlink()

    # 3. File modified concurrently
    file_mod.write_text("v2")
    ok, err_path = tx.check_optimistic_locking()
    assert ok is False
    assert err_path == file_mod.resolve()

    # 4. File read error / permission error
    with patch.object(Path, "read_bytes", side_effect=PermissionError("Locked")):
        ok, err_path = tx.check_optimistic_locking()
        assert ok is False
        assert err_path == file_mod.resolve()


def test_commit_failure_triggers_rollback(tmp_path):
    """commit failure on intermediate file rolls back previously written files."""
    ws = tmp_path / "ws_commit"
    ws.mkdir()

    f1 = ws / "f1.txt"
    f2 = ws / "f2.txt"
    f1.write_text("orig1")
    f2.write_text("orig2")

    tx = FileTransaction(ws)
    tx.register_file(f1)
    tx.register_file(f2)
    tx.write_backups()

    with patch("builtins.open", side_effect=[open(f1, "w", encoding="utf-8"), OSError("Disk Full")]):
        with pytest.raises(OSError):
            tx.commit({f1: "new1", f2: "new2"})

    # Check that f1 was rolled back to orig1
    assert f1.read_text() == "orig1"


def test_rollback_with_target_filter_and_backup_restore(tmp_path):
    """rollback handles target filters, unlinking created files, and backup fallback."""
    ws = tmp_path / "ws_rb"
    ws.mkdir()
    f_new = ws / "new.txt"
    f_exist = ws / "exist.txt"
    f_exist.write_text("original")

    tx = FileTransaction(ws)
    tx.register_file(f_new)
    tx.register_file(f_exist)
    tx.write_backups()

    f_new.write_text("dirty new")
    f_exist.write_text("dirty exist")

    # 1. Rollback only f_new
    tx.rollback(targets_to_restore=[f_new.resolve()])
    assert not f_new.exists()
    assert f_exist.read_text() == "dirty exist"

    # 2. Rollback f_exist
    tx.rollback(targets_to_restore=[f_exist.resolve()])
    assert f_exist.read_text() == "original"

    # 3. Test _restore_from_backup_file directly
    f_exist.write_text("corrupted again")
    backup_path = tx._get_backup_path(f_exist)
    tx._restore_from_backup_file(backup_path, f_exist)
    assert f_exist.read_text() == "original"


def test_restore_single_backup_security_guards(tmp_path):
    """_restore_single_backup guards against path traversal and relative paths."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    bak = allowed / "file.bak"
    bak.write_text("backup")

    # 1. Target with traversal .. is rejected
    traversal_target = Path(str(allowed / ".." / "evil.txt"))
    FileTransaction._restore_single_backup(bak, traversal_target, allowed)

    # 2. Bak outside allowed_root is rejected
    outside_bak = outside / "leak.bak"
    outside_bak.write_text("leaked")
    target = allowed / "safe.txt"
    FileTransaction._restore_single_backup(outside_bak, target, allowed)
    assert not target.exists()

    # 3. Valid restore when target does not exist
    FileTransaction._restore_single_backup(bak, target, allowed)
    assert target.read_text() == "backup"


def test_run_startup_recovery_relative_and_absolute(tmp_path):
    """run_startup_recovery cleans dirty relative and absolute backups and missing markers."""
    ws = tmp_path / "ws_rec"
    ws.mkdir()

    # 1. Relative backup recovery
    rel_bak_dir = ws / FileTransaction.BACKUP_DIR / "backups" / "relative" / "pkg"
    rel_bak_dir.mkdir(parents=True)
    bak_file = rel_bak_dir / "mod.ts"
    bak_file.write_text("recovered mod")

    # Missing marker for file that shouldn't exist
    missing_marker = rel_bak_dir / ("deleted.ts" + FileTransaction.MISSING_MARKER_SUFFIX)
    missing_marker.write_bytes(b"")

    target_deleted = ws / "pkg" / "deleted.ts"
    target_deleted.parent.mkdir(parents=True, exist_ok=True)
    target_deleted.write_text("should be deleted")

    # Set timestamps so bak_file is newer or equal
    now = time.time()
    os.utime(bak_file, (now, now))

    # 2. Absolute backup recovery
    abs_bak_dir = ws / FileTransaction.BACKUP_DIR / "backups" / "absolute"
    drive_dir = abs_bak_dir / "C" / "test_abs"
    drive_dir.mkdir(parents=True)
    abs_bak_file = drive_dir / "abs_mod.ts"
    abs_bak_file.write_text("recovered abs mod")
    os.utime(abs_bak_file, (now, now))

    abs_missing = drive_dir / ("abs_del.ts" + FileTransaction.MISSING_MARKER_SUFFIX)
    abs_missing.write_bytes(b"")

    with patch.object(FileTransaction, "_restore_single_backup") as mock_restore:
        FileTransaction.run_startup_recovery(ws)
        assert mock_restore.called

    assert not (ws / FileTransaction.BACKUP_DIR).exists()


def test_self_mod_safety_helpers(monkeypatch):
    """Tests for commit delay, shutdown event, and linter recommendations."""
    # 1. Commit delay parsing
    monkeypatch.delenv("PATCHITRIGHT_COMMIT_DELAY", raising=False)
    assert get_commit_delay() == 0.5

    monkeypatch.setenv("PATCHITRIGHT_COMMIT_DELAY", "1.8")
    assert get_commit_delay() == 1.8

    monkeypatch.setenv("PATCHITRIGHT_COMMIT_DELAY", "not_a_float")
    assert get_commit_delay() == 0.5

    # 2. Linter suggestions
    assert "biome" in _get_linter_suggestion("app.ts")
    assert "ruff" in _get_linter_suggestion("server.py")
    assert _get_linter_suggestion("doc.md") == ""

    # 3. Shutdown event
    set_shutdown_event()


def test_trigger_jcodemunch_sync_subprocesses(monkeypatch, tmp_path):
    """trigger_jcodemunch_sync handles direct import, subprocess fallback, and error handling."""
    # 1. Disabled by default
    monkeypatch.delenv("PATCHITRIGHT_SYNC_JCODEMUNCH", raising=False)
    with patch("threading.Thread") as mock_thread:
        trigger_jcodemunch_sync(tmp_path / "a.py")
        mock_thread.assert_not_called()

    # 2. Enabled with direct import
    monkeypatch.setenv("PATCHITRIGHT_SYNC_JCODEMUNCH", "true")
    with patch("jcodemunch_mcp.tools.index_file.index_file"):
        trigger_jcodemunch_sync(tmp_path / "b.py", storage_path="db.sqlite")
        time.sleep(0.05)

    # 3. Fallback to subprocess when ImportError
    with patch.dict(sys.modules, {"jcodemunch_mcp.tools.index_file": None}):
        with patch("subprocess.run") as mock_subproc:
            trigger_jcodemunch_sync(tmp_path / "c.py", storage_path="db.sqlite")
            time.sleep(0.05)
            assert mock_subproc.called


def test_delayed_writers_and_self_mod(tmp_path, monkeypatch):
    """_write_file_with_delay, _write_patched_file, and _commit_transaction_with_delay execute in background."""
    from patchitright_mcp.self_mod_safety import _SHUTDOWN_EVENT
    _SHUTDOWN_EVENT.clear()
    monkeypatch.setenv("PATCHITRIGHT_COMMIT_DELAY", "0.01")
    f = tmp_path / "delayed.txt"

    # 1. _write_file_with_delay
    _write_file_with_delay(f, "delayed content", delay=0.01)
    time.sleep(0.08)
    assert f.read_text() == "delayed content"

    # 2. _write_patched_file for self-mod vs external
    from patchitright_mcp import __file__ as pkg_file
    pkg_dir = Path(pkg_file).parent.resolve()
    self_file = pkg_dir / "scratch_self.py"
    try:
        _write_patched_file(self_file, "# self mod test")
        time.sleep(0.08)
        assert self_file.read_text() == "# self mod test"
    finally:
        self_file.unlink(missing_ok=True)

    # 3. _commit_transaction_with_delay
    tx = FileTransaction(tmp_path)
    tx.register_file(f)
    tx.write_backups()
    _commit_transaction_with_delay(tx, {f: "committed delayed"}, delay=0.01)
    time.sleep(0.08)
    assert f.read_text() == "committed delayed"

    # 4. _commit_or_defer_transaction on self_mod
    tx2 = FileTransaction(tmp_path)
    tx2.register_file(self_file)
    tx2.write_backups()
    err = _commit_or_defer_transaction(tx2, {self_file: "# self mod defer"})
    assert err is None
    time.sleep(0.08)
    assert self_file.read_text() == "# self mod defer"
    self_file.unlink(missing_ok=True)


def test_absolute_backup_posix_and_shutdown_cancellation(tmp_path):
    """Test absolute backups with POSIX paths and shutdown event early cancellation."""
    from patchitright_mcp.self_mod_safety import _SHUTDOWN_EVENT, _write_file_with_delay, _commit_transaction_with_delay

    ws = tmp_path / "ws_posix"
    ws.mkdir()

    # 1. Absolute backup with POSIX-style path (not a single letter drive)
    abs_bak_dir = ws / FileTransaction.BACKUP_DIR / "backups" / "absolute" / "tmp" / "test_posix"
    abs_bak_dir.mkdir(parents=True)
    posix_bak_file = abs_bak_dir / "file.txt"
    posix_bak_file.write_text("posix content")
    now = time.time()
    os.utime(posix_bak_file, (now, now))

    posix_missing = abs_bak_dir / ("del.txt" + FileTransaction.MISSING_MARKER_SUFFIX)
    posix_missing.write_bytes(b"")

    with patch.object(FileTransaction, "_restore_single_backup") as mock_restore:
        FileTransaction.run_startup_recovery(ws)
        assert mock_restore.called

    # 2. Early shutdown cancellation in delayed writers
    _SHUTDOWN_EVENT.set()
    f_cancel = tmp_path / "cancelled.txt"
    _write_file_with_delay(f_cancel, "should not be written", delay=1.0)
    time.sleep(0.05)
    assert not f_cancel.exists()

    tx = FileTransaction(tmp_path)
    tx.register_file(f_cancel)
    tx.write_backups()
    _commit_transaction_with_delay(tx, {f_cancel: "should not be written"}, delay=1.0)
    time.sleep(0.05)
    assert not f_cancel.exists()

    _SHUTDOWN_EVENT.clear()


def test_run_startup_recovery_wrapper(tmp_path):
    """run_startup_recovery wrapper accepts workspace_root or workspace_path."""
    ws = tmp_path / "ws_wrap"
    ws.mkdir()
    run_startup_recovery(workspace_root=ws)
    run_startup_recovery(workspace_path=ws)
