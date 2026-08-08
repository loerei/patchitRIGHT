"""Unit tests for Workspace LRU caching and path resolution safety."""
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from patchitright_mcp.workspace import Workspace, _cached_resolve_allowed_base_dir, clear_workspace_cache


@pytest.fixture(autouse=True)
def setup_teardown_workspace_cache():
    clear_workspace_cache()
    yield
    clear_workspace_cache()


def test_allowed_base_dir_cache_hit_and_eviction(tmp_path):
    ws = Workspace(cwd=tmp_path)
    target = "test_file.py"

    # Initial call populates cache
    info0 = _cached_resolve_allowed_base_dir.cache_info()
    res1 = ws.resolve_allowed_base_dir(target)
    info1 = _cached_resolve_allowed_base_dir.cache_info()

    assert res1 == tmp_path
    assert info1.hits == info0.hits
    assert info1.misses == info0.misses + 1

    # Second call hits cache
    res2 = ws.resolve_allowed_base_dir(target)
    info2 = _cached_resolve_allowed_base_dir.cache_info()

    assert res2 == tmp_path
    assert info2.hits == info1.hits + 1
    assert info2.misses == info1.misses


def test_allowed_base_dir_repo_source_root_branch(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo_root"
    fake_root.mkdir()
    mock_resolve = MagicMock(return_value={"found": True, "source_root": str(fake_root)})

    import sys
    fake_module = MagicMock()
    fake_module.resolve_repo = mock_resolve
    monkeypatch.setitem(sys.modules, "jcodemunch_mcp.tools.resolve_repo", fake_module)

    ws = Workspace(cwd=tmp_path)
    res1 = ws.resolve_allowed_base_dir("src/main.py")
    assert res1 == fake_root.resolve()

    # Verify second call hits cache
    info1 = _cached_resolve_allowed_base_dir.cache_info()
    res2 = ws.resolve_allowed_base_dir("src/main.py")
    info2 = _cached_resolve_allowed_base_dir.cache_info()
    assert res2 == fake_root.resolve()
    assert info2.hits == info1.hits + 1


def test_clear_workspace_cache(tmp_path):
    ws = Workspace(cwd=tmp_path)
    ws.resolve_allowed_base_dir("sample.py")
    assert _cached_resolve_allowed_base_dir.cache_info().currsize > 0

    clear_workspace_cache()
    assert _cached_resolve_allowed_base_dir.cache_info().currsize == 0

    # Post-clear call causes a new cache miss and repopulates cache
    info_before = _cached_resolve_allowed_base_dir.cache_info()
    ws.resolve_allowed_base_dir("sample.py")
    info_after = _cached_resolve_allowed_base_dir.cache_info()

    assert info_after.misses == info_before.misses + 1
    assert info_after.currsize == 1
