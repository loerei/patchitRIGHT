"""Tests for fuzzy matching heuristics and out-of-scope relocation."""

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


class TestFuzzyAndRelocation:

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


    def test_detect_mismatch_reason_whitespace(self):
        """PatchEngine must detect whitespace/indentation mismatch in suggestion."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("    line 1\n    line 2\n", "test.py")
        with pytest.raises(ValueError) as exc:
            # The search content lacks indentation
            engine.apply_classic_patch("line 1\nline 2", "modified")
        assert "Mismatch due to indentation or whitespace differences" in str(exc.value)


    def test_detect_mismatch_reason_escaped(self):
        """PatchEngine must detect raw escape character mismatch in suggestion."""
        from patchitright_mcp.engine import PatchEngine
        engine = PatchEngine("line 1\nline 2\nline 3\n", "test.py")
        with pytest.raises(ValueError) as exc:
            # The file has literal newlines, but we search using escaped \\n
            engine.apply_classic_patch("line 1\\nline 2", "modified")
        assert "Mismatch due to raw escape characters" in str(exc.value)


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

