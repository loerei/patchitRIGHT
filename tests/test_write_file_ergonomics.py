from __future__ import annotations

from pathlib import Path
import pytest

from patchitright_mcp.patch_file import write_file
from patchitright_mcp.server import _execute_write_file
from patchitright_mcp.tool_schemas import get_tool_schemas


def test_write_file_content_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = write_file(target_file="sample.txt", content="hello from content alias")
    assert res.get("status") == "success" or res.get("success") is True or "files" in res
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "hello from content alias"


def test_write_file_path_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = write_file(path="sample_path.txt", code_content="hello from path alias")
    assert res.get("status") == "success" or res.get("success") is True or "files" in res
    assert (tmp_path / "sample_path.txt").read_text(encoding="utf-8") == "hello from path alias"


def test_write_file_content_and_path_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = write_file(path="sample_both.txt", content="both aliases work")
    assert res.get("status") == "success" or res.get("success") is True or "files" in res
    assert (tmp_path / "sample_both.txt").read_text(encoding="utf-8") == "both aliases work"


def test_write_file_overwrite_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing.txt").write_text("initial", encoding="utf-8")

    # Without overwrite alias -> should fail
    fail_res = write_file(path="existing.txt", content="attempt", overwrite=False)
    assert fail_res.get("success") is False or "error" in fail_res
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "initial"

    # With overwrite=True -> succeeds
    ok_res = write_file(path="existing.txt", content="overwritten", overwrite=True)
    assert ok_res.get("status") == "success" or ok_res.get("success") is True or "files" in ok_res
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "overwritten"


def test_write_file_batch_files_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    batch = [
        {"path": "file1.txt", "content": "content 1"},
        {"target_file": "file2.txt", "code_content": "content 2"},
    ]
    res = write_file(files=batch)
    assert res.get("success") is not False and "error" not in res
    assert (tmp_path / "file1.txt").read_text(encoding="utf-8") == "content 1"
    assert (tmp_path / "file2.txt").read_text(encoding="utf-8") == "content 2"


def test_write_file_batch_files_atomic_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    batch = [
        {"path": "good.py", "content": "x = 1\n"},
        {"path": "bad.py", "content": "def unclosed(\n"},
    ]
    res = write_file(files=batch)
    assert res.get("success") is False or "error" in res
    # Atomic rollback: good.py must NOT exist on disk!
    assert not (tmp_path / "good.py").exists()
    assert not (tmp_path / "bad.py").exists()


def test_server_execute_write_file_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Call _execute_write_file with path and content
    args = {"path": "server_alias.txt", "content": "server content"}
    contents = _execute_write_file(args)
    assert len(contents) == 1
    assert (tmp_path / "server_alias.txt").read_text(encoding="utf-8") == "server content"

    # Call _execute_write_file with files
    batch_args = {
        "files": [
            {"path": "batch_server_1.txt", "content": "b1"},
            {"target_file": "batch_server_2.txt", "content": "b2"},
        ]
    }
    contents_batch = _execute_write_file(batch_args)
    assert len(contents_batch) == 1
    assert (tmp_path / "batch_server_1.txt").read_text(encoding="utf-8") == "b1"
    assert (tmp_path / "batch_server_2.txt").read_text(encoding="utf-8") == "b2"


def test_write_file_schema_advertises_ergonomics() -> None:
    tools = get_tool_schemas()
    write_tool = next((t for t in tools if t.name == "write_file"), None)
    assert write_tool is not None
    props = write_tool.inputSchema.get("properties", {})
    assert "content" in props
    assert "path" in props
    assert "overwrite" in props
    assert "files" in props
