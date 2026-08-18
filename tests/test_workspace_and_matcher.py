"""Unit tests for workspace path resolution, LRU caching, and AST line matcher collision checks."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patchitright_mcp.line_matcher import (
    _resolve_ast_boundaries,
    check_replacement_collisions,
    sort_resolved_items_descending,
)
from patchitright_mcp.workspace import (
    Workspace,
    _cached_resolve_allowed_base_dir,
    clear_workspace_cache,
)


def test_workspace_lru_cache_and_clear():
    """Workspace LRU cache caches directory resolutions and clears properly."""
    clear_workspace_cache()
    cwd_str = "d:/Projects/patchitRIGHT"
    tf_norm = "src/index.ts"

    res1 = _cached_resolve_allowed_base_dir(cwd_str, tf_norm)
    res2 = _cached_resolve_allowed_base_dir(cwd_str, tf_norm)
    assert res1 == res2

    # Clear cache
    clear_workspace_cache()


def test_workspace_jcodemunch_fallback_exceptions(monkeypatch):
    """Workspace degrades gracefully when jCodeMunch throws unexpected exceptions."""
    clear_workspace_cache()
    cwd = Path("d:/Projects/patchitRIGHT")
    ws = Workspace(cwd)

    with patch("jcodemunch_mcp.tools.resolve_repo.resolve_repo", side_effect=Exception("Database lock error")):
        resolved = ws.resolve_allowed_base_dir("file.py")
        assert resolved == cwd.resolve()


def test_workspace_find_workspace_root_anchors(tmp_path):
    """find_workspace_root walks up and identifies git, package.json, or pyproject.toml."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n")

    sub = root / "src" / "deep" / "pkg"
    sub.mkdir(parents=True)

    ws = Workspace(root)
    found_root = ws.find_workspace_root(sub / "mod.py")
    assert found_root == root.resolve()


def test_workspace_resolve_safe_path_security(tmp_path):
    """resolve_safe_path blocks traversal sequences and resolves safe paths."""
    ws = Workspace(tmp_path)

    # 1. Traversal blocked
    with pytest.raises(ValueError, match="fatal_context_mismatch"):
        ws.resolve_safe_path("../outside.py")

    # 2. Relative path resolved safely
    rel_path = ws.resolve_safe_path("src/app.py")
    assert rel_path == (tmp_path / "src" / "app.py").resolve()

    # 3. Absolute path resolved
    abs_target = tmp_path / "absolute_file.txt"
    abs_path = ws.resolve_safe_path(str(abs_target))
    assert abs_path == abs_target.resolve()


def test_line_matcher_resolve_ast_fallback_regex(tmp_path):
    """_resolve_ast_boundaries falls back to regex matching when index is missing."""
    f = tmp_path / "app.py"
    f.write_text(
        "def calculate_total(a, b):\n"
        "    res = a + b\n"
        "    return res\n\n"
        "def other():\n"
        "    pass\n"
    )

    # 1. Fallback regex finding function boundary when index is None
    with patch("jcodemunch_mcp.tools.resolve_repo.resolve_repo", return_value={"found": True, "repo": "local/test"}), \
         patch("jcodemunch_mcp.storage.IndexStore.load_index", return_value=None):
        s_line, e_line, err, body_range = _resolve_ast_boundaries(
            cwd=tmp_path,
            target_path=f,
            symbol_name="calculate_total",
            storage_path=None,
            start_line=None,
            end_line=None,
            symbol_scope="boundary"
        )
        assert s_line == 1
        assert e_line == 4
        assert err is None

    # 2. Symbol not found in unindexed file returns error
    with patch("jcodemunch_mcp.tools.resolve_repo.resolve_repo", return_value={"found": False}):
        s_line, e_line, err, _body_range = _resolve_ast_boundaries(
            cwd=tmp_path,
            target_path=f,
            symbol_name="non_existent_func",
            storage_path=None,
            start_line=None,
            end_line=None,
        )
        assert s_line is None
        assert "not indexed" in err["error"]


def test_line_matcher_indexed_symbol_scope_body(tmp_path):
    """_resolve_ast_boundaries resolves symbol from index with symbol_scope=body."""
    f = tmp_path / "math.py"
    f.write_text("def add(x, y):\n    return x + y\n")

    mock_index = MagicMock()
    mock_index.source_root = str(tmp_path)
    mock_index.symbols = [
        {"name": "add", "file": "math.py", "line": 1, "end_line": 2}
    ]

    with patch("jcodemunch_mcp.tools.resolve_repo.resolve_repo", return_value={"found": True, "repo": "local/test", "source_root": str(tmp_path)}), \
         patch("jcodemunch_mcp.storage.IndexStore.load_index", return_value=mock_index):

        s_line, e_line, _err, body_range = _resolve_ast_boundaries(
            cwd=tmp_path,
            target_path=f,
            symbol_name="add",
            storage_path=None,
            start_line=None,
            end_line=None,
            symbol_scope="body"
        )
        assert s_line == 2  # body line 2
        assert e_line == 2
        assert body_range is not None
        assert _err is None


def test_line_matcher_collision_detection():
    """check_replacement_collisions detects overlaps and insertions inside replacement blocks."""
    # 1. No collision
    items_ok = [
        {"start_line": 1, "end_line": 5, "is_insertion": False},
        {"start_line": 10, "end_line": 15, "is_insertion": False},
        {"insert_line": 8, "start_line": 8, "is_insertion": True}
    ]
    assert check_replacement_collisions(items_ok) is None

    # 2. Overlapping replacements
    items_overlap = [
        {"start_line": 1, "end_line": 10, "is_insertion": False},
        {"start_line": 8, "end_line": 15, "is_insertion": False}
    ]
    err_overlap = check_replacement_collisions(items_overlap)
    assert "Overlapping replacements" in err_overlap["error"]

    # 3. Insertion inside replacement range
    items_ins_inside = [
        {"start_line": 1, "end_line": 10, "is_insertion": False},
        {"insert_line": 5, "start_line": 5, "is_insertion": True}
    ]
    err_ins = check_replacement_collisions(items_ins_inside)
    assert "Cannot insert code inside an active replacement range" in err_ins["error"]


def test_sort_resolved_items_descending():
    """sort_resolved_items_descending sorts by start_line desc, replacements before insertions."""
    items = [
        {"start_line": 5, "is_insertion": True, "insert_line": 5},
        {"start_line": 5, "is_insertion": False, "end_line": 10},
        {"start_line": 20, "is_insertion": False, "end_line": 25},
    ]
    sorted_res = sort_resolved_items_descending(items)
    assert sorted_res[0]["start_line"] == 20
    assert sorted_res[1]["is_insertion"] is False  # replacement before insertion on line 5
    assert sorted_res[2]["is_insertion"] is True
