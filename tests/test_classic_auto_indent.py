"""Tests for Smart Indent Alignment in classic search-and-replace and did_you_mean."""

import pytest
from patchitright_mcp.patch_file import patch_file
from patchitright_mcp.engine import PatchEngine


class TestClassicAutoIndent:

    def test_classic_patch_auto_indent_flat_replace(self, tmp_path, monkeypatch):
        """Classic patch automatically rebases 0-indented replace_content to match search_content indentation."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "calc.py"
        test_file.write_text(
            "def calculate():\n"
            "    a = 1\n"
            "    b = 2\n"
            "    return a + b\n"
        )

        # Agent provides 0-indent replace_content
        res = patch_file(
            target_file="calc.py",
            search_content="    a = 1\n    b = 2",
            replace_content="a = 10\nb = 20",
            auto_indent=True,
        )

        assert res.get("success") is True
        content = test_file.read_text()
        assert content == (
            "def calculate():\n"
            "    a = 10\n"
            "    b = 20\n"
            "    return a + b\n"
        )

    def test_classic_patch_auto_indent_preserves_relative_indent(self, tmp_path, monkeypatch):
        """Classic patch auto-indent preserves internal relative indentation of nested blocks."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "proc.py"
        test_file.write_text(
            "class Processor:\n"
            "    def process(self, data):\n"
            "        # old code\n"
            "        return None\n"
        )

        # Agent provides flat base with relative internal 4-space indent
        res = patch_file(
            target_file="proc.py",
            search_content="        # old code\n        return None",
            replace_content=(
                "if data:\n"
                "    return data.strip()\n"
                "return None"
            ),
            auto_indent=True,
        )

        assert res.get("success") is True
        content = test_file.read_text()
        assert content == (
            "class Processor:\n"
            "    def process(self, data):\n"
            "        if data:\n"
            "            return data.strip()\n"
            "        return None\n"
        )

    def test_classic_patch_auto_indent_disabled(self, tmp_path, monkeypatch):
        """When auto_indent=False, replace_content is placed verbatim without re-indentation."""
        engine = PatchEngine(
            "def foo():\n    return 1\n",
            "dummy.py",
            bypass_validation=True,
        )
        # With auto_indent=False, 0-indent is kept verbatim
        patched, count = engine.apply_classic_patch(
            search_content="    return 1",
            replace_content="return 2",
            auto_indent=False,
            validate=False,
        )
        assert count == 1
        assert patched == "def foo():\nreturn 2\n"

    def test_classic_patch_did_you_mean_auto_rebase(self, tmp_path, monkeypatch):
        """When did_you_mean applies and target on disk has different indent, auto-rebase to disk indent."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "nested.py"
        test_file.write_text(
            "class Container:\n"
            "    def run(self):\n"
            "        val = 'hello_world'\n"
            "        return val\n"
        )

        # Agent searched with 4-space indent and typo, while disk is 8-space indent
        res = patch_file(
            target_file="nested.py",
            search_content="    val = 'hello_worl'",
            replace_content="    val = 'new_val'",
            did_you_mean=True,
            auto_indent=True,
        )

        assert res.get("success") is True
        content = test_file.read_text()
        assert content == (
            "class Container:\n"
            "    def run(self):\n"
            "        val = 'new_val'\n"
            "        return val\n"
        )

    def test_replacements_batch_auto_indent(self, tmp_path, monkeypatch):
        """replacements array applies auto_indent per item."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "batch.py"
        test_file.write_text(
            "def f():\n"
            "    x = 1\n"
            "    y = 2\n"
            "    return x + y\n"
        )

        res = patch_file(
            target_file="batch.py",
            replacements=[
                {
                    "search_content": "    x = 1",
                    "replace_content": "x = 10",
                    "auto_indent": True,
                },
                {
                    "search_content": "    y = 2",
                    "replace_content": "y = 20",
                    "auto_indent": True,
                },
            ],
        )

        assert res.get("success") is True
        content = test_file.read_text()
        assert content == (
            "def f():\n"
            "    x = 10\n"
            "    y = 20\n"
            "    return x + y\n"
        )
