"""Tests for line-based insertion and indentation heuristics."""

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
            dry_run=False
        )
        assert res1["success"] is True
        assert f.read_text() == "import sys\nline 1\nline 2\nline 3\n"

        # Insert before line 3 (which is now 'line 2')
        res2 = patch_file(
            target_file="sample.py",
            insert_line=3,
            insert_content="# header comment",
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


    def test_insert_line_simultaneous_line_and_symbol_prohibited(self, tmp_path, monkeypatch):
        """Providing both insert_line/insert_content and symbol_name in a single item raises ValueError."""
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
        assert "Cannot combine 'insert_content'" in res["error"]


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


    def test_insert_line_tab_space_mismatch_warning(self, tmp_path, monkeypatch):
        """Verify tab vs space mismatch warning when auto_indent=False."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("    a = 1\n    b = 2\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=2,
            insert_content="\tc = 3",
            auto_indent=False,
            dry_run=False
        )
        assert res["success"] is True
        assert any("contains tabs while auto_indent=False" in w for w in res.get("warnings", []))


    def test_insert_line_out_of_bounds_clamping_warning(self, tmp_path, monkeypatch):
        """Verify insert_line > total_lines emits a clamping warning in the response."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("line 1\nline 2\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=100,
            insert_content="# Clamped footer",
            auto_indent=True,
            dry_run=False
        )
        assert res["success"] is True
        assert "warnings" in res
        assert any("exceeds total file lines" in w for w in res["warnings"])


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


    def test_insert_line_pre_indented_content(self, tmp_path, monkeypatch):
        """Verify pre-indented insert_content is normalized with textwrap.dedent under auto_indent=True."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sample.py"
        f.write_text("def foo():\n    pass\n")

        res = patch_file(
            target_file="sample.py",
            insert_line=2,
            insert_content="    \"\"\"Pre-indented docstring.\"\"\"",
            auto_indent=True,
            dry_run=False
        )
        assert res["success"] is True
        content = f.read_text()
        assert "    \"\"\"Pre-indented docstring.\"\"\"\n    pass" in content


    def test_patch_file_symbol_omission_warning_end_to_end(self, tmp_path, monkeypatch):
        """End-to-end test verifying patch_file output contains symbol omission warnings."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "sorter.ts"
        code = """
        const firstIndex = 1;
        let lastIndex = 2;
        console.log(firstIndex + lastIndex);
        """
        f.write_text(code)

        res = patch_file(
            target_file="sorter.ts",
            search_content="        const firstIndex = 1;\n        let lastIndex = 2;",
            replace_content="        // deleted indices",
            dry_run=False
        )

        assert res["success"] is True
        assert "warnings" in res
        warning_str = "\n".join(res["warnings"])
        assert "Symbol Omission Alert" in warning_str
        assert "firstIndex" in warning_str


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

