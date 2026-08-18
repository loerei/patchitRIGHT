"""Unified diff parsing and hunk verification utilities for patchitRIGHT."""

from __future__ import annotations

import re
from typing import Optional


def parse_hunk_header(match: re.Match) -> dict:
    """Parse hunk header numbers from regex match object."""
    old_start = int(match.group(1))
    old_lines = int(match.group(2)) if match.group(2) else 1
    new_start = int(match.group(3))
    new_lines = int(match.group(4)) if match.group(4) else 1

    return {
        "old_start": old_start,
        "old_lines": old_lines,
        "new_start": new_start,
        "new_lines": new_lines,
        "lines": [],
    }


def parse_hunk_line(line: str, current_hunk: dict) -> Optional[dict]:
    """Parse line prefix and append to current hunk lines list."""
    if line.startswith("-"):
        current_hunk["lines"].append(("-", line[1:]))
    elif line.startswith("+"):
        current_hunk["lines"].append(("+", line[1:]))
    elif line.startswith(" "):
        current_hunk["lines"].append((" ", line[1:]))
    elif line == "":
        current_hunk["lines"].append((" ", ""))
    else:
        if line.startswith(("--- ", "+++ ", "diff ")):
            return None
        current_hunk["lines"].append((" ", line))
    return current_hunk


def parse_unified_diff(patch_str: str) -> list[dict]:
    """Parse Git-style Unified Diff patch string into a list of hunk dictionaries."""
    hunks = []
    lines = patch_str.splitlines()
    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    current_hunk = None

    for line in lines:
        match = hunk_header_re.match(line)
        if match:
            current_hunk = parse_hunk_header(match)
            hunks.append(current_hunk)
            continue

        if current_hunk is not None:
            current_hunk = parse_hunk_line(line, current_hunk)
    return hunks


def verify_hunk_match(
    expected_pos: int,
    expected_old_lines: list[str],
    file_lines: list[str],
) -> bool:
    """Verify that expected old hunk lines strictly match the target file lines."""
    if expected_pos == 0 and not expected_old_lines:
        return True
    if expected_pos < 0 or expected_pos + len(expected_old_lines) > len(file_lines):
        return False
    for idx, expected_line in enumerate(expected_old_lines):
        if file_lines[expected_pos + idx] != expected_line:
            return False
    return True
