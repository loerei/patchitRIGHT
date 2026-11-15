"""Unit tests for Workspace LRU caching and path resolution safety."""
from pathlib import Path
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


def test_allowed_base_dir_ignores_storage_path_and_returns_cwd(tmp_path):
    ignored_db = tmp_path / "ignored_index"
    ignored_db.mkdir()

    ws = Workspace(cwd=tmp_path, storage_path=str(ignored_db))
    assert ws.base_dir == tmp_path.resolve()

    res1 = ws.resolve_allowed_base_dir("src/main.py")
    assert res1 == tmp_path.resolve()

    # Verify second call hits cache
    info1 = _cached_resolve_allowed_base_dir.cache_info()
    res2 = ws.resolve_allowed_base_dir("src/main.py")
    info2 = _cached_resolve_allowed_base_dir.cache_info()
    assert res2 == tmp_path.resolve()
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
