"""Tests for RunCache — the in-memory dry-run result store."""

import time
import pytest

from patchitright_mcp.run_cache import RunCache


class TestRunCacheStoreConsume:
    """Cycle 1 — store() returns a run_id, consume() pops it."""

    def test_store_returns_nonempty_run_id(self, tmp_path):
        cache = RunCache()
        f = tmp_path / "a.py"
        f.write_text("hello")
        run_id = cache.store(
            entries=[{"target_path": f, "patched_content": "world"}],
            original_contents={"a.py": "hello"},
        )
        assert isinstance(run_id, str)
        assert len(run_id) > 0

    def test_consume_returns_entry_and_removes_it(self, tmp_path):
        cache = RunCache()
        f = tmp_path / "a.py"
        f.write_text("hello")
        run_id = cache.store(
            entries=[{"target_path": f, "patched_content": "world"}],
            original_contents={"a.py": "hello"},
        )
        entry = cache.consume(run_id)
        assert entry is not None
        assert len(entry["files"]) == 1
        assert entry["files"][0]["patched_content"] == "world"
        assert entry["files"][0]["target_path"] == f

    def test_consume_is_single_use(self, tmp_path):
        cache = RunCache()
        f = tmp_path / "a.py"
        f.write_text("hello")
        run_id = cache.store(
            entries=[{"target_path": f, "patched_content": "world"}],
            original_contents={"a.py": "hello"},
        )
        cache.consume(run_id)
        # Second consume must return None
        assert cache.consume(run_id) is None

    def test_run_ids_are_unique(self, tmp_path):
        cache = RunCache()
        f = tmp_path / "a.py"
        f.write_text("x")
        id1 = cache.store([{"target_path": f, "patched_content": "y"}], {"a.py": "x"})
        id2 = cache.store([{"target_path": f, "patched_content": "z"}], {"a.py": "x"})
        assert id1 != id2


class TestRunCacheExpiry:
    """Cycle 2 — consume() returns None for unknown or expired run_id."""

    def test_consume_unknown_run_id_returns_none(self):
        cache = RunCache()
        assert cache.consume("does-not-exist") is None

    def test_consume_expired_entry_returns_none(self, tmp_path):
        cache = RunCache(ttl=1)  # 1-second TTL
        f = tmp_path / "a.py"
        f.write_text("hello")
        run_id = cache.store(
            entries=[{"target_path": f, "patched_content": "world"}],
            original_contents={"a.py": "hello"},
        )
        time.sleep(1.1)
        assert cache.consume(run_id) is None

    def test_original_hashes_stored_per_file(self, tmp_path):
        """consume() result includes original_hash for each file so apply can guard."""
        cache = RunCache()
        f = tmp_path / "a.py"
        content = "hello"
        f.write_text(content)
        run_id = cache.store(
            entries=[{"target_path": f, "patched_content": "world"}],
            original_contents={"a.py": content},
        )
        entry = cache.consume(run_id)
        assert "original_hash" in entry["files"][0]
