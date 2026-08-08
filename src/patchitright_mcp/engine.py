import re
from typing import Optional, Union
from rapidfuzz import fuzz
from .validators import ValidationService
from .symbol_checker import detect_omitted_symbols, extract_net_diff_declarations

class PatchEngine:
    """Core in-memory engine to apply search-and-replace and unified diff patches."""

    def __init__(self, file_content: str, filename: str, bypass_validation: bool = False):
        self.file_content = file_content
        self.filename = filename
        self.is_crlf = "\r\n" in file_content
        self.norm_content = file_content.replace("\r\n", "\n").replace("\r", "")
        self.file_lines = self.norm_content.split("\n") if self.norm_content else []
        self.is_did_you_mean_applied = False
        self.s_ratio = 0.0
        self.did_you_mean_start_line = None
        self.did_you_mean_end_line = None
        self.is_relocated = False
        self.relocated_start_line = None
        self.relocated_end_line = None
        self.linter_warnings = []
        self.symbol_warnings = []
        self.validator = ValidationService()
        self.bypass_validation = bypass_validation
        self.indentation_adjusted = False
        self.indent_delta = ""
        self.newline_padded = False

    def _find_occurrence_line_ranges(
        self, content: str, search: str, base_line_offset: int = 0
    ) -> list[tuple[int, int]]:
        """Finds the 1-based start and end line numbers of all occurrences of search in content."""
        ranges = []
        start_pos = 0
        while True:
            idx = content.find(search, start_pos)
            if idx == -1:
                break
            start_line = base_line_offset + content[:idx].count("\n") + 1
            end_line = start_line + search.count("\n")
            ranges.append((start_line, end_line))
            start_pos = idx + max(1, len(search))
        return ranges

    def _handle_zero_occurrences(
        self,
        norm_search: str,
        norm_replace: str,
        start_idx: int,
        end_idx: int,
        symbol_name: Optional[str],
        allow_multiple: bool,
        did_you_mean: bool,
    ) -> tuple[int, int, str, int]:
        total_occurrences = self.norm_content.count(norm_search)
        if total_occurrences == 1:
            char_idx = self.norm_content.find(norm_search)
            new_start = self.norm_content[:char_idx].count("\n")
            new_end = new_start + norm_search.count("\n")
            new_slice = "\n".join(self.file_lines[new_start:new_end + 1])
            self.is_relocated = True
            self.relocated_start_line = new_start + 1
            self.relocated_end_line = new_end + 1
            return new_start, new_end, new_slice, 1

        if total_occurrences > 1 and not allow_multiple:
            ranges = self._find_occurrence_line_ranges(self.norm_content, norm_search)
            ranges_str = ", ".join(f"lines {s}-{e}" for s, e in ranges)
            raise ValueError(
                f"Error: Search content not found inside the specified scope (lines {start_idx + 1} to {end_idx + 1}), "
                f"but it occurs {total_occurrences} times in the entire file (at {ranges_str}). "
                "Cannot relocate safely."
            )
        if did_you_mean:
            suggestion = self._find_closest_match(start_idx, end_idx, norm_search)
            if suggestion:
                s_start, s_end, s_text, s_ratio = suggestion
                if s_ratio >= 0.8:
                    self.is_did_you_mean_applied = True
                    self.s_ratio = s_ratio
                    self.did_you_mean_start_line = s_start
                    self.did_you_mean_end_line = s_end
                    return s_start - 1, s_end - 1, s_text, 1

        target_slice = "\n".join(self.file_lines[start_idx:end_idx + 1])
        self._handle_missing_match(start_idx, end_idx, norm_search, norm_replace, target_slice, symbol_name)

    def _expand_alignment_start(self, target_slice: str, src_start: int, match_char: str) -> int:
        while src_start > 0 and target_slice[src_start - 1] in "'\"([{":
            if target_slice[src_start - 1] == match_char:
                src_start -= 1
                break
            src_start -= 1
        return src_start

    def _expand_alignment_end(self, target_slice: str, src_end: int, match_char: str) -> int:
        while src_end < len(target_slice) and target_slice[src_end] in "'\")}]":
            if target_slice[src_end] == match_char:
                src_end += 1
                break
            src_end += 1
        return src_end

    def _apply_replacement_logic(self, target_slice: str, norm_search: str, norm_replace: str) -> str:
        if self.is_did_you_mean_applied:
            alignment = fuzz.partial_ratio_alignment(target_slice, norm_search)
            if alignment:
                src_start = self._expand_alignment_start(target_slice, alignment.src_start, norm_search[0])
                src_end = self._expand_alignment_end(target_slice, alignment.src_end, norm_search[-1])
                return (
                    target_slice[:src_start]
                    + norm_replace
                    + target_slice[src_end:]
                )
            return norm_replace
        return target_slice.replace(norm_search, norm_replace)

    def _check_symbol_omissions(
        self, start_idx: int, end_idx: int, target_slice: str, norm_search: str, replace_content: str
    ):
        """Checks for omitted declared symbols that are referenced in outer scope."""
        base_offset = sum(len(line) + 1 for line in self.file_lines[:start_idx])
        rel_offset = target_slice.find(norm_search)
        if rel_offset == -1:
            rel_offset = 0
        match_start = base_offset + rel_offset
        match_end = match_start + len(norm_search)
        warnings = detect_omitted_symbols(
            file_content=self.norm_content,
            match_start=match_start,
            match_end=match_end,
            original_slice=norm_search,
            replace_content=replace_content,
            filename=self.filename,
        )
        self.symbol_warnings.extend(warnings)

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
        validate: bool = True,
        check_symbols: bool = True,
    ) -> tuple[str, int]:
        """Applies a classic search-and-replace patch inside line/symbol scope."""
        norm_search = search_content.replace("\r\n", "\n").replace("\r", "")
        norm_replace = replace_content.replace("\r\n", "\n").replace("\r", "")

        start_idx, end_idx = self._resolve_classic_boundaries(
            start_line, end_line, symbol_boundaries
        )

        # Check occurrences
        target_slice = "\n".join(self.file_lines[start_idx:end_idx + 1])
        occurrences = target_slice.count(norm_search)

        if occurrences == 0:
            start_idx, end_idx, target_slice, occurrences = self._handle_zero_occurrences(
                norm_search, norm_replace, start_idx, end_idx, symbol_name, allow_multiple, did_you_mean
            )

        if not allow_multiple and occurrences > 1:
            ranges = self._find_occurrence_line_ranges(target_slice, norm_search, base_line_offset=start_idx)
            ranges_str = ", ".join(f"lines {s}-{e}" for s, e in ranges)
            raise ValueError(
                f"Error: Search content occurs {occurrences} times within the specified scope (lines {start_idx + 1} to {end_idx + 1}): {ranges_str}. "
                "To replace all, set 'allow_multiple: true'."
            )

        # Line filter assertion
        if line_filter is not None and not self.is_did_you_mean_applied:
            self._assert_line_filter(line_filter, target_slice, norm_search, start_idx)

        if check_symbols:
            self._check_symbol_omissions(start_idx, end_idx, target_slice, norm_search, norm_replace)

        # Apply replacement
        patched_slice = self._apply_replacement_logic(target_slice, norm_search, norm_replace)

        before_part = "\n".join(self.file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
        after_part = "\n" + "\n".join(self.file_lines[end_idx + 1:]) if end_idx < len(self.file_lines) - 1 else ""
        patched_file = before_part + patched_slice + after_part

        if validate and not self.bypass_validation:
            self.validator.validate_file(self.filename, patched_file, self.file_content)
            self.linter_warnings = self.validator.lint_file(self.filename, patched_file)

        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file, occurrences

    def apply_line_insertion(
        self,
        insert_line: Optional[int],
        insert_content: str,
        auto_indent: bool = True,
        validate: bool = True,
    ) -> tuple[str, int]:
        """Applies a line-based insertion to file_lines.

        insert_line: 1-indexed line number (1 for top of file, -1 for EOF).
        insert_content: string content to insert.
        auto_indent: if True, matches target reference line leading whitespace.

        Returns: (patched_file, 1)
        """
        if not insert_content:
            raise ValueError("Error: 'insert_content' cannot be empty.")

        if insert_line is None:
            raise ValueError("Error: 'insert_line' parameter is required.")

        if insert_line == 0 or insert_line < -1:
            raise ValueError("Error: Line index must be >= 1 or -1 for end-of-file.")

        # Empty file handling
        if not self.file_lines:
            content = insert_content[:-1] if insert_content.endswith("\n") else insert_content
            if content.endswith("\r"):
                content = content[:-1]
            patched_file = content
            if validate and not self.bypass_validation:
                self.validator.validate_file(self.filename, patched_file, self.file_content)
                self.linter_warnings = self.validator.lint_file(self.filename, patched_file)
            if self.is_crlf:
                patched_file = patched_file.replace("\n", "\r\n")
            return patched_file, 1

        total_lines = len(self.file_lines)

        self.insertion_warnings = []
        if insert_line > total_lines:
            self.insertion_warnings.append(f"Warning: Specified insert_line ({insert_line}) exceeds total file lines ({total_lines}). Clamped insertion to end-of-file.")

        is_eof = (insert_line == -1 or insert_line > total_lines)
        if is_eof:
            target_idx = total_lines
            ref_idx = total_lines - 1
        else:
            line_idx = insert_line - 1
            target_idx = line_idx
            ref_idx = line_idx

        indent = ""
        if auto_indent and not is_eof:
            ref_line_idx = max(0, min(ref_idx, total_lines - 1))
            ref_line = self.file_lines[ref_line_idx]
            if not ref_line.strip():
                found = False
                # 1. Scan succeeding non-blank lines first (captures block body indent)
                for s_idx in range(ref_line_idx + 1, total_lines):
                    if self.file_lines[s_idx].strip():
                        ref_line = self.file_lines[s_idx]
                        found = True
                        break
                # 2. If no succeeding non-blank line, scan preceding non-blank lines
                if not found:
                    for p_idx in range(ref_line_idx - 1, -1, -1):
                        p_line = self.file_lines[p_idx]
                        if p_line.strip():
                            ref_line = p_line
                            found = True
                            # If preceding header ends with ':', append 4 spaces / 1 tab
                            if p_line.rstrip().endswith(":"):
                                p_indent = p_line[:len(p_line) - len(p_line.lstrip())]
                                unit = "\t" if "\t" in p_line else "    "
                                indent = p_indent + unit
                            break
            if not indent and ref_line.strip():
                indent = ref_line[:len(ref_line) - len(ref_line.lstrip())]

            if not indent and any(line_str.strip() for line_str in self.file_lines):
                self.insertion_warnings.append("Warning: Could not infer reference indentation for auto_indent. Defaulted to top-level (0 spaces).")

        if not auto_indent and insert_content:
            file_has_spaces = any(line_str.startswith(" ") for line_str in self.file_lines if line_str.strip())
            file_has_tabs = any(line_str.startswith("\t") for line_str in self.file_lines if line_str.strip())
            content_has_tabs = "\t" in insert_content
            content_has_spaces = any(line_str.startswith(" ") for line_str in insert_content.splitlines() if line_str.strip())

            if file_has_spaces and content_has_tabs:
                self.insertion_warnings.append("Warning: File uses spaces for indentation, but inserted content contains tabs while auto_indent=False.")
            elif file_has_tabs and content_has_spaces:
                self.insertion_warnings.append("Warning: File uses tabs for indentation, but inserted content contains spaces while auto_indent=False.")

        norm_insert = insert_content[:-1] if insert_content.endswith("\n") else insert_content
        if norm_insert.endswith("\r"):
            norm_insert = norm_insert[:-1]
        norm_insert = norm_insert.replace("\r\n", "\n").replace("\r", "")

        if auto_indent and norm_insert.strip():
            import textwrap
            norm_insert = textwrap.dedent(norm_insert)

        insert_lines = norm_insert.split("\n")
        if indent:
            indented_lines = [
                (indent + line) if line.strip() else line
                for line in insert_lines
            ]
        else:
            indented_lines = insert_lines

        new_file_lines = list(self.file_lines)
        new_file_lines[target_idx:target_idx] = indented_lines
        patched_file = "\n".join(new_file_lines)

        if validate and not self.bypass_validation:
            self.validator.validate_file(self.filename, patched_file, self.file_content)
            self.linter_warnings = self.validator.lint_file(self.filename, patched_file)

        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file, 1


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
        self, start_idx: int, end_idx: int, norm_search: str, norm_replace: str, target_slice: str, symbol_name: Optional[str]
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

            try:
                s_indent = s_text[:len(s_text) - len(s_text.lstrip())]
                s_replace = s_indent + norm_replace if s_indent and not norm_replace.startswith(s_indent) else norm_replace
                suggested_patched_slice = self._apply_replacement_logic(target_slice, s_text, s_replace)
                before_part = "\n".join(self.file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
                after_part = "\n" + "\n".join(self.file_lines[end_idx + 1:]) if end_idx < len(self.file_lines) - 1 else ""
                suggested_patched = before_part + suggested_patched_slice + after_part
                if self.is_crlf:
                    suggested_patched = suggested_patched.replace("\n", "\r\n")

                from .run_cache import get_cache
                from pathlib import Path
                cache = get_cache()
                run_id = cache.store(
                    entries=[{"target_path": Path(self.filename), "patched_content": suggested_patched}],
                    original_contents={self.filename: self.file_content}
                )
                err_msg += f"\n\nTo apply this suggestion, run apply_last_dry_run with run_id '{run_id}'."
                ve = ValueError(err_msg)
                setattr(ve, "run_id", run_id)
                raise ve
            except ValueError:
                raise
            except Exception:
                pass
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
            if hunk['old_start'] == 0:
                expected_pos = 0
            else:
                expected_pos = hunk['old_start'] - 1 + offset

            if not self._verify_hunk_match(expected_pos, expected_old_lines, file_lines):
                self._handle_mismatched_hunk(hunk, hunk_index, expected_pos, offset, expected_old_lines, file_lines)

            new_hunk_lines = [l_content for l_type, l_content in hunk['lines'] if l_type in (' ', '+')]

            file_lines[expected_pos : expected_pos + len(expected_old_lines)] = new_hunk_lines
            hunk_offset = len(new_hunk_lines) - len(expected_old_lines)
            offset += hunk_offset

        patched_file = "\n".join(file_lines)

        if not self.bypass_validation:
            self.validator.validate_file(self.filename, patched_file, self.file_content)
            self.linter_warnings = self.validator.lint_file(self.filename, patched_file)

        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file

    def _verify_hunk_match(self, expected_pos: int, expected_old_lines: list[str], file_lines: list[str]) -> bool:
        if expected_pos == 0 and not expected_old_lines:
            return True
        if expected_pos < 0 or expected_pos + len(expected_old_lines) > len(file_lines):
            return False
        for idx, expected_line in enumerate(expected_old_lines):
            if file_lines[expected_pos + idx] != expected_line:
                return False
        return True

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

        if n >= 6:
            sample_offsets = [0, n // 2, n - 1]
            samples = []
            for off in sample_offsets:
                if off < n:
                    line = search_lines[off].strip()
                    if line:
                        samples.append((off, line))

            file_stripped = [line.strip() for line in self.file_lines]
            max_range = min(end_idx - n + 2, len(self.file_lines) - n + 1)

            def eval_sample_score(i: int) -> float:
                score_sum = 0.0
                cnt = 0
                for off, s_line in samples:
                    c_idx = i + off
                    if c_idx < len(file_stripped) and file_stripped[c_idx]:
                        score_sum += fuzz.ratio(file_stripped[c_idx], s_line, score_cutoff=30.0)
                        cnt += 1
                return (score_sum / cnt) if cnt > 0 else 0.0

            # 1. Coarse strided scan across the file
            stride = max(1, n // 10)
            coarse_scores = []
            for i in range(start_idx, max_range, stride):
                score = eval_sample_score(i)
                coarse_scores.append((score, i))

            coarse_scores.sort(key=lambda x: x[0], reverse=True)

            # 2. Fine local scan around top coarse candidate regions
            top_coarse = coarse_scores[:5]
            fine_indices = set()
            for _, region_i in top_coarse:
                local_start = max(start_idx, region_i - stride)
                local_end = min(max_range, region_i + stride + 1)
                fine_indices.update(range(local_start, local_end))

            fine_scores = []
            for i in fine_indices:
                score = eval_sample_score(i)
                fine_scores.append((score, i))

            fine_scores.sort(key=lambda x: x[0], reverse=True)

            # 3. Evaluate full fuzz.ratio on top candidate windows
            for sample_score, i in fine_scores:
                if best_ratio >= 0.5 and (sample_score / 100.0) < (best_ratio - 0.08):
                    break

                for window_size in (n, max(1, n - 1), n + 1):
                    if (i + window_size - 1) >= len(self.file_lines):
                        continue
                    candidate_slice = "\n".join(self.file_lines[i : i + window_size])
                    score_cutoff = max(50.0, best_ratio * 100.0)
                    ratio = fuzz.ratio(candidate_slice, norm_search, score_cutoff=score_cutoff) / 100.0
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_slice = candidate_slice
                        best_range = (i + 1, i + window_size)
                        if best_ratio >= 0.99:
                            return best_range[0], best_range[1], best_slice, best_ratio
        else:
            for window_size in (n, max(1, n - 1), n + 1):
                for i in range(start_idx, min(end_idx - window_size + 2, len(self.file_lines))):
                    candidate_slice = "\n".join(self.file_lines[i : i + window_size])
                    score_cutoff = max(50.0, best_ratio * 100.0)
                    ratio = fuzz.ratio(candidate_slice, norm_search, score_cutoff=score_cutoff) / 100.0
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_slice = candidate_slice
                        best_range = (i + 1, i + window_size)
                        if best_ratio >= 0.99:
                            return best_range[0], best_range[1], best_slice, best_ratio

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
            score_cutoff = max(30.0, best_ratio * 100.0)
            ratio = fuzz.ratio(candidate, search_str, score_cutoff=score_cutoff) / 100.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_line = i + 1
                if best_ratio >= 0.99:
                    return best_line, best_ratio

        if best_ratio < 0.5:
            for i in range(len(file_lines) - n + 1):
                candidate = "\n".join(file_lines[i : i + n])
                score_cutoff = max(30.0, best_ratio * 100.0)
                ratio = fuzz.ratio(candidate, search_str, score_cutoff=score_cutoff) / 100.0
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_line = i + 1
                    if best_ratio >= 0.99:
                        return best_line, best_ratio

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

    def apply_symbol_replacement(
        self,
        replace_content: str,
        start_line: int,       # 1-indexed
        start_col: int,        # 0-indexed char offset
        end_line: int,         # 1-indexed
        end_col: int,          # 0-indexed char offset
        symbol_scope: str,     # "full" or "body"
        is_expression: bool = False,
        symbol_start_line: Optional[int] = None,
    ) -> tuple[str, int]:
        
        """Replace the resolved symbol or body in-place."""
        replace_content = replace_content.replace("\r\n", "\n").replace("\r", "")
        start_line_idx = start_line - 1
        end_line_idx = end_line - 1

        # Check line limits (avoid out of range)
        start_line_idx = max(0, min(start_line_idx, len(self.file_lines) - 1))
        end_line_idx = max(start_line_idx, min(end_line_idx, len(self.file_lines) - 1))

        from .body_parser import detect_indent, normalize_indent, pad_block_newlines, _infer_indent_unit

        if symbol_scope == "body":
            # Extract current body content for indent detection
            if start_line_idx == end_line_idx:
                body_text = self.file_lines[start_line_idx][start_col:end_col]
            else:
                first = self.file_lines[start_line_idx][start_col:]
                last = self.file_lines[end_line_idx][:end_col]
                middle = self.file_lines[start_line_idx + 1:end_line_idx]
                body_text = "\n".join([first] + middle + [last])
            
            body_lines = body_text.split("\n")
            sig_line = self.file_lines[start_line_idx]
            
            # 1. Detect base indent
            if self.filename.endswith(".py"):
                if symbol_start_line and start_line_idx == symbol_start_line - 1:
                    sig_indent = sig_line[:len(sig_line) - len(sig_line.lstrip())]
                    indent_unit = _infer_indent_unit(self.file_lines)
                    target_indent = sig_indent + indent_unit
                else:
                    first_body_line = self.file_lines[start_line_idx]
                    target_indent = first_body_line[:len(first_body_line) - len(first_body_line.lstrip())]
            else:
                target_indent = detect_indent(body_lines, sig_line, self.file_lines)
            
            # 2. Re-indent replace_content in isolation
            replace_content, adjusted, delta = normalize_indent(replace_content, target_indent)
            self.indentation_adjusted = adjusted
            self.indent_delta = delta

            # 3. Smart newline padding (skip for expression bodies and
            #    single-line bodies where the replacement is also single-line)
            self.newline_padded = False
            is_single_line_body = start_line_idx == end_line_idx
            replacement_is_multiline = "\n" in replace_content
            if not is_expression and (not is_single_line_body or replacement_is_multiline):
                sig_indent = sig_line[:len(sig_line) - len(sig_line.lstrip())]
                replace_content, padded = pad_block_newlines(replace_content, sig_indent, False)
                self.newline_padded = padded

            # 4. Line-level column splicing
            start_line_text = self.file_lines[start_line_idx]
            end_line_text = self.file_lines[end_line_idx]

            prefix = start_line_text[:start_col]
            suffix = end_line_text[end_col:]

            # Prevent double indentation if replacement starts with target_indent
            # and prefix is exactly target_indent.
            if replace_content.startswith(target_indent) and prefix == target_indent:
                replace_content = replace_content[len(target_indent):]

            spliced_str = prefix + replace_content + suffix
            spliced_lines = spliced_str.split("\n")

            patched_lines = list(self.file_lines)
            patched_lines[start_line_idx:end_line_idx + 1] = spliced_lines
            patched_file = "\n".join(patched_lines)

        else: # "full"
            # Resolve signature indent
            sig_line = self.file_lines[start_line_idx]
            sig_indent = sig_line[:len(sig_line) - len(sig_line.lstrip())]
            
            # 1. Re-indent full replacement to match signature indent
            replace_content, adjusted, delta = normalize_indent(replace_content, sig_indent)
            self.indentation_adjusted = adjusted
            self.indent_delta = delta
            self.newline_padded = False

            # 2. Line replacement
            patched_lines = list(self.file_lines)
            patched_lines[start_line_idx:end_line_idx + 1] = replace_content.split("\n")
            patched_file = "\n".join(patched_lines)

        # Validate
        if not self.bypass_validation:
            self.validator.validate_file(self.filename, patched_file, self.file_content)
            self.linter_warnings = self.validator.lint_file(self.filename, patched_file)

        if self.is_crlf:
            patched_file = patched_file.replace("\n", "\r\n")

        return patched_file, 1
        
