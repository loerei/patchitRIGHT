"""Line matching, AST symbol resolution, and line offset calculations."""
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .symbol_resolver import resolve_symbol

if TYPE_CHECKING:
    from .body_parser import BodyRange


def _resolve_ast_boundaries(
    cwd: Path,
    target_path: Path,
    symbol_name: Optional[str],
    storage_path: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    symbol_scope: str = "boundary",
    file_content: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], Optional[dict], Optional["BodyRange"]]:
    """Resolve start and end lines for an AST symbol directly from the target file."""
    del cwd, storage_path  # Kept for signature compatibility; resolution is local and index-free.
    if not symbol_name:
        return start_line, end_line, None, None

    try:
        text = file_content
        if text is None:
            if not target_path.exists():
                return None, None, {"error": f"Error: Target file '{target_path}' does not exist for symbol resolution."}, None
            text = target_path.read_text(encoding="utf-8", errors="replace")

        sym = resolve_symbol(target_path, text, symbol_name, hint_start=start_line, hint_end=end_line)
        sym_start, sym_end = sym.start_line, sym.end_line

        if symbol_scope == "body":
            from .body_parser import get_body_range

            body_range = get_body_range(text, str(target_path), sym_start, sym_end)
            if body_range is None:
                return None, None, {"error": f"Error: Could not resolve body boundaries for symbol '{symbol_name}'."}, None
            return body_range.start_line, body_range.end_line, None, body_range

        return sym_start, sym_end, None, None
    except ValueError as e:
        return None, None, {"error": str(e)}, None
    except Exception as e:
        return None, None, {"error": f"Error resolving symbolName '{symbol_name}': {e}"}, None


def check_replacement_collisions(resolved_items: list[dict]) -> Optional[dict]:
    """Check for overlapping replacement ranges or insertions inside active replacement bodies."""
    repl_items = [x for x in resolved_items if not x.get("is_insertion") and not x.get("unbounded")]
    sorted_repl = sorted(repl_items, key=lambda x: x["start_line"])
    for i in range(len(sorted_repl) - 1):
        curr = sorted_repl[i]
        nxt = sorted_repl[i + 1]
        if curr["end_line"] >= nxt["start_line"]:
            return {"error": f"Error: Overlapping replacements detected between lines {curr['start_line']}-{curr['end_line']} and {nxt['start_line']}-{nxt['end_line']}."}

    ins_items = [x for x in resolved_items if x.get("is_insertion")]
    for ins in ins_items:
        ins_l = ins["insert_line"]
        for repl in repl_items:
            if repl["start_line"] < ins_l <= repl["end_line"]:
                return {"error": f"Error: Cannot insert code inside an active replacement range (lines {repl['start_line']}-{repl['end_line']})."}
    return None


def sort_resolved_items_descending(resolved_items: list[dict]) -> list[dict]:
    """Sort resolved items descending by target line; replacements before insertions.
    Co-located insertions targeting the same line execute in reverse declaration order
    so that subsequent insertions push earlier ones down, preserving declared order.
    EOF insertions (insert_line == -1) execute in ascending declaration order.
    """
    def _ins_key(x: dict) -> int:
        if not x.get("is_insertion"):
            return -x.get("orig_idx", 0)
        is_eof = (x.get("insert_line") == -1 or x.get("start_line") == -1)
        return -x.get("orig_idx", 0) if is_eof else x.get("orig_idx", 0)

    return sorted(
        resolved_items,
        key=lambda x: (
            x["start_line"],
            1 if not x.get("is_insertion") else 0,
            _ins_key(x),
        ),
        reverse=True,
    )
