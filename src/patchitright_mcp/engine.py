import re
import difflib
from typing import Optional, Union

class PatchEngine:
    """Core in-memory engine to apply search-and-replace and unified diff patches."""

    def __init__(self, file_content: str, filename: str):
        self.file_content = file_content
        self.filename = filename
        self.is_crlf = "\r\n" in file_content
        self.norm_content = file_content.replace("\r\n", "\n")
        self.file_lines = self.norm_content.split("\n")

    def apply_classic_patch(
        self,
        search_content: str,
        replace_content: str,
        allow_multiple: bool = False,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        symbol_boundaries: Optional[tuple[Optional[int], Optional[int]]] = None,
        symbol_name: Optional[str] = None,
        line_filter: Optional[Union[str, int]] = None,
    ) -> tuple[str, int]:
        """Applies a classic search-and-replace patch inside line/symbol scope."""
        norm_search = search_content.replace("\r\n", "\n")
        norm_replace = replace_content.replace("\r\n", "\n")

        # Determine boundaries
        resolved_start, resolved_end = start_line, end_line
        if symbol_boundaries:
            sym_start, sym_end = symbol_boundaries
            if sym_start is not None:
                resolved_start = sym_start
            if sym_end is not None:
                resolved_end = sym_end

        start_idx = (resolved_start - 1) if resolved_start is not None else 0
        end_idx = (resolved_end - 1) if resolved_end is not None else len(self.file_lines) - 1

        start_idx = max(0, min(start_idx, len(self.file_lines) - 1))
        end_idx = max(start_idx, min(end_idx, len(self.file_lines) - 1))

        # Check occurrences
        target_slice = "\n".join(self.file_lines[start_idx:end_idx + 1])
        occurrences = target_slice.count(norm_search)

        if occurrences == 0:
            first_lines = "\n".join(norm_search.split("\n")[:3])
            err_msg = f"Error: Search content not found inside the specified scope (lines {start_idx + 1} to {end_idx + 1})!\nFirst 3 lines of search block:\n{first_lines}"
            if symbol_name:
                err_msg += f"\nAST Scope: Symbol '{symbol_name}' at lines {start_idx + 1}-{end_idx + 1}"
            
            # Smart closest match search within the scope
            suggestion = self._find_closest_match(start_idx, end_idx, norm_search)
            if suggestion:
                s_start, s_end, s_text, s_ratio = suggestion
                err_msg += f"\n\nDid you mean (lines {s_start} to {s_end}, similarity {s_ratio:.0%}):\n{s_text}"
            raise ValueError(err_msg)

        if not allow_multiple and occurrences > 1:
            raise ValueError(
                f"Error: Search content occurs {occurrences} times within the specified scope (lines {start_idx + 1} to {end_idx + 1}). "
                "To replace all, set 'allow_multiple: true'."
            )

        # Line filter assertion
        if line_filter is not None:
            is_numeric = False
            try:
                assert_line_num = int(line_filter)
                is_numeric = True
            except (ValueError, TypeError):
                pass

            if is_numeric:
                match_index = target_slice.find(norm_search)
                before_match = target_slice[:match_index]
                lines_before_match = before_match.count("\n")
                actual_start_line = start_idx + 1 + lines_before_match
                if actual_start_line != assert_line_num:
                    raise ValueError(
                        f"Error: lineFilter assertion failed! The search content starts at line {actual_start_line}, but lineFilter asserted line {assert_line_num}."
                    )
            else:
                if str(line_filter) not in target_slice:
                    raise ValueError(
                        f"Error: lineFilter assertion failed! The target scope does not contain the substring '{line_filter}'."
                    )

        # Apply replacement
        patched_slice = target_slice.replace(norm_search, norm_replace)

        before_part = "\n".join(self.file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
        after_part = "\n" + "\n".join(self.file_lines[end_idx + 1:]) if end_idx < len(self.file_lines) - 1 else ""
        patched_file = before_part + patched_slice + after_part

        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file, occurrences

    def apply_unified_patch(self, patch_content: str) -> str:
        """Applies a Git-style Unified Diff patch (Fuzz = 0)."""
        norm_patch = patch_content.replace("\r\n", "\n")
        hunks = self._parse_unified_diff(norm_patch)
        if not hunks:
            raise ValueError("Error: No valid unified diff hunks found in patch_content.")

        file_lines = list(self.file_lines)
        offset = 0

        hunk_index = 0
        for hunk in hunks:
            hunk_index += 1
            expected_old_lines = [l_content for l_type, l_content in hunk['lines'] if l_type in (' ', '-')]
            expected_pos = hunk['old_start'] - 1 + offset

            match_success = True
            if expected_pos < 0 or expected_pos + len(expected_old_lines) > len(file_lines):
                match_success = False
            else:
                for idx, expected_line in enumerate(expected_old_lines):
                    if file_lines[expected_pos + idx] != expected_line:
                        match_success = False
                        break

            if not match_success:
                first_few_old = "\n".join(expected_old_lines[:3])
                err_msg = (
                    f"Error: Unified Diff hunk #{hunk_index} failed to match strictly at line {hunk['old_start']} "
                    f"(adjusted to line {expected_pos + 1} with cumulative offset {offset})!\n"
                    f"First 3 lines of expected old hunk:\n{first_few_old}"
                )

                suggestion = self._find_closest_hunk_match(expected_old_lines, expected_pos, file_lines)
                if suggestion:
                    s_line, s_ratio = suggestion
                    s_text = "\n".join(file_lines[s_line - 1 : s_line - 1 + len(expected_old_lines)])
                    err_msg += f"\n\nDid you mean (line {s_line}, similarity {s_ratio:.0%}):\n{s_text}"
                raise ValueError(err_msg)

            new_hunk_lines = []
            for l_type, l_content in hunk['lines']:
                if l_type in (' ', '+'):
                    new_hunk_lines.append(l_content)

            file_lines[expected_pos : expected_pos + len(expected_old_lines)] = new_hunk_lines
            hunk_offset = len(new_hunk_lines) - len(expected_old_lines)
            offset += hunk_offset

        patched_file = "\n".join(file_lines)
        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file

    def _find_closest_match(self, start_idx: int, end_idx: int, norm_search: str) -> Optional[tuple[int, int, str, float]]:
        search_lines = norm_search.split("\n")
        n = len(search_lines)
        best_ratio = 0.0
        best_slice = None
        best_range = None

        for window_size in (n, max(1, n - 1), n + 1):
            for i in range(start_idx, min(end_idx - window_size + 2, len(self.file_lines))):
                candidate_slice = "\n".join(self.file_lines[i : i + window_size])
                ratio = difflib.SequenceMatcher(None, candidate_slice, norm_search).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_slice = candidate_slice
                    best_range = (i + 1, i + window_size)

        if best_ratio >= 0.5 and best_range and best_slice:
            return best_range[0], best_range[1], best_slice, best_ratio
        return None

    def _find_closest_hunk_match(self, hunk_old_lines: list[str], start_search: int, file_lines: list[str]) -> Optional[tuple[int, float]]:
        search_str = "\n".join(hunk_old_lines)
        n = len(hunk_old_lines)
        if n == 0:
            return None
        best_ratio = 0.0
        best_line = None

        search_range = range(max(0, start_search - 20), min(len(file_lines) - n + 1, start_search + 20))
        if not list(search_range):
            search_range = range(len(file_lines) - n + 1)

        for i in search_range:
            candidate = "\n".join(file_lines[i : i + n])
            ratio = difflib.SequenceMatcher(None, candidate, search_str).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_line = i + 1

        if best_ratio < 0.5:
            for i in range(len(file_lines) - n + 1):
                candidate = "\n".join(file_lines[i : i + n])
                ratio = difflib.SequenceMatcher(None, candidate, search_str).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_line = i + 1

        if best_ratio >= 0.3 and best_line is not None:
            return best_line, best_ratio
        return None

    def _parse_unified_diff(self, patch_str: str) -> list[dict]:
        hunks = []
        lines = patch_str.splitlines()
        hunk_header_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
        current_hunk = None

        for line in lines:
            match = hunk_header_re.match(line)
            if match:
                old_start = int(match.group(1))
                old_lines = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_lines = int(match.group(4)) if match.group(4) else 1

                current_hunk = {
                    'old_start': old_start,
                    'old_lines': old_lines,
                    'new_start': new_start,
                    'new_lines': new_lines,
                    'lines': []
                }
                hunks.append(current_hunk)
                continue

            if current_hunk is not None:
                if line.startswith('-'):
                    current_hunk['lines'].append(('-', line[1:]))
                elif line.startswith('+'):
                    current_hunk['lines'].append(('+', line[1:]))
                elif line.startswith(' '):
                    current_hunk['lines'].append((' ', line[1:]))
                elif line == '':
                    current_hunk['lines'].append((' ', ''))
                else:
                    if line.startswith('--- ') or line.startswith('+++ ') or line.startswith('diff '):
                        current_hunk = None
                    else:
                        current_hunk['lines'].append((' ', line))
        return hunks
