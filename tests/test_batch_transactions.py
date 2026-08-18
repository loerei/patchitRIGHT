"""Tests for batch patching, transactional rollback, and dry-run cache."""

from pathlib import Path
import os
import json
import pytest
from unittest.mock import patch

from patchitright_mcp.patch_file import patch_file, batch_patch_files, run_startup_recovery
from patchitright_mcp.engine import PatchEngine
from patchitright_mcp.dry_run import apply_last_dry_run
from patchitright_mcp.run_cache import get_cache
from jcodemunch_mcp.tools.index_folder import index_folder


class TestBatchTransactions:

    def test_batch_patch_success(self, tmp_path, monkeypatch):
        """batch_patch_files must successfully apply multiple diffs atomically."""
        monkeypatch.chdir(tmp_path)
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text("hello a\n")
        file_b.write_text("hello b\n")

        patches = [
            {"target_file": "a.py", "patch_content": "@@ -1,1 +1,1 @@\n-hello a\n+world a\n"},
            {"target_file": "b.py", "patch_content": "@@ -1,1 +1,1 @@\n-hello b\n+world b\n"}
        ]

        res = batch_patch_files(patches=patches, dry_run=False)
        assert "success" in res
        assert res["success"] is True

        assert file_a.read_text() == "world a\n"
        assert file_b.read_text() == "world b\n"


    def test_batch_patch_rollback_on_failure(self, tmp_path, monkeypatch):
        """batch_patch_files must rollback all changes if any single patch validation fails."""
        monkeypatch.chdir(tmp_path)
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text("hello a\n")
        file_b.write_text("hello b\n")

        patches = [
            {"target_file": "a.py", "patch_content": "@@ -1,1 +1,1 @@\n-hello a\n+world a\n"},
            # File B has wrong context, which triggers validation failure
            {"target_file": "b.py", "patch_content": "@@ -1,1 +1,1 @@\n-wrong context\n+world b\n"}
        ]

        res = batch_patch_files(patches=patches, dry_run=False)
        assert "error" in res
        assert "Validation failed for file b.py" in res["error"]

        # Atomic rollback check: verify BOTH files remain untouched
        assert file_a.read_text() == "hello a\n"
        assert file_b.read_text() == "hello b\n"


    def test_batch_patch_optimistic_locking(self, tmp_path, monkeypatch):
        """batch_patch_files must abort and rollback if an external modification occurs mid-transaction."""
        monkeypatch.chdir(tmp_path)
        file_a = tmp_path / "a.py"
        file_a.write_text("hello a\n")


    def test_batch_patch_auto_recovery(self, tmp_path, monkeypatch):
        """run_startup_recovery must restore target files from .bak if target is unchanged since backup."""
        monkeypatch.chdir(tmp_path)
        file_a = tmp_path / "a.py"
        file_a.write_text("broken content\n")

        # Create a backup file representing the safe old content in the nested backup path
        bak_file = tmp_path / ".patchitRIGHT" / "backups" / "relative" / "a.py"
        bak_file.parent.mkdir(parents=True, exist_ok=True)
        bak_file.write_text("safe content\n")

        # Set modification time of target older or equal to bak to simulate crash state
        import os
        import time
        t = time.time() - 10
        os.utime(file_a, (t, t))
        os.utime(bak_file, (t + 2, t + 2))

        run_startup_recovery(tmp_path)

        assert file_a.read_text() == "safe content\n"
        assert not (tmp_path / ".patchitRIGHT").exists()


    def test_batch_patch_optimistic_locking_conflict(self, tmp_path, monkeypatch):
        """batch_patch_files must abort if the file hash changes before commit."""
        monkeypatch.chdir(tmp_path)
        file_a = tmp_path / "a.py"
        file_a.write_text("hello a\n")

        patches = [
            {"target_file": "a.py", "patch_content": "@@ -1,1 +1,1 @@\n-hello a\n+world a\n"}
        ]

        # Let's mock target_path.read_bytes during step 4 (the locking check)
        # by patching the read_bytes method of Path.
        # We want the first read_bytes (read_text) to succeed normally, but subsequent read_bytes
        # (during hash checking) to return modified bytes to simulate a concurrent write.
        original_read_bytes = Path.read_bytes
        call_count = 0

        def mock_read_bytes(self):
            nonlocal call_count
            # Only trigger drift for a.py
            if self.name == "a.py":
                call_count += 1
                if call_count > 1: # After first read, simulate modification
                    return b"different content\n"
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)

        res = batch_patch_files(patches=patches, dry_run=False)
        assert "error" in res
        assert "Optimistic Locking Conflict" in res["error"]
        assert file_a.read_text() == "hello a\n" # Rolled back / Unmodified


    def test_batch_patch_auto_recovery_skipped_if_newer(self, tmp_path, monkeypatch):
        """run_startup_recovery must NOT restore if target file was modified after the backup."""
        monkeypatch.chdir(tmp_path)
        file_a = tmp_path / "a.py"
        file_a.write_text("user manual edits\n")

        bak_file = tmp_path / ".patchitRIGHT" / "backups" / "relative" / "a.py"
        bak_file.parent.mkdir(parents=True, exist_ok=True)
        bak_file.write_text("safe content\n")

        import os
        import time
        t = time.time()
        # Target file has newer modified time than bak
        os.utime(bak_file, (t - 10, t - 10))
        os.utime(file_a, (t, t))

        run_startup_recovery(tmp_path)

        # Should NOT overwrite, and should delete bak to prevent getting stuck
        assert file_a.read_text() == "user manual edits\n"
        assert not (tmp_path / ".patchitRIGHT").exists()


    def test_body_scope_in_replacements_array(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\nfunction bar() {\n  return 2;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replacements=[
                {"symbol_name": "foo", "symbol_scope": "body", "replace_content": "  return 100;"},
                {"symbol_name": "bar", "symbol_scope": "body", "replace_content": "  return 200;"},
            ],
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        expected = "function foo() {\n  return 100;\n}\nfunction bar() {\n  return 200;\n}\n"
        assert app_file.read_text() == expected


    def test_output_schema_multi_file_and_apply_dry_run(self, tmp_path, monkeypatch):
        """batch_patch_files and apply_last_dry_run must return standardized modified_files schema."""
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "f1.py"
        f2 = tmp_path / "f2.py"
        f1.write_text("a = 1\n")
        f2.write_text("b = 2\n")

        res_dry = patch_file(
            files=[
                {"target_file": "f1.py", "search_content": "a = 1", "replace_content": "a = 10"},
                {"target_file": "f2.py", "search_content": "b = 2", "replace_content": "b = 20"},
            ],
            dry_run=True
        )

        assert res_dry["target_file"] == "f1.py"
        assert res_dry["diff_content"] is None
        assert res_dry["modified_files"] is not None
        assert len(res_dry["modified_files"]) == 2
        assert res_dry["modified_files"][0]["target_file"] == "f1.py"
        assert res_dry["modified_files"][1]["target_file"] == "f2.py"

        # Test apply_last_dry_run
        run_id = res_dry["run_id"]
        res_apply = patch_file(
            target_file=None, # or call apply_last_dry_run directly
            files=None
        )
        from patchitright_mcp.patch_file import apply_last_dry_run
        res_applied = apply_last_dry_run(run_id)
        assert res_applied["success"] is True
        assert res_applied["target_file"] == "f1.py"
        assert res_applied["modified_files"] is not None
        assert f1.read_text() == "a = 10\n"
        assert f2.read_text() == "b = 20\n"


    def test_multi_file_duplicate_target_rejection(self, tmp_path, monkeypatch):
        """patch_file must reject duplicate target_file paths in files array."""
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "file1.py"
        f1.write_text("hello\n")

        res = patch_file(
            files=[
                {"target_file": "file1.py", "search_content": "hello", "replace_content": "world 1"},
                {"target_file": "./file1.py", "search_content": "world 1", "replace_content": "world 2"},
            ]
        )

        assert "error" in res
        assert "Duplicate resolved target_file paths" in res["error"]


    def test_multi_file_batch_patch_file(self, tmp_path, monkeypatch):
        """patch_file with files array must atomically patch multiple files."""
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "file1.py"
        f2 = tmp_path / "file2.py"
        f1.write_text("hello 1\n")
        f2.write_text("hello 2\n")

        res = patch_file(
            files=[
                {"target_file": "file1.py", "search_content": "hello 1", "replace_content": "world 1"},
                {"target_file": "file2.py", "search_content": "hello 2", "replace_content": "world 2"},
            ],
            dry_run=False
        )

        assert res.get("success") is True
        assert f1.read_text() == "world 1\n"
        assert f2.read_text() == "world 2\n"


    def test_multi_file_conflicting_args_rejection(self, tmp_path, monkeypatch):
        """patch_file must reject calls providing both files array and top-level single-file args."""
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "file1.py"
        f1.write_text("hello\n")

        res = patch_file(
            target_file="file1.py",
            files=[{"target_file": "file1.py", "search_content": "hello", "replace_content": "world"}]
        )

        assert "error" in res
        assert "Cannot provide both 'files' array and top-level single-file edit parameters" in res["error"]


    def test_missing_marker_startup_recovery(self, tmp_path, monkeypatch):
        """run_startup_recovery must unlink target paths associated with .missing backup markers."""
        monkeypatch.chdir(tmp_path)
        backup_dir = tmp_path / ".patchitRIGHT" / "backups"
        backup_dir.mkdir(parents=True)

        new_file = tmp_path / "new_created.txt"
        new_file.write_text("should be deleted on recovery\n")

        # Create matching .missing backup marker with recent timestamp
        marker = backup_dir / "new_created.txt.missing"
        marker.write_text("")

        run_startup_recovery(workspace_root=tmp_path)

        assert not new_file.exists()
        assert not marker.exists()



class TestDryRunReturnsRunId:

    def test_patch_file_dry_run_returns_run_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "app.py"
        f.write_text("line 1\nline 2\nline 3\n")

        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=True,
        )

        assert res["success"] is True
        assert res["dryRun"] is True
        assert "run_id" in res
        assert isinstance(res["run_id"], str)
        assert len(res["run_id"]) > 0
        assert "expires_in" in res
        assert isinstance(res["expires_in"], int)
        assert res["expires_in"] > 0


    def test_batch_patch_files_dry_run_returns_run_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("hello\nworld\n")
        f2.write_text("foo\nbar\n")

        diff_a = (
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n-hello\n+HELLO\n"
        )
        diff_b = (
            "--- a/b.py\n+++ b/b.py\n"
            "@@ -1,1 +1,1 @@\n-foo\n+FOO\n"
        )

        res = batch_patch_files(
            patches=[
                {"target_file": "a.py", "patch_content": diff_a},
                {"target_file": "b.py", "patch_content": diff_b},
            ],
            dry_run=True,
        )

        assert res["success"] is True
        assert res["dryRun"] is True
        assert "run_id" in res
        assert "expires_in" in res


    def test_patch_file_non_dry_run_has_no_run_id(self, tmp_path, monkeypatch):
        """run_id must NOT appear when actually applying (no cache pollution)."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "app.py"
        f.write_text("line 1\nline 2\nline 3\n")

        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=False,
        )

        assert res["success"] is True
        assert "run_id" not in res



class TestApplyLastDryRun:

    def test_applies_cached_patch_and_returns_success(self, tmp_path, monkeypatch):
        """Cycle 5 — apply_last_dry_run(run_id) writes the patched file."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "app.py"
        original = "line 1\nline 2\nline 3\n"
        f.write_text(original)

        dry_res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=True,
        )
        run_id = dry_res["run_id"]

        apply_res = apply_last_dry_run(run_id=run_id)

        assert apply_res["success"] is True
        assert apply_res.get("dryRun") is False
        assert f.read_text() == "line 1\nmodified line 2\nline 3\n"


    def test_fails_on_unknown_run_id(self, tmp_path, monkeypatch):
        """Cycle 6 — apply_last_dry_run fails with clear error for unknown run_id."""
        monkeypatch.chdir(tmp_path)
        res = apply_last_dry_run(run_id="totally-fake-id")
        assert "error" in res
        assert "run_id" in res["error"].lower() or "not found" in res["error"].lower() or "unknown" in res["error"].lower() or "expired" in res["error"].lower()


    def test_fails_if_file_changed_after_dry_run(self, tmp_path, monkeypatch):
        """Cycle 7 — apply_last_dry_run rejects apply if file changed since dry-run."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "app.py"
        f.write_text("line 1\nline 2\nline 3\n")

        dry_res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=True,
        )
        run_id = dry_res["run_id"]

        # Mutate the file after the dry-run
        f.write_text("line 1\nSOMETHING CHANGED\nline 3\n")

        apply_res = apply_last_dry_run(run_id=run_id)
        assert "error" in apply_res
        assert "modified" in apply_res["error"].lower() or "changed" in apply_res["error"].lower() or "conflict" in apply_res["error"].lower() or "hash" in apply_res["error"].lower()
        # File must NOT be overwritten with the stale patch
        assert f.read_text() == "line 1\nSOMETHING CHANGED\nline 3\n"


    def test_applies_batch_patch_for_all_files(self, tmp_path, monkeypatch):
        """Cycle 8 — apply_last_dry_run works for batch (multiple files)."""
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("hello\nworld\n")
        f2.write_text("foo\nbar\n")

        diff_a = (
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n-hello\n+HELLO\n"
        )
        diff_b = (
            "--- a/b.py\n+++ b/b.py\n"
            "@@ -1,1 +1,1 @@\n-foo\n+FOO\n"
        )

        dry_res = batch_patch_files(
            patches=[
                {"target_file": "a.py", "patch_content": diff_a},
                {"target_file": "b.py", "patch_content": diff_b},
            ],
            dry_run=True,
        )
        run_id = dry_res["run_id"]

        apply_res = apply_last_dry_run(run_id=run_id)
        assert apply_res["success"] is True
        assert f1.read_text() == "HELLO\nworld\n"
        assert f2.read_text() == "FOO\nbar\n"


    def test_line_ending_normalization(self, tmp_path, monkeypatch):
        """Line endings must be normalized internally, and original line endings preserved."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        # File with CRLF
        app_file.write_bytes(b"line 1\r\nline 2\r\nline 3\r\n")

        # Patch with LF
        res = patch_file(
            target_file="app.py",
            search_content="line 2\n",
            replace_content="modified line 2\n",
            dry_run=False
        )
        assert res["success"] is True
        # Original CRLF line ending should be preserved
        assert app_file.read_bytes() == b"line 1\r\nmodified line 2\r\nline 3\r\n"


    def test_python_ast_syntax_check(self, tmp_path, monkeypatch):
        """Python AST check should reject syntactically invalid Python code."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("def func():\n    pass\n")

        # Patch that introduces a syntax error
        res = patch_file(
            target_file="app.py",
            search_content="    pass",
            replace_content="    pass\n    if :",
            dry_run=False
        )
        assert "error" in res
        assert "Syntax Error" in res["error"]
        # Original content should be untouched
        assert app_file.read_text() == "def func():\n    pass\n"


    def test_multi_patch_replacements(self, tmp_path, monkeypatch):
        """Multi-patch replacements should apply bottom-up to prevent line drift."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\nline 4\n")

        # Edit line 4 and line 2 in one call
        res = patch_file(
            target_file="app.py",
            replacements=[
                {"search_content": "line 2", "replace_content": "new line 2\nand extra 2", "start_line": 2, "end_line": 2},
                {"search_content": "line 4", "replace_content": "new line 4\nand extra 4", "start_line": 4, "end_line": 4},
            ],
            dry_run=False
        )
        assert res["success"] is True
        assert app_file.read_text() == "line 1\nnew line 2\nand extra 2\nline 3\nnew line 4\nand extra 4\n"


    def test_multi_patch_intermediate_syntax_invalid(self, tmp_path, monkeypatch):
        """Multi-patch replacements should succeed even if intermediate states are invalid, provided final is valid."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("x = 1\ny = 2\n")

        res = patch_file(
            target_file="app.py",
            replacements=[
                {"search_content": "x = 1", "replace_content": "def func():\n    x = 1", "start_line": 1, "end_line": 1},
                {"search_content": "y = 2", "replace_content": "    y = 2", "start_line": 2, "end_line": 2},
            ],
            dry_run=False
        )
        assert res["success"] is True
        assert app_file.read_text() == "def func():\n    x = 1\n    y = 2\n"


    def test_ruff_linter_warnings(self, tmp_path, monkeypatch):
        """Ruff/Linter warnings should be returned in message for Python files."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("def func():\n    pass\n")

        # Patch that introduces an unused import (F401)
        res = patch_file(
            target_file="app.py",
            search_content="    pass",
            replace_content="    import os\n    pass",
            dry_run=False
        )
        assert res["success"] is True
        assert "warnings" in res
        assert any("F401" in w for w in res["warnings"])

