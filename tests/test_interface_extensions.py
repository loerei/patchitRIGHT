"""End-to-end tests for interface extensions: broadcasts, occurrence assertions, guarded ranges, and cross-file slicing."""

from __future__ import annotations

import json
import pytest

from patchitright_mcp.patch_file import patch_file, write_file
from patchitright_mcp.server import call_tool


class TestTargetBroadcastAndSkip:
    """Tests for target_files, target_glob, files[] default inheritance, and skip_unmatched."""

    def test_target_files_strict_failure_vs_skip_unmatched(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f3 = tmp_path / "c.py"
        f1.write_text("LEGACY = 1\n")
        f2.write_text("MODERN = 2\n")
        f3.write_text("LEGACY = 3\n")

        strict_fail = patch_file(
            target_files=["a.py", "b.py", "c.py"],
            search_content="LEGACY",
            replace_content="UPDATED",
        )
        assert "error" in strict_fail
        assert f1.read_text() == "LEGACY = 1\n"
        assert f3.read_text() == "LEGACY = 3\n"

        res_skip = patch_file(
            target_files=["a.py", "b.py", "c.py"],
            search_content="LEGACY",
            replace_content="UPDATED",
            skip_unmatched=True,
        )
        assert res_skip["success"] is True
        assert f1.read_text() == "UPDATED = 1\n"
        assert f2.read_text() == "MODERN = 2\n"
        assert f3.read_text() == "UPDATED = 3\n"
        assert [s["target_file"] for s in res_skip["skipped_files"]] == ["b.py"]
        assert len(res_skip["modified_files"]) == 2

        all_skipped = patch_file(
            target_files=["b.py"],
            search_content="NON_EXISTENT",
            replace_content="X",
            skip_unmatched=True,
        )
        assert "error" in all_skipped
        assert "all files skipped" in all_skipped["error"].lower()

    def test_target_glob_deterministic_and_excludes_ignored_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "one.py").write_text("OLD_FLAG = False\n")
        (tmp_path / "src" / "two.py").write_text("OLD_FLAG = False\n")
        (tmp_path / ".venv" / "lib").mkdir(parents=True)
        (tmp_path / ".venv" / "lib" / "ignored.py").write_text("OLD_FLAG = False\n")
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "node_modules" / "pkg" / "ignored.py").write_text("OLD_FLAG = False\n")

        res = patch_file(
            target_glob="**/*.py",
            search_content="OLD_FLAG",
            replace_content="NEW_FLAG",
        )
        assert res["success"] is True
        assert (tmp_path / "src" / "one.py").read_text() == "NEW_FLAG = False\n"
        assert (tmp_path / "src" / "two.py").read_text() == "NEW_FLAG = False\n"
        assert (tmp_path / ".venv" / "lib" / "ignored.py").read_text() == "OLD_FLAG = False\n"
        assert (tmp_path / "node_modules" / "pkg" / "ignored.py").read_text() == "OLD_FLAG = False\n"

        empty_glob = patch_file(
            target_glob="nonexistent/**/*.py",
            search_content="A",
            replace_content="B",
        )
        assert "error" in empty_glob
        assert "matched zero files" in empty_glob["error"]

    def test_files_array_inherits_top_level_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "m1.py"
        f2 = tmp_path / "m2.py"
        f1.write_text("TOKEN = 'old'\n")
        f2.write_text("TOKEN = 'old'\n")

        res = patch_file(
            search_content="TOKEN = 'old'",
            replace_content="TOKEN = 'shared'",
            files=[
                {"target_file": "m1.py"},
                {"target_file": "m2.py", "replace_content": "TOKEN = 'override'"},
            ],
        )
        assert res["success"] is True
        assert f1.read_text() == "TOKEN = 'shared'\n"
        assert f2.read_text() == "TOKEN = 'override'\n"


class TestExpectedOccurrencesAndGuardedRanges:
    """Tests for expected_occurrences assertions and guarded line-range replacements."""

    def test_expected_occurrences_exact_mismatch_and_conflict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "occ.py"
        target.write_text("a = V1\nb = V1\nc = 3\n")

        conflict = patch_file(
            target_file="occ.py",
            search_content="V1",
            replace_content="V2",
            expected_occurrences=2,
            allow_multiple=False,
        )
        assert "error" in conflict
        assert "conflicts" in conflict["error"].lower()

        under_match = patch_file(
            target_file="occ.py",
            search_content="V1",
            replace_content="V2",
            expected_occurrences=3,
        )
        assert "error" in under_match
        assert "Expected 3" in under_match["error"]
        assert "found 2" in under_match["error"]
        assert target.read_text() == "a = V1\nb = V1\nc = 3\n"

        ok = patch_file(
            target_file="occ.py",
            search_content="V1",
            replace_content="V2",
            expected_occurrences=2,
        )
        assert ok["success"] is True
        assert ok["occurrences"] == 2
        assert target.read_text() == "a = V2\nb = V2\nc = 3\n"

    def test_guarded_line_range_replacement_and_clean_deletion(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "ranges.py"
        target.write_text("line1 = 1\nold_start = 2\nold_mid = 3\nold_end = 4\nline5 = 5\n")

        unguarded = patch_file(
            target_file="ranges.py",
            start_line=2,
            end_line=4,
            replace_content="replaced = 0",
        )
        assert "error" in unguarded
        assert "require" in unguarded["error"].lower()

        bad_guard = patch_file(
            target_file="ranges.py",
            start_line=2,
            end_line=4,
            start_anchor="wrong_anchor",
            replace_content="replaced = 0",
        )
        assert "error" in bad_guard
        assert "old_start = 2" in bad_guard["error"]

        delete_res = patch_file(
            target_file="ranges.py",
            start_line=2,
            end_line=4,
            start_anchor="old_start = 2",
            end_anchor="old_end = 4",
            replace_content="",
        )
        assert delete_res["success"] is True
        assert target.read_text() == "line1 = 1\nline5 = 5\n"

        replace_res = patch_file(
            target_file="ranges.py",
            start_line=2,
            end_line=2,
            line_filter="line5 = 5",
            replace_content="final_line = 50",
        )
        assert replace_res["success"] is True
        assert target.read_text() == "line1 = 1\nfinal_line = 50\n"


class TestCrossFileResearchExtractionWorkflows:
    """Tests covering research.md line-range extraction into new files and replacement into existing files."""

    def test_extract_research_slice_into_new_file_and_replace_into_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        research = tmp_path / "research.md"
        lines = [f"// header {i}" for i in range(1, 74)]
        guard_lines = [
            "export function checkGuard(x: number): boolean {",
            "  return x > 0;",
            "}",
        ]
        mid_lines = [f"// spacer {i}" for i in range(77, 100)]
        update_lines = [
            "export function updateRecord(id: string): string {",
            "  return `updated:${id}`;",
            "}",
        ]
        research.write_text("\n".join(lines + guard_lines + mid_lines + update_lines) + "\n", encoding="utf-8")

        # 1. Extract slice L74..L76 into new guard.ts via write_file (bypass TS external binary dependency by .py? Or test .ts directly!)
        res_new = write_file(
            target_file="guard.ts",
            source_file="research.md",
            source_start_line=74,
            source_end_line=76,
            source_start_anchor="export function checkGuard",
            source_end_anchor="}",
        )
        assert res_new["success"] is True
        assert (tmp_path / "guard.ts").read_text(encoding="utf-8") == (
            "export function checkGuard(x: number): boolean {\n  return x > 0;\n}\n"
        )

        # 2. Replace old function in update.ts using slice L100..L102 from research.md via patch_file
        update_ts = tmp_path / "update.ts"
        update_ts.write_text(
            "export function updateRecord(id: string): string {\n"
            "  return id;\n"
            "}\n",
            encoding="utf-8",
        )
        res_patch = patch_file(
            target_file="update.ts",
            search_content="export function updateRecord(id: string): string {\n  return id;\n}",
            source_file="research.md",
            source_start_line=100,
            source_end_line=102,
            source_start_anchor="export function updateRecord",
        )
        assert res_patch["success"] is True
        assert "return `updated:${id}`;" in update_ts.read_text(encoding="utf-8")

    def test_cross_file_replacements_item_slice_and_cut_source_rollback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "snippets.py"
        src.write_text(
            "A = 1\n"
            "B = 200\n"
            "C = 3\n",
            encoding="utf-8",
        )
        target = tmp_path / "consumer.py"
        target.write_text("X = 0\nY = 1\n")

        # Rollback: if target patch fails (search_content not found), cut_source must NOT modify snippets.py
        fail_cut = patch_file(
            target_file="consumer.py",
            search_content="MISSING_VAR = 0",
            source_file="snippets.py",
            source_start_line=2,
            source_end_line=2,
            cut_source=True,
        )
        assert "error" in fail_cut
        assert src.read_text() == "A = 1\nB = 200\nC = 3\n"

        # Two conflicting cut_source operations against same source_file must be rejected
        conflict_cut = patch_file(
            target_file="consumer.py",
            replacements=[
                {"search_content": "X = 0", "source_file": "snippets.py", "source_start_line": 1, "source_end_line": 1, "cut_source": True},
                {"search_content": "Y = 1", "source_file": "snippets.py", "source_start_line": 2, "source_end_line": 2, "cut_source": True},
            ],
        )
        assert "error" in conflict_cut

        # Valid replacements[] item pulling replace_content from source_file slice + cut_source
        ok_res = patch_file(
            target_file="consumer.py",
            replacements=[
                {"search_content": "X = 0", "source_file": "snippets.py", "source_start_line": 2, "source_end_line": 2, "cut_source": True},
                {"search_content": "Y = 1", "replace_content": "Y = 9"},
            ],
        )
        assert ok_res["success"] is True
        assert target.read_text() == "B = 200\nY = 9\n"
        assert src.read_text() == "A = 1\nC = 3\n"


@pytest.mark.asyncio
async def test_mcp_call_tool_end_to_end_extensions(tmp_path, monkeypatch):
    """MCP call_tool handles write_file slice dry_run->apply and patch_file broadcast."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "one.py").write_text("VER = 'v1'\n")
    (tmp_path / "src" / "two.py").write_text("VER = 'v1'\n")

    raw_patch = await call_tool("patch_file", {
        "target_glob": "src/*.py",
        "search_content": "VER = 'v1'",
        "replace_content": "VER = 'v2'",
        "expected_occurrences": 1,
    })
    patch_payload = json.loads(raw_patch[0].text)
    assert patch_payload["success"] is True
    assert (tmp_path / "src" / "one.py").read_text() == "VER = 'v2'\n"
    assert (tmp_path / "src" / "two.py").read_text() == "VER = 'v2'\n"

    raw_write = await call_tool("write_file", {
        "target_file": "src/extracted.py",
        "source_file": "src/one.py",
        "source_start_line": 1,
        "source_end_line": 1,
        "dry_run": True,
    })
    write_payload = json.loads(raw_write[0].text)
    assert write_payload["success"] is True
    assert write_payload["dryRun"] is True

    raw_apply = await call_tool("apply_last_dry_run", {"run_id": write_payload["run_id"]})
    apply_payload = json.loads(raw_apply[0].text)
    assert apply_payload["success"] is True
    assert (tmp_path / "src" / "extracted.py").read_text() == "VER = 'v2'\n"
