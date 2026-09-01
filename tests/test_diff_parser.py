"""Unit tests for Unified Diff parsing and hunk verification."""

from patchitright_mcp.diff_parser import (
    parse_hunk_line,
    parse_unified_diff,
    verify_hunk_match,
)


def test_parse_unified_diff_headers_and_prefixes():
    """parse_unified_diff correctly extracts hunk headers, deletions, additions, and context."""
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,5 +1,6 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_new\n"
        "+line2_extra\n"
        " \n"
        "line4\n"
    )
    hunks = parse_unified_diff(patch_text)
    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk["old_start"] == 1
    assert hunk["old_lines"] == 5
    assert hunk["new_start"] == 1
    assert hunk["new_lines"] == 6
    assert len(hunk["lines"]) == 6
    assert hunk["lines"][1] == ("-", "line2")
    assert hunk["lines"][2] == ("+", "line2_new")


def test_parse_hunk_line_headers_and_empty():
    """parse_hunk_line skips diff headers and handles empty lines."""
    hunk = {"lines": []}
    assert parse_hunk_line("diff --git a b", hunk) is None

    res = parse_hunk_line("", hunk)
    assert res is not None
    assert hunk["lines"][-1] == (" ", "")

    res_plain = parse_hunk_line("plain context line", hunk)
    assert res_plain is not None
    assert hunk["lines"][-1] == (" ", "plain context line")


def test_verify_hunk_match_bounds():
    """verify_hunk_match tests bounds, empty hunks, and line equality."""
    file_lines = ["a", "b", "c"]

    # 1. Empty hunk at position 0
    assert verify_hunk_match(0, [], file_lines) is True

    # 2. Out of bounds
    assert verify_hunk_match(-1, ["a"], file_lines) is False
    assert verify_hunk_match(2, ["b", "c", "d"], file_lines) is False

    # 3. Matching vs mismatching
    assert verify_hunk_match(1, ["b", "c"], file_lines) is True
    assert verify_hunk_match(1, ["b", "mismatch"], file_lines) is False
