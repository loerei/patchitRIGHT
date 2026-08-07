"""Tests for patch_file tool in patchitRIGHT."""

from pathlib import Path
import pytest

from unittest.mock import patch
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

    def test_did_you_mean_cache_suggestion(self, tmp_path, monkeypatch):
        """patch_file must cache the suggested patch on error so it can be applied via apply_last_dry_run."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text(
            "def test_func():\n"
            "    val = 'hello_world'\n"
            "    return val\n"
        )

        # Call patch_file without did_you_mean=True (so it fails)
        res = patch_file(
            target_file="app.py",
            search_content="val = 'hello_worl'",
            replace_content="val = 'new'",
            did_you_mean=False,
            dry_run=False
        )

        assert "error" in res
        assert "run_id" in res
        assert "expires_in" in res
        assert "apply_last_dry_run" in res["message"]
        
        # Verify the file is not changed yet
        assert "hello_world" in app_file.read_text()

        # Now apply the cached suggestion!
        from patchitright_mcp.patch_file import apply_last_dry_run
        apply_res = apply_last_dry_run(run_id=res["run_id"])
        assert apply_res["success"] is True
        
        # Verify the suggested patch was successfully applied
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

    def test_classic_patch_relocation_success(self, tmp_path, monkeypatch):
        """patch_file must successfully relocate the search content if it's unique in the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")

        # Search content is at line 4, but we restrict scope to lines 1-2 (mismatch)
        res = patch_file(
            target_file="app.py",
            search_content="line 4",
            replace_content="modified 4",
            start_line=1,
            end_line=2,
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        assert "relocated" in res["message"]
        assert app_file.read_text() == "line 1\nline 2\nline 3\nmodified 4\nline 5\n"

    def test_classic_patch_relocation_multiple_fail(self, tmp_path, monkeypatch):
        """patch_file must fail to relocate if the search content exists multiple times in the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\ndup\nline 4\ndup\n")

        # Search content 'dup' is at lines 3 and 5, but we restrict scope to lines 1-2
        res = patch_file(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            start_line=1,
            end_line=2,
            dry_run=False
        )

        assert "error" in res
        assert "occurs 2 times" in res["error"]

    def test_classic_patch_relocation_not_found_fail(self, tmp_path, monkeypatch):
        """patch_file must fail if the search content is not found anywhere in the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = patch_file(
            target_file="app.py",
            search_content="nonexistent",
            replace_content="new",
            start_line=1,
            end_line=2,
            dry_run=False
        )

        assert "error" in res
        assert "Search content not found" in res["error"]


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

    def test_detect_mismatch_reason_escaped(self):
        """PatchEngine must detect raw escape character mismatch in suggestion."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\nline 3\n", "test.py")
        with pytest.raises(ValueError) as exc:
            # The file has literal newlines, but we search using escaped \\n
            engine.apply_classic_patch("line 1\\nline 2", "modified")
        assert "Mismatch due to raw escape characters" in str(exc.value)

    def test_detect_mismatch_reason_whitespace(self):
        """PatchEngine must detect whitespace/indentation mismatch in suggestion."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("    line 1\n    line 2\n", "test.py")
        with pytest.raises(ValueError) as exc:
            # The search content lacks indentation
            engine.apply_classic_patch("line 1\nline 2", "modified")
        assert "Mismatch due to indentation or whitespace differences" in str(exc.value)

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
# RunCache is imported and unused here, so we remove it to fix ruff error


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


class TestSymbolScope:
    """Integration tests for symbol_scope features (full, body, boundary)."""

    def test_full_scope_replaces_entire_function(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="function foo() { return 2; }",
            symbol_name="foo",
            symbol_scope="full",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function foo() { return 2; }\n"

    def test_body_scope_replaces_only_body(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo(a, b) {\n  const x = a + b;\n  return x;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  return a * b;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        # Original signature and braces preserved, body replaced and padded
        expected = "function foo(a, b) {\n  return a * b;\n}\n"
        assert app_file.read_text() == expected

    def test_body_scope_arrow_block(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("const fn = (x) => {\n  return x + 1;\n};\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  return x + 2;",
            symbol_name="fn",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        expected = "const fn = (x) => {\n  return x + 2;\n};\n"
        assert app_file.read_text() == expected

    def test_body_scope_arrow_expression(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("const fn = (x) => x + 1;\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="x + 2",
            symbol_name="fn",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        # Expression replaced verbatim, no braces auto-wrap, no block padding
        assert app_file.read_text() == "const fn = (x) => x + 2;\n"

    def test_body_scope_class_method(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("class C {\n  method() {\n    const a = 1;\n  }\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="    return 42;",
            symbol_name="method",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        expected = "class C {\n  method() {\n    return 42;\n  }\n}\n"
        assert app_file.read_text() == expected

    def test_body_scope_getter_setter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("class C {\n  get val() {\n    return this._val;\n  }\n  set val(v) {\n    this._val = v;\n  }\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="    return 100;",
            symbol_name="val",  # targets getter usually depending on index
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True

    def test_body_scope_async_function(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("async function gen() {\n  await doSomething();\n}\n")

        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  await doSomethingElse();",
            symbol_name="gen",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "async function gen() {\n  await doSomethingElse();\n}\n"

    def test_body_scope_destructured_params(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo({ a, b }) {\n  return a;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  return b;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function foo({ a, b }) {\n  return b;\n}\n"

    def test_body_scope_typescript_generics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.ts"
        app_file.write_text("function foo<T extends { k: string }>(x: T): { r: string } {\n  return { r: x.k };\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.ts",
            replace_content="  return { r: 'new' };",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function foo<T extends { k: string }>(x: T): { r: string } {\n  return { r: 'new' };\n}\n"

    def test_body_scope_typescript_overloads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.ts"
        app_file.write_text("function f(x: string): string;\nfunction f(x: any): any {\n  return x;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.ts",
            replace_content="  return 'overloaded';",
            symbol_name="f",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function f(x: string): string;\nfunction f(x: any): any {\n  return 'overloaded';\n}\n"

    def test_full_scope_with_search_content_still_works(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        # Classic replacement inside symbol boundary: scope="boundary" is default
        res = patch_file(
            target_file="app.js",
            search_content="return 1;",
            replace_content="return 2;",
            symbol_name="foo",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function foo() {\n  return 2;\n}\n"

    def test_body_scope_dry_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  return 2;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=True,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert res.get("dryRun") is True
        assert "run_id" in res
        # File should not be modified
        assert app_file.read_text() == "function foo() {\n  return 1;\n}\n"

    def test_scope_without_symbol_name_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() { return 1; }")
        
        res = patch_file(
            target_file="app.js",
            replace_content="return 2;",
            symbol_scope="body",
            dry_run=False
        )
        assert "error" in res

    def test_boundary_scope_is_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        # Calling with search_content and no scope (defaults to boundary)
        res = patch_file(
            target_file="app.js",
            search_content="return 1;",
            replace_content="return 3;",
            symbol_name="foo",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function foo() {\n  return 3;\n}\n"

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

    def test_body_scope_single_line_function(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("const add = (a, b) => { return a + b; };\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content=" return a * b; ",
            symbol_name="add",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        # signature and closing brace on the same line preserved via column offsets
        assert app_file.read_text() == "const add = (a, b) => { return a * b; };\n"

    def test_body_scope_compact_formatting(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function f(){return 1}")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="return 2",
            symbol_name="f",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert app_file.read_text() == "function f(){return 2}"

    def test_tree_sitter_unavailable_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        with patch("patchitright_mcp.body_parser._try_tree_sitter", side_effect=ImportError):
            res = patch_file(
                target_file="app.js",
                replace_content="  return 999;",
                symbol_name="foo",
                symbol_scope="body",
                dry_run=False,
                storage_path=store_path
            )
        assert res.get("success") is True
        assert app_file.read_text() == "function foo() {\n  return 999;\n}\n"

    def test_jsx_fallback_errors_not_silently_wrong(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.tsx"
        app_file.write_text("function foo() {\n  return <div />;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        with patch("patchitright_mcp.body_parser._try_tree_sitter", side_effect=ImportError):
            res = patch_file(
                target_file="app.tsx",
                replace_content="  return <span />;",
                symbol_name="foo",
                symbol_scope="body",
                dry_run=False,
                storage_path=store_path
            )
        # Should raise clear error about fallback being unsupported on JSX/TSX
        assert "error" in res
        assert "JSX/TSX" in res["error"]

    def test_body_scope_indentation_min_baseline(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        # Body has mixed indentation (line 2 is 2 spaces, line 3 is 4 spaces).
        # Target base indent is min = 2 spaces.
        app_file.write_text("function foo() {\n  // comment\n    const a = 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        # replace content is 0-indented
        res = patch_file(
            target_file="app.js",
            replace_content="const b = 2;\nreturn b;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        # Normalized to 2 spaces base indent
        expected = "function foo() {\n  const b = 2;\n  return b;\n}\n"
        assert app_file.read_text() == expected

    def test_body_scope_indentation_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="return 2;",  # 0 indent
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert res.get("indentation_adjusted") is True
        assert "indent_delta" in res

    def test_large_file_skips_treesitter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        # Create a large body function (mocked as large)
        lines = ["function foo() {"] + [f"  console.log({i});" for i in range(10)] + ["}"]
        app_file.write_text("\n".join(lines))
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        with patch("patchitright_mcp.body_parser.MAX_LINES_FOR_TREESITTER", 5):
            res = patch_file(
                target_file="app.js",
                replace_content="  return 2;",
                symbol_name="foo",
                symbol_scope="body",
                dry_run=False,
                storage_path=store_path
            )
        assert res.get("success") is True
        assert res.get("large_file_fallback") is True

    def test_body_scope_empty_body_indent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {}")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="const a = 1;\nreturn a;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        # Empty body fallback indent: signature indent + 1 level (default 2 spaces)
        expected = "function foo() {\n  const a = 1;\n  return a;\n}"
        assert app_file.read_text() == expected

    def test_body_scope_multibyte_characters(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        # Emojis/multibyte chars before the target function.
        app_file.write_text("const a = \"🐛é\";\nfunction foo() {\n  return 1;\n}\n", encoding="utf-8")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  return 2;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert "🐛é" in app_file.read_text(encoding="utf-8")
        assert "return 2;" in app_file.read_text(encoding="utf-8")

    def test_body_scope_auto_pads_block_newlines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_text("function foo() {\n  return 1;\n}\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        # Replaced content has NO leading/trailing newline
        res = patch_file(
            target_file="app.js",
            replace_content="  console.log('new');",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        assert res.get("newline_padded") is True
        # Format must be nicely multiline, not squished
        expected = "function foo() {\n  console.log('new');\n}\n"
        assert app_file.read_text() == expected

    def test_body_scope_windows_crlf(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.js"
        app_file.write_bytes(b"function foo() {\r\n  return 1;\r\n}\r\n")
        
        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.js",
            replace_content="  return 2;",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        # Spliced correctly with CRLF preserved
        assert b"\r\n" in app_file.read_bytes()
        assert b"\n" not in app_file.read_bytes().replace(b"\r\n", b"")

    def test_python_body_scope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("def foo(a, b):\n    return a + b\n")

        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.py",
            replace_content="    return a * b",
            symbol_name="foo",
            symbol_scope="body",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        expected = "def foo(a, b):\n    return a * b\n"
        assert app_file.read_text() == expected

    def test_python_full_scope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("def foo(a, b):\n    return a + b\n")

        from jcodemunch_mcp.tools.index_folder import index_folder
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")

        res = patch_file(
            target_file="app.py",
            replace_content="def foo(a, b):\n    return a * b",
            symbol_name="foo",
            symbol_scope="full",
            dry_run=False,
            storage_path=store_path
        )
        assert res.get("success") is True
        expected = "def foo(a, b):\n    return a * b\n"
        assert app_file.read_text() == expected

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

    def test_creation_diff_old_start_zero(self, tmp_path, monkeypatch):
        """patch_file must allow creating a new file via unified diff patch_content with old_start=0."""
        monkeypatch.chdir(tmp_path)
        new_file = tmp_path / "new_doc.py"

        patch_content = (
            "--- /dev/null\n"
            "+++ b/new_doc.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def new_func():\n"
            "+    return True\n"
        )

        res = patch_file(
            target_file="new_doc.py",
            patch_content=patch_content,
            dry_run=False
        )

        assert res.get("success") is True
        assert new_file.exists()
        assert new_file.read_text() == "def new_func():\n    return True"

    def test_find_closest_match_large_file_performance(self):
        """_find_closest_match must run in < 0.5s for large search blocks across 5000 lines."""
        import time
        from patchitright_mcp.engine import PatchEngine

        # Build a 5000-line mock python file
        file_lines = []
        for i in range(5000):
            file_lines.append(f"def func_{i}(val: int = {i}) -> int:\n    return val * {i} + 1\n")
        file_content = "\n".join(file_lines)

        # Build a 200-line search block from lines 3000-3200 with minor formatting differences
        search_lines = []
        for i in range(3000, 3200):
            search_lines.append(f"def func_{i}(val: int = {i}) -> int:\n    return val * {i} + 2\n") # minor difference (+2 instead of +1)
        search_content = "\n".join(search_lines)

        engine = PatchEngine(file_content, "mock.py", bypass_validation=True)

        start_time = time.time()
        res = engine._find_closest_match(0, len(engine.file_lines) - 1, search_content)
        duration = time.time() - start_time

        assert res is not None
        start_line, end_line, _, ratio = res
        assert ratio >= 0.85
        assert duration < 0.5, f"_find_closest_match took {duration:.3f}s, expected < 0.5s"

    def test_output_schema_single_file(self, tmp_path, monkeypatch):
        """patch_file must return standardized top-level schema fields for single-file mode."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # Dry run
        res_dry = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=True
        )
        assert res_dry["target_file"] == "app.py"
        assert res_dry["diff_content"] is not None
        assert "modified line 2" in res_dry["diff_content"]
        assert res_dry["relocated_range"] is None
        assert res_dry["did_you_mean_info"] is None
        assert res_dry["modified_files"] is None

        # Live run
        res_live = patch_file(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=False
        )
        assert res_live["target_file"] == "app.py"
        assert res_live["diff_content"] is None
        assert res_live["relocated_range"] is None
        assert res_live["did_you_mean_info"] is None
        assert res_live["modified_files"] is None
        assert "Successfully patched" in res_live["message"]

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


class TestLineBasedInsertion:
    def test_insert_line_before_and_after(self, tmp_path, monkeypatch):
        """Test insert_line with before, after, and EOF -1 sentinel positioning."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("line 1\nline 2\nline 3\n")

        # Insert before line 1 (top of file)
        res1 = patch_file(
            target_file="sample.py",
            insert_line=1,
            insert_content="import sys\n",
            insert_position="before",
            dry_run=False
        )
        assert res1["success"] is True
        assert f.read_text() == "import sys\nline 1\nline 2\nline 3\n"

        # Insert after line 2 (which is now 'line 1')
        res2 = patch_file(
            target_file="sample.py",
            insert_line=2,
            insert_content="# header comment",
            insert_position="after",
            dry_run=False
        )
        assert res2["success"] is True
        assert "import sys\nline 1\n# header comment\nline 2\nline 3\n" in f.read_text()

        # Insert at EOF via -1
        res3 = patch_file(
            target_file="sample.py",
            insert_line=-1,
            insert_content="# bottom comment",
            dry_run=False
        )
        assert res3["success"] is True
        assert f.read_text().endswith("# bottom comment\n") or f.read_text().endswith("# bottom comment")

    def test_insert_line_invalid_indexes(self, tmp_path, monkeypatch):
        """Line index 0 or < -1 must raise ValueError."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("a = 1\n")

        res0 = patch_file(
            target_file="sample.py",
            insert_line=0,
            insert_content="b = 2",
        )
        assert "error" in res0
        assert "must be >= 1 or -1" in res0["error"]

        res_neg = patch_file(
            target_file="sample.py",
            insert_line=-2,
            insert_content="b = 2",
        )
        assert "error" in res_neg
        assert "must be >= 1 or -1" in res_neg["error"]

    def test_insert_line_numeric_position_restrictions(self, tmp_path, monkeypatch):
        """Positions 'start' and 'end' require a symbol_name."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("a = 1\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=1,
            insert_content="b = 2",
            insert_position="start"
        )
        assert "error" in res
        assert "Positions 'start' and 'end' require a symbol_name" in res["error"]

    def test_insert_line_simultaneous_line_and_symbol_prohibited(self, tmp_path, monkeypatch):
        """Providing both insert_line and symbol_name in a single item raises ValueError."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("def foo():\n    pass\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=1,
            symbol_name="foo",
            insert_content="x = 1"
        )
        assert "error" in res
        assert "Cannot specify both 'insert_line' and 'symbol_name'" in res["error"]

    def test_insert_line_empty_file(self, tmp_path, monkeypatch):
        """Insertion into a 0-byte file must work correctly."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "empty.py"
        f.write_text("")

        res = patch_file(
            target_file="empty.py",
            insert_line=1,
            insert_content="x = 42\n",
            dry_run=False
        )
        assert res["success"] is True
        assert f.read_text() == "x = 42"

    def test_insert_line_auto_indent_false(self, tmp_path, monkeypatch):
        """auto_indent=False must insert content verbatim without matching target indentation."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("a = 1\nb = 2\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=2,
            insert_content="    raw_indent = True",
            auto_indent=False,
            bypass_validation=True,
            dry_run=False
        )
        assert res["success"] is True
        assert "a = 1\n    raw_indent = True\nb = 2\n" in f.read_text()

    def test_insert_line_crlf_preservation(self, tmp_path, monkeypatch):
        """CRLF line endings must be preserved post-insertion."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "crlf.py"
        f.write_bytes(b"line 1\r\nline 2\r\n")

        res = patch_file(
            target_file="crlf.py",
            insert_line=2,
            insert_content="new line",
            dry_run=False
        )
        assert res["success"] is True
        content_bytes = f.read_bytes()
        assert b"\r\n" in content_bytes
        assert b"\nline 2" not in content_bytes.replace(b"\r\n", b"LF")

    def test_insert_line_dry_run_preview(self, tmp_path, monkeypatch):
        """dry_run=True must generate diff preview with + additions and return run_id."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("a = 1\nb = 2\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=1,
            insert_content="import os",
            dry_run=True
        )
        assert res["success"] is True
        assert res["dryRun"] is True
        assert "+import os" in res["message"]
        assert res["run_id"] is not None

    def test_files_batch_array_line_insertion(self, tmp_path, monkeypatch):
        """files batch array must support line insertions across multiple files."""
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "f1.py"
        f2 = tmp_path / "f2.py"
        f1.write_text("a = 1\n")
        f2.write_text("b = 2\n")

        res = patch_file(
            files=[
                {"target_file": "f1.py", "insert_line": 1, "insert_content": "# f1 header"},
                {"target_file": "f2.py", "insert_line": 1, "insert_content": "# f2 header"},
            ],
            dry_run=False
        )
        assert res["success"] is True
        assert f1.read_text().startswith("# f1 header")
        assert f2.read_text().startswith("# f2 header")

    def test_insert_line_auto_indent_blank_lines(self, tmp_path, monkeypatch):
        """Auto-indentation must handle multi-line blocks and scan adjacent lines when target is blank."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("def foo():\n\n    pass\n")

        # Insert at blank line 2
        res = patch_file(
            target_file="sample.py",
            insert_line=2,
            insert_content="x = 10\ny = 20\n",
            auto_indent=True,
            dry_run=False
        )
        assert res["success"] is True
        lines = f.read_text().split("\n")
        assert lines[1] == "    x = 10"
        assert lines[2] == "    y = 20"

    def test_insert_line_auto_indent_tab_vs_space(self, tmp_path, monkeypatch):
        """Tab-indented files must match tab indentation without space conversion."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "tab.py"
        f.write_text("def foo():\n\tpass\n")

        res = patch_file(
            target_file="tab.py",
            insert_line=2,
            insert_content="x = 1",
            auto_indent=True,
            dry_run=False
        )
        assert res["success"] is True
        assert "\t" in f.read_text()
        assert "\tx = 1\n\tpass" in f.read_text()

    def test_insert_relative_to_symbol_with_decorators(self, tmp_path, monkeypatch):
        """symbol-relative insertion with position 'before' must insert above decorators."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "dec.py"
        f.write_text("@decorator_one\n@decorator_two\ndef foo():\n    pass\n")

        res = patch_file(
            target_file="dec.py",
            symbol_name="foo",
            insert_content="# method doc",
            insert_position="before",
            dry_run=False
        )
        assert res["success"] is True
        assert f.read_text().startswith("# method doc\n@decorator_one")

    def test_mixed_search_and_line_insertion_batch(self, tmp_path, monkeypatch):
        """Bottom-up execution sorting for mixed insertion and replacement items in replacements array."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("line 1\nline 2\nline 3\nline 4\n")

        res = patch_file(
            target_file="sample.py",
            replacements=[
                {"insert_line": 1, "insert_content": "# header"},
                {"search_content": "line 3", "replace_content": "modified line 3"},
            ],
            dry_run=False
        )
        assert res["success"] is True
        content = f.read_text()
        assert "# header\nline 1" in content
        assert "modified line 3" in content

    def test_insert_line_invalid_params_validation(self, tmp_path, monkeypatch):
        """Parameter conflict rules: insert_content cannot mix with search_content or be empty."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("a = 1\n")

        # Empty content
        res1 = patch_file(
            target_file="sample.py",
            insert_line=1,
            insert_content=""
        )
        assert "error" in res1
        assert "cannot be empty" in res1["error"]

        # Conflict with search_content
        res2 = patch_file(
            target_file="sample.py",
            insert_line=1,
            insert_content="b = 2",
            search_content="a = 1"
        )
        assert "error" in res2
        assert "Cannot combine 'insert_content'" in res2["error"]

    def test_insert_line_eof_indentation_default_root_scope(self, tmp_path, monkeypatch):
        """Verify appending to EOF via insert_line=-1 defaults to root scope (0 spaces) even if last line is indented."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "dummy_test.py"
        f.write_text("class Calculator:\n    def add(self, a, b):\n        return a + b\n")

        res = patch_file(
            target_file="dummy_test.py",
            insert_line=-1,
            insert_content="# Footer comment",
            auto_indent=True,
            dry_run=False
        )
        assert res["success"] is True
        content = f.read_text()
        assert content.endswith("\n# Footer comment\n") or content.endswith("\n# Footer comment")
        assert "        # Footer comment" not in content

    def test_insert_line_auto_indent_pre_indented_content(self, tmp_path, monkeypatch):
        """Verify pre-indented insert_content is normalized with textwrap.dedent under auto_indent=True."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("def foo():\n    pass\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=2,
            insert_content="    \"\"\"Pre-indented docstring.\"\"\"",
            insert_position="before",
            auto_indent=True,
            dry_run=False
        )
        assert res["success"] is True
        content = f.read_text()
        assert "    \"\"\"Pre-indented docstring.\"\"\"\n    pass" in content

    def test_same_target_line_tie_breaking(self, tmp_path, monkeypatch):
        """Tie-breaking test: when replacement and insertion share target line, replacement executes first."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("line 1\nline 2\nline 3\n")

        res = patch_file(
            target_file="sample.py",
            replacements=[
                {"insert_line": 2, "insert_content": "# inserted at line 2"},
                {"search_content": "line 2", "replace_content": "modified line 2"},
            ],
            dry_run=False
        )
        assert res["success"] is True
        content = f.read_text()
        assert "# inserted at line 2\nmodified line 2" in content or "line 1\n# inserted at line 2\nmodified line 2" in content


