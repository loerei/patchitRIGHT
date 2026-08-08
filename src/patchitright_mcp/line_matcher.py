"""Line matching, AST symbol resolution, and line offset calculations."""
import re
from pathlib import Path
from typing import Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .body_parser import BodyRange


def _resolve_ast_boundaries(
    cwd: Path,
    target_path: Path,
    symbol_name: Optional[str],
    storage_path: Optional[str],
    start_line: Optional[int],
    end_line: Optional[int],
    symbol_scope: str = "boundary",
    file_content: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], Optional[dict], Optional["BodyRange"]]:
    """Resolve start and end lines for AST symbolName boundary."""
    if not symbol_name:
        return start_line, end_line, None, None

    try:
        from jcodemunch_mcp.tools.resolve_repo import resolve_repo as resolve_repo_fn
        from jcodemunch_mcp.storage import IndexStore
        repo_res = resolve_repo_fn(str(target_path), storage_path)
        if not repo_res.get("found"):
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed. Call index_folder first to resolve symbols."}, None
        
        repo_id = repo_res.get("repo")
        store = IndexStore(base_path=storage_path)
        index = store.load_index(*repo_id.split("/", 1)) if repo_id and "/" in repo_id else None
        if not repo_res.get("found") or not index:
            if file_content is None and target_path.exists():
                with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                    file_content = f.read()
            if file_content:
                lines = file_content.split("\n")
                pattern = re.compile(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(symbol_name)}\b")
                found_line = None
                for idx, line in enumerate(lines, 1):
                    if pattern.search(line):
                        found_line = idx
                        break
                if found_line:
                    indent = len(lines[found_line - 1]) - len(lines[found_line - 1].lstrip())
                    end_l = len(lines)
                    for idx in range(found_line, len(lines)):
                        l = lines[idx]
                        if l.strip() and not l.strip().startswith("#"):
                            curr_indent = len(l) - len(l.lstrip())
                            if curr_indent <= indent and idx + 1 > found_line:
                                end_l = idx
                                break
                    body_range = None
                    try:
                        from .body_parser import get_body_range
                        body_range = get_body_range(file_content, str(target_path), found_line, end_l)
                    except Exception:
                        pass
                    return found_line, end_l, None, body_range
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed and symbol '{symbol_name}' could not be matched."}, None
            
        source_root = Path(repo_res.get("source_root") or index.source_root or cwd).resolve()
        try:
            rel_file_path = str(target_path.relative_to(source_root)).replace("\\", "/")
        except ValueError:
            rel_file_path = str(target_path.relative_to(cwd)).replace("\\", "/")
            
        matched_symbols = []
        for sym in index.symbols:
            if sym.get("name") == symbol_name and sym.get("file") == rel_file_path:
                matched_symbols.append(sym)
                
        if not matched_symbols:
            return None, None, {"error": f"Symbol '{symbol_name}' not found in file '{rel_file_path}'."}, None
            
        symbol = matched_symbols[0]
        sym_start, sym_end = symbol["line"], symbol["end_line"]

        if symbol_scope == "body":
            if file_content is None:
                with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                    file_content = f.read()
            from .body_parser import get_body_range
            body_range = get_body_range(file_content, str(target_path), sym_start, sym_end)
            return body_range.start_line, body_range.end_line, None, body_range

        return sym_start, sym_end, None, None
    except Exception as e:
        return None, None, {"error": f"Error resolving symbolName '{symbol_name}': {e}"}, None


def check_replacement_collisions(resolved_items: list[dict]) -> Optional[dict]:
    """Check for overlapping replacement ranges or insertions inside active replacement bodies."""
    repl_items = [x for x in resolved_items if not x.get("is_insertion")]
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
            if repl["start_line"] < ins_l < repl["end_line"]:
                return {"error": f"Error: Cannot insert code inside an active replacement range (lines {repl['start_line']}-{repl['end_line']})."}
    return None


def sort_resolved_items_descending(resolved_items: list[dict]) -> list[dict]:
    """Sort resolved items descending by target line; replacements before insertions."""
    return sorted(
        resolved_items,
        key=lambda x: (x["start_line"], 1 if not x.get("is_insertion") else 0),
        reverse=True,
    )
