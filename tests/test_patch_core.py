"""Tests for core patch operations, AST scopes, and line filters."""

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


class TestPatchCore:

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



class TestPatchEngineCore:

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



class TestSymbolScopeCore:

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


    def test_replacements_pipeline_syntax_validation_error(self, tmp_path, monkeypatch):
        """Replacements pipeline catches SyntaxValidationError and formats error response."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "calc.py"
        app_file.write_text("def add(a, b):\n    return a + b\n")

        res = patch_file(
            target_file="calc.py",
            replacements=[
                {"search_content": "return a + b", "replace_content": "return a +"}
            ],
            dry_run=False
        )
        assert "error" in res
        assert "Syntax Error" in res["error"]
        assert app_file.read_text() == "def add(a, b):\n    return a + b\n"


