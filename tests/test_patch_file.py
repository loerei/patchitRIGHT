"""Tests for patch_file tool in patchitRIGHT."""

from pathlib import Path
import pytest

from patchitright_mcp.patch_file import patch_file, batch_patch_files, run_startup_recovery
from jcodemunch_mcp.tools.index_folder import index_folder


class TestPatchFile:
    def test_context_mismatch_guard(self, tmp_path, monkeypatch):
        """patch_file must refuse to edit a file outside the active workspace."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        external_file = tmp_path / "external.py"
        external_file.write_text("print('hello')\n")

        monkeypatch.chdir(workspace_dir)

        # Call patch_file pointing outside the active workspace relatively
        res = patch_file(
            target_file="../external.py",
            search_content="hello",
            replace_content="world",
            dry_run=True
        )

        assert "error" in res
        assert res["error"] == "fatal_context_mismatch"

    def test_dry_run_generates_diff(self, tmp_path, monkeypatch):
        """patch_file must return a diff on dry_run and not modify the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=True
        )

        assert "success" in res
        assert res["success"] is True
        assert res["dryRun"] is True
        assert "modified line 2" in res["message"]
        # Verify file is not changed
        assert app_file.read_text() == "line 1\nline 2\nline 3\n"

    def test_patch_success(self, tmp_path, monkeypatch):
        """patch_file must successfully modify the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        assert res["dryRun"] is False
        assert app_file.read_text() == "line 1\nmodified line 2\nline 3\n"

    def test_folder_and_file_filters(self, tmp_path, monkeypatch):
        """patch_file must respect folder_filter and file_filter."""
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        app_file = src_dir / "app.py"
        app_file.write_text("test content\n")

        # Wrong folder filter
        res = patch_file(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            folder_filter="tests",
            dry_run=True
        )
        assert "error" in res

        # Correct folder filter
        res = patch_file(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            folder_filter="src",
            dry_run=True
        )
        assert "success" in res

        # Wrong file filter
        res = patch_file(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            file_filter="test",
            dry_run=True
        )
        assert "error" in res

        # Correct file filter
        res = patch_file(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            file_filter="app",
            dry_run=True
        )
        assert "success" in res

    def test_line_filter_assertions(self, tmp_path, monkeypatch):
        """patch_file must respect numeric and string line_filter assertions."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # Numeric line_filter matches actual line
        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter=2,
            dry_run=True
        )
        assert "success" in res

        # Numeric line_filter mismatch
        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter=3,
            dry_run=True
        )
        assert "error" in res

        # String line_filter match
        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter="line 2",
            dry_run=True
        )
        assert "success" in res

        # String line_filter mismatch
        res = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter="unrelated",
            dry_run=True
        )
        assert "error" in res

    def test_allow_multiple_check(self, tmp_path, monkeypatch):
        """patch_file must refuse to modify multiple matches by default unless allow_multiple is True."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("dup\ndup\n")

        # Default is False
        res = patch_file(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            allow_multiple=False,
            dry_run=True
        )
        assert "error" in res

        # Explicit True
        res = patch_file(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            allow_multiple=True,
            dry_run=True
        )
        assert "success" in res
        assert res["occurrences"] == 2

    def test_line_range_boundary(self, tmp_path, monkeypatch):
        """patch_file must restrict search and replace to the specified line boundaries."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("dup\ndup\ndup\n")

        # Scope lines 1-2 only (contains 2 occurrences)
        res = patch_file(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            start_line=1,
            end_line=2,
            allow_multiple=True,
            dry_run=False
        )
        assert "success" in res
        # Only lines 1 and 2 replaced
        assert app_file.read_text() == "new\nnew\ndup\n"

    def test_ast_symbol_boundary(self, tmp_path, monkeypatch):
        """patch_file must restrict search and replace to AST symbol_name boundaries by loading the index."""
        monkeypatch.chdir(tmp_path)
        
        # Write a file containing a python function
        app_file = tmp_path / "app.py"
        app_file.write_text(
            "def target_func():\n"
            "    val = 'dup'\n"
            "    return val\n"
            "\n"
            "other_val = 'dup'\n"
        )
        
        # Index the directory
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")
        
        # Call patch_file with symbol_name boundary (restricts to target_func, lines 1-3)
        res = patch_file(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            symbol_name="target_func",
            dry_run=False,
            storage_path=store_path
        )
        
        assert "success" in res
        assert res["success"] is True
        
        # Verify only the 'dup' inside target_func was replaced
        expected_content = (
            "def target_func():\n"
            "    val = 'new'\n"
            "    return val\n"
            "\n"
            "other_val = 'dup'\n"
        )
        assert app_file.read_text() == expected_content

    def test_external_workspace_indexed_repo(self, tmp_path, monkeypatch):
        """patch_file must allow editing files inside an indexed repo even if CWD is outside."""
        # Set CWD to an external directory (simulating AppData programs directory)
        external_cwd = tmp_path / "appdata_cwd"
        external_cwd.mkdir()
        monkeypatch.chdir(external_cwd)

        # Create a workspace directory and index it
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        app_file = workspace_dir / "app.py"
        app_file.write_text("print('hello')\n")

        store_path = str(tmp_path / "store")
        index_folder(str(workspace_dir), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        # Call patch_file with absolute path of target file (which is under indexed workspace_dir)
        res = patch_file(
            target_file=str(app_file),
            search_content="hello",
            replace_content="world",
            dry_run=False,
            storage_path=store_path
        )

        assert "success" in res
        assert res["success"] is True
        assert app_file.read_text() == "print('world')\n"

    def test_did_you_mean_suggestion(self, tmp_path, monkeypatch):
        """patch_file must include a smart 'Did you mean?' suggestion upon search failure if a close match exists."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text(
            "def test_func():\n"
            "    val = 'hello_world'\n"
            "    return val\n"
        )

        res = patch_file(
            target_file="app.py",
            search_content="val = 'hello_worl'",
            replace_content="val = 'new'",
            dry_run=True
        )

        assert "error" in res
        assert "Did you mean" in res["error"]
        assert "val = 'hello_world'" in res["error"]
        assert "similarity 88%" in res["error"]

    def test_did_you_mean_applied(self, tmp_path, monkeypatch):
        """patch_file must apply the closest match replacement when did_you_mean is True."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text(
            "def test_func():\n"
            "    val = 'hello_world'\n"
            "    return val\n"
        )

        res = patch_file(
            target_file="app.py",
            search_content="val = 'hello_worl'",
            replace_content="val = 'new'",
            did_you_mean=True,
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        assert "applied via 'did_you_mean' fallback" in res["message"]
        assert "similarity 88%" in res["message"]
        
        expected_content = (
            "def test_func():\n"
            "    val = 'new'\n"
            "    return val\n"
        )
        assert app_file.read_text() == expected_content

    def test_unified_patch_success(self, tmp_path, monkeypatch):
        """patch_file must successfully apply a unified diff patch."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        patch = (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -2,2 +2,2 @@\n"
            "-line 2\n"
            "+modified line 2\n"
        )

        res = patch_file(
            target_file="app.py",
            patch_content=patch,
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        assert app_file.read_text() == "line 1\nmodified line 2\nline 3\n"

    def test_unified_patch_multi_hunk_offset(self, tmp_path, monkeypatch):
        """patch_file must track line offsets when applying multiple hunks."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # Hunk 1: insert two lines at line 1
        # Hunk 2: replace 'line 3' (original line 3, now shifted to line 5)
        patch = (
            "@@ -1,1 +1,3 @@\n"
            " line 1\n"
            "+new line A\n"
            "+new line B\n"
            "@@ -3,1 +5,1 @@\n"
            "-line 3\n"
            "+modified line 3\n"
        )

        res = patch_file(
            target_file="app.py",
            patch_content=patch,
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        
        expected = "line 1\nnew line A\nnew line B\nline 2\nmodified line 3\n"
        assert app_file.read_text() == expected

    def test_unified_patch_strict_fuzz_failure(self, tmp_path, monkeypatch):
        """patch_file must reject a unified diff with strict Fuzz = 0 if context is mismatched."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # Wrong context: expect 'wrong line 2' instead of 'line 2'
        patch = (
            "@@ -2,1 +2,1 @@\n"
            "-wrong line 2\n"
            "+modified line 2\n"
        )

        res = patch_file(
            target_file="app.py",
            patch_content=patch,
            dry_run=True
        )

        assert "error" in res
        assert "failed to match strictly at line 2" in res["error"]

    def test_unified_patch_did_you_mean(self, tmp_path, monkeypatch):
        """patch_file must provide a 'Did you mean' suggestion on unified patch mismatch."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # We assert for 'line 22' but the file has 'line 2'
        patch = (
            "@@ -2,1 +2,1 @@\n"
            "-line 22\n"
            "+modified line 2\n"
        )

        res = patch_file(
            target_file="app.py",
            patch_content=patch,
            dry_run=True
        )

        assert "error" in res
        assert "Did you mean" in res["error"]
        assert "line 2" in res["error"]
        assert "similarity 92%" in res["error"]

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

        # We will hijack TargetPath write to simulate concurrent change right before commit
        # But a cleaner way is just to manually test the check in batch_patch_files.
        # We can simulate this by setting up a patch, but wait, batch_patch_files reads from the disk
        # sequentially. If we pass two patches to file_a, but wait, the tool calculates original_hash
        # at the start. If we modify file_a during execution or mock target_path.read_bytes to return
        # something else on the second check, we trigger optimistic lock.
        # Let's mock hashlib.sha256 in a way or simulate it by having a patch that succeeds in validation
        # but fails optimistic lock.
        # Actually, let's write a test that simulates checksum drift by modifying the file on disk
        # during the batch processing if we can, or just mock target_path.read_bytes.
        # Let's mock target_path.read_bytes or target_path.stat to change.
        # Let's write a simple helper test.
        # Removed redundant pass

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

    def test_path_normalization_double_slashes(self, tmp_path, monkeypatch):
        """patch_file must successfully normalize paths with double backslashes/slashes."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\n")

        # Call with redundant separators and mixed slashes
        res = patch_file(
            target_file=".\\\\//\\\\app.py",
            search_content="line 2",
            replace_content="modified",
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        assert app_file.read_text() == "line 1\nmodified\n"


class TestPatchEngine:
    def test_apply_classic_patch_success(self):
        """PatchEngine must apply classic search-and-replace correctly."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\nline 3\n", "test.py")
        res, count = engine.apply_classic_patch("line 2", "modified line 2")
        assert res == "line 1\nmodified line 2\nline 3\n"
        assert count == 1

    def test_apply_classic_patch_missing_error(self):
        """PatchEngine must raise ValueError with suggestion if content is missing."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\nline 3\n", "test.py")
        with pytest.raises(ValueError) as exc:
            engine.apply_classic_patch("line 22", "modified")
        assert "Search content not found" in str(exc.value)
        assert "Did you mean" in str(exc.value)
        assert "line 2" in str(exc.value)

    def test_apply_classic_patch_line_boundary(self):
        """PatchEngine must restrict replacements to line boundaries."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("dup\ndup\ndup\n", "test.py")
        res, count = engine.apply_classic_patch("dup", "new", allow_multiple=True, start_line=1, end_line=2)
        assert res == "new\nnew\ndup\n"
        assert count == 2

    def test_apply_classic_patch_allow_multiple(self):
        """PatchEngine must reject multiple occurrences by default unless allow_multiple is True."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("dup\ndup\n", "test.py")
        with pytest.raises(ValueError) as exc:
            engine.apply_classic_patch("dup", "new", allow_multiple=False)
        assert "occurs 2 times" in str(exc.value)

    def test_apply_classic_patch_line_filter_numeric(self):
        """PatchEngine must assert numeric line filters correctly."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\n", "test.py")
        
        # Valid assertion
        res, _ = engine.apply_classic_patch("line 2", "new", line_filter=2)
        assert res == "line 1\nnew\n"
        
        # Invalid assertion
        with pytest.raises(ValueError) as exc:
            engine.apply_classic_patch("line 2", "new", line_filter=1)
        assert "lineFilter assertion failed" in str(exc.value)

    def test_apply_classic_patch_line_filter_string(self):
        """PatchEngine must assert string line filters correctly."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\n", "test.py")
        
        # Valid assertion
        res, _ = engine.apply_classic_patch("line 2", "new", line_filter="line 2")
        assert res == "line 1\nnew\n"
        
        # Invalid assertion
        with pytest.raises(ValueError) as exc:
            engine.apply_classic_patch("line 2", "new", line_filter="unrelated")
        assert "lineFilter assertion failed" in str(exc.value)

    def test_apply_unified_patch_success(self):
        """PatchEngine must apply unified patches strictly."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\nline 3\n", "test.py")
        patch = (
            "@@ -2,2 +2,2 @@\n"
            "-line 2\n"
            "+modified line 2\n"
        )
        res = engine.apply_unified_patch(patch)
        assert res == "line 1\nmodified line 2\nline 3\n"

    def test_apply_unified_patch_mismatch_error(self):
        """PatchEngine must raise ValueError with suggestion if unified hunk context fails to match."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\nline 3\n", "test.py")
        patch = (
            "@@ -2,1 +2,1 @@\n"
            "-line 22\n"
            "+modified line 2\n"
        )
        with pytest.raises(ValueError) as exc:
            engine.apply_unified_patch(patch)
        assert "failed to match strictly at line 2" in str(exc.value)
        assert "Did you mean" in str(exc.value)
        assert "line 2" in str(exc.value)


# ---------------------------------------------------------------------------
# Cycles 3–8: run_id flow (apply_last_dry_run)
# ---------------------------------------------------------------------------

from patchitright_mcp.patch_file import apply_last_dry_run  # noqa: E402
from patchitright_mcp.run_cache import RunCache              # noqa: E402


class TestDryRunReturnsRunId:
    """Cycles 3 & 4 — dry-run responses include run_id + expires_in."""

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
        assert isinstance(res["run_id"], str) and len(res["run_id"]) > 0
        assert "expires_in" in res
        assert isinstance(res["expires_in"], int) and res["expires_in"] > 0

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
    """Cycles 5–8 — apply_last_dry_run behavior."""

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
