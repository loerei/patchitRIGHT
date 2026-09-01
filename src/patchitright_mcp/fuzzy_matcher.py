"""Fuzzy substring matching and alignment algorithms for patchitRIGHT."""

from __future__ import annotations

from rapidfuzz import fuzz


def expand_alignment_start(target_slice: str, src_start: int, match_char: str) -> int:
    """Expand fuzzy alignment start boundary to matching delimiters."""
    if match_char and match_char in "'\"([{" and 0 < src_start <= len(target_slice) and target_slice[src_start - 1] == match_char:
        return src_start - 1
    return src_start


def expand_alignment_end(target_slice: str, src_end: int, match_char: str) -> int:
    """Expand fuzzy alignment end boundary to matching delimiters."""
    if match_char and match_char in "'\")}]" and 0 <= src_end < len(target_slice) and target_slice[src_end] == match_char:
        return src_end + 1
    return src_end


def detect_mismatch_reason(text_a: str, text_b: str) -> list[str]:
    """Detect heuristic explanations for string mismatches (escapes, whitespace)."""
    reasons = []
    norm_a = text_a.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    norm_b = text_b.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    if text_a != text_b and norm_a == norm_b:
        reasons.append("Mismatch due to raw escape characters (\\n) vs literal newlines")
    strip_a = "".join(text_a.split())
    strip_b = "".join(text_b.split())
    if strip_a == strip_b and norm_a != norm_b:
        reasons.append("Mismatch due to indentation or whitespace differences")
    return reasons


def find_closest_match(
    file_lines: list[str],
    start_idx: int,
    end_idx: int,
    norm_search: str,
) -> tuple[int, int, str, float] | None:
    """Find closest matching slice within line boundaries using coarse/fine strided scan."""
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

        file_stripped = [line.strip() for line in file_lines]
        min_window = max(1, n - 1)
        max_range = min(end_idx - min_window + 2, len(file_lines) - min_window + 1)

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
        for i in sorted(fine_indices):
            score = eval_sample_score(i)
            fine_scores.append((score, i))

        fine_scores.sort(key=lambda x: (-x[0], x[1]))

        # 3. Evaluate full fuzz.ratio on top candidate windows
        for sample_score, i in fine_scores:
            if best_ratio >= 0.5 and (sample_score / 100.0) < (best_ratio - 0.08):
                break

            for window_size in (n, max(1, n - 1), n + 1):
                if (i + window_size - 1) >= len(file_lines):
                    continue
                candidate_slice = "\n".join(file_lines[i : i + window_size])
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
            for i in range(start_idx, min(end_idx - window_size + 2, len(file_lines))):
                if (i + window_size - 1) >= len(file_lines):
                    continue
                candidate_slice = "\n".join(file_lines[i : i + window_size])
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


def find_closest_hunk_match(
    hunk_old_lines: list[str],
    start_search: int,
    file_lines: list[str],
) -> tuple[int, float] | None:
    """Find closest line index matching unified diff hunk old lines."""
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
