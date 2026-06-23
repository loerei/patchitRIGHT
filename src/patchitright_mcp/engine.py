import re
from typing import Optional, Union
from rapidfuzz import fuzz

class PatchEngine:
    """Core in-memory engine to apply search-and-replace and unified diff patches."""

    def __init__(self, file_content: str, filename: str):
        self.file_content = file_content
        self.filename = filename
        self.is_crlf = "\r\n" in file_content
        self.norm_content = file_content.replace("\r\n", "\n")
        self.file_lines = self.norm_content.split("\n")
        self.is_did_you_mean_applied = False
        self.s_ratio = 0.0
        self.did_you_mean_start_line = None
        self.did_you_mean_end_line = None
        self.is_did_you_mean_applied = False
        self.s_ratio = 0.0
        self.did_you_mean_start_line = None
        self.did_you_mean_end_line = None

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
        did_you_mean: bool = False,
    ) -> tuple[str, int]:
        """Applies a classic search-and-replace patch inside line/symbol scope."""
        norm_search = search_content.replace("\r\n", "\n")
        norm_replace = replace_content.replace("\r\n", "\n")

        start_idx, end_idx = self._resolve_classic_boundaries(
            start_line, end_line, symbol_boundaries
        )

        # Check occurrences
        target_slice = "\n".join(self.file_lines[start_idx:end_idx + 1])
        occurrences = target_slice.count(norm_search)

        if occurrences == 0:
            if did_you_mean:
                suggestion = self._find_closest_match(start_idx, end_idx, norm_search)
                if suggestion:
                    s_start, s_end, s_text, s_ratio = suggestion
                    if s_ratio >= 0.8:
                        start_idx = s_start - 1
                        end_idx = s_end - 1
                        target_slice = s_text
                        occurrences = 1
                        self.is_did_you_mean_applied = True
                        self.s_ratio = s_ratio
                        self.did_you_mean_start_line = s_start
                        self.did_you_mean_end_line = s_end
                    else:
                        self._handle_missing_match(start_idx, end_idx, norm_search, symbol_name)
                else:
                    self._handle_missing_match(start_idx, end_idx, norm_search, symbol_name)
            else:
                self._handle_missing_match(start_idx, end_idx, norm_search, symbol_name)

        if not allow_multiple and occurrences > 1:
            raise ValueError(
                f"Error: Search content occurs {occurrences} times within the specified scope (lines {start_idx + 1} to {end_idx + 1}). "
                "To replace all, set 'allow_multiple: true'."
            )

        # Line filter assertion
        if line_filter is not None and not self.is_did_you_mean_applied:
            self._assert_line_filter(line_filter, target_slice, norm_search, start_idx)

        # Apply replacement
        if self.is_did_you_mean_applied:
            alignment = fuzz.partial_ratio_alignment(target_slice, norm_search)
            if alignment:
                src_start = alignment.src_start
                src_end = alignment.src_end
                
                # Expand start if there's preceding punctuation/quotes matching start of search
                while src_start > 0 and target_slice[src_start - 1] in "'\"([{":
                    if target_slice[src_start - 1] == norm_search[0]:
                        src_start -= 1
                        break
                    src_start -= 1
                    
                # Expand end if there's succeeding punctuation/quotes matching end of search
                while src_end < len(target_slice) and target_slice[src_end] in "'\")}]":
                    if target_slice[src_end] == norm_search[-1]:
                        src_end += 1
                        break
                    src_end += 1

                patched_slice = (
                    target_slice[:src_start]
                    + norm_replace
                    + target_slice[src_end:]
                )
            else:
                patched_slice = norm_replace
        else:
            patched_slice = target_slice.replace(norm_search, norm_replace)

        before_part = "\n".join(self.file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
        after_part = "\n" + "\n".join(self.file_lines[end_idx + 1:]) if end_idx < len(self.file_lines) - 1 else ""
        patched_file = before_part + patched_slice + after_part

        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file, occurrences

    def _resolve_classic_boundaries(
        self,
        start_line: Optional[int],
        end_line: Optional[int],
        symbol_boundaries: Optional[tuple[Optional[int], Optional[int]]],
    ) -> tuple[int, int]:
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
        return start_idx, end_idx

    def _detect_mismatch_reason(self, text_a: str, text_b: str) -> list[str]:
        reasons = []
        norm_a = text_a.replace("\\n", "\n").replace("\\r\\n", "\n")
        norm_b = text_b.replace("\\n", "\n").replace("\\r\\n", "\n")
        if norm_a == norm_b:
            reasons.append("Mismatch due to raw escape characters (\\n) vs literal newlines")
        strip_a = "".join(text_a.split())
        strip_b = "".join(text_b.split())
        if strip_a == strip_b and norm_a != norm_b:
            reasons.append("Mismatch due to indentation or whitespace differences")
        return reasons

    def _handle_missing_match(
        self, start_idx: int, end_idx: int, norm_search: str, symbol_name: Optional[str]
    ) -> None:
        first_lines = "\n".join(norm_search.split("\n")[:3])
        err_msg = f"Error: Search content not found inside the specified scope (lines {start_idx + 1} to {end_idx + 1})!\nFirst 3 lines of search block:\n{first_lines}"
        if symbol_name:
            err_msg += f"\nAST Scope: Symbol '{symbol_name}' at lines {start_idx + 1}-{end_idx + 1}"
        
        # Smart closest match search within the scope
        suggestion = self._find_closest_match(start_idx, end_idx, norm_search)
        if suggestion:
            s_start, s_end, s_text, s_ratio = suggestion
            err_msg += f"\n\nDid you mean (lines {s_start} to {s_end}, similarity {round(s_ratio * 100)}%):\n{s_text}"
            reasons = self._detect_mismatch_reason(s_text, norm_search)
            for r in reasons:
                err_msg += f"\n*Note:* {r}."
        raise ValueError(err_msg)

    def _assert_line_filter(
        self, line_filter: Union[str, int], target_slice: str, norm_search: str, start_idx: int
    ) -> None:
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
                self._handle_mismatched_hunk(hunk, hunk_index, expected_pos, offset, expected_old_lines, file_lines)

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

    def _handle_mismatched_hunk(
        self, hunk: dict, hunk_index: int, expected_pos: int, offset: int, expected_old_lines: list[str], file_lines: list[str]
    ) -> None:
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
            err_msg += f"\n\nDid you mean (line {s_line}, similarity {round(s_ratio * 100)}%):\n{s_text}"
            reasons = self._detect_mismatch_reason(s_text, "\n".join(expected_old_lines))
            for r in reasons:
                err_msg += f"\n*Note:* {r}."
        raise ValueError(err_msg)

    def _find_closest_match(self, start_idx: int, end_idx: int, norm_search: str) -> Optional[tuple[int, int, str, float]]:
        search_lines = norm_search.split("\n")
        n = len(search_lines)
        best_ratio = 0.0
        best_slice = None
        best_range = None

        for window_size in (n, max(1, n - 1), n + 1):
            for i in range(start_idx, min(end_idx - window_size + 2, len(self.file_lines))):
                candidate_slice = "\n".join(self.file_lines[i : i + window_size])
                ratio = fuzz.ratio(candidate_slice, norm_search) / 100.0
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
            ratio = fuzz.ratio(candidate, search_str) / 100.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_line = i + 1

        if best_ratio < 0.5:
            for i in range(len(file_lines) - n + 1):
                candidate = "\n".join(file_lines[i : i + n])
                ratio = fuzz.ratio(candidate, search_str) / 100.0
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
                current_hunk = self._parse_hunk_header(match)
                hunks.append(current_hunk)
                continue

            if current_hunk is not None:
                current_hunk = self._parse_hunk_line(line, current_hunk)
        return hunks

    def _parse_hunk_header(self, match: re.Match) -> dict:
        old_start = int(match.group(1))
        old_lines = int(match.group(2)) if match.group(2) else 1
        new_start = int(match.group(3))
        new_lines = int(match.group(4)) if match.group(4) else 1

        return {
            'old_start': old_start,
            'old_lines': old_lines,
            'new_start': new_start,
            'new_lines': new_lines,
            'lines': []
        }

    def _parse_hunk_line(self, line: str, current_hunk: dict) -> Optional[dict]:
        if line.startswith('-'):
            current_hunk['lines'].append(('-', line[1:]))
        elif line.startswith('+'):
            current_hunk['lines'].append(('+', line[1:]))
        elif line.startswith(' '):
            current_hunk['lines'].append((' ', line[1:]))
        elif line == '':
            current_hunk['lines'].append((' ', ''))
        else:
            if line.startswith(('--- ', '+++ ', 'diff ')):
                return None
            else:
                current_hunk['lines'].append((' ', line))
        return current_hunk
