"""Standardized error responses, engine flag decoration, and exception formatting."""
from pathlib import Path
from typing import Optional, Union


def _create_error_response(
    error_msg: Union[str, dict],
    target_file: Optional[str] = None,
    detail: Optional[str] = None,
    **extra_fields,
) -> dict:
    """Format standardized error response dictionary with consistent schema fallbacks."""
    if isinstance(error_msg, dict):
        base_err = dict(error_msg)
        tf = base_err.get("target_file", target_file)
    else:
        base_err = {"error": str(error_msg)}
        tf = target_file
        if detail:
            base_err["detail"] = detail

    base_err.update(extra_fields)
    base_err.setdefault("target_file", tf)
    base_err.setdefault("diff_content", None)
    base_err.setdefault("relocated_range", None)
    base_err.setdefault("did_you_mean_info", None)
    base_err.setdefault("modified_files", None)
    return base_err


def _attach_engine_flags(res: dict, pf: dict) -> dict:
    """Attach linter warnings, suggestions, and engine execution flags to response dictionary."""
    if pf.get("warnings"):
        res["warnings"] = pf["warnings"]
        res["suggestion"] = pf["suggestion"]
    eng = pf.get("engine")
    if eng:
        if getattr(eng, "indentation_adjusted", False):
            res["indentation_adjusted"] = True
            res["indent_delta"] = getattr(eng, "indent_delta", "")
        if getattr(eng, "newline_padded", False):
            res["newline_padded"] = True
        if getattr(eng, "large_file_fallback", False):
            res["large_file_fallback"] = True
        if getattr(eng, "is_relocated", False):
            res["is_relocated"] = True
            res["relocated_range"] = {
                "start_line": getattr(eng, "relocated_start_line", 1),
                "end_line": getattr(eng, "relocated_end_line", 1),
            }
        else:
            res.setdefault("relocated_range", None)

        if getattr(eng, "is_did_you_mean_applied", False):
            res["is_did_you_mean_applied"] = True
            res["did_you_mean_info"] = {
                "applied": True,
                "similarity": getattr(eng, "s_ratio", 0.0),
                "start_line": getattr(eng, "did_you_mean_start_line", None),
                "end_line": getattr(eng, "did_you_mean_end_line", None),
            }
        else:
            res.setdefault("did_you_mean_info", None)
    else:
        res.setdefault("relocated_range", None)
        res.setdefault("did_you_mean_info", None)
    return res


def _handle_patch_file_value_error(e: ValueError, target_file: str) -> dict:
    """Translate ValueError into structured diagnostic response payload."""
    if str(e) == "fatal_context_mismatch":
        cwd = Path.cwd().resolve()
        return _create_error_response(
            "fatal_context_mismatch",
            target_file=target_file,
            detail=(
                f"[FATAL CONTEXT MISMATCH]\n"
                f"Relative path '{target_file}' resolves outside the active MCP workspace '{cwd}'.\n\n"
                "Relative paths are restricted to the active workspace to prevent cross-repo drift.\n"
                "To fix:\n"
                "1. Use an absolute path to target a file outside the current workspace.\n"
                "2. Or ensure the terminal shell is CD'ed to the correct repository.\n"
            ),
        )
    res = _create_error_response(str(e), target_file=target_file)
    if getattr(e, "run_id", None):
        res["run_id"] = getattr(e, "run_id")
        from .run_cache import get_cache
        res["expires_in"] = get_cache().get_ttl()
        res["message"] = str(e)
    return res


def _handle_batch_value_error(e: ValueError) -> dict:
    """Translate ValueError in batch processing into error dictionary."""
    return _create_error_response(str(e))
