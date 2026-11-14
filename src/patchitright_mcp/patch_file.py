"""AST-bounded file editing interface and execution controller facade for patchitRIGHT."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Optional, Union

from .diagnostics import (
    _create_error_response,
    _handle_patch_file_value_error,
)
from .dry_run import (
    apply_last_dry_run,
    batch_patch_files,
)
from .patch_applier import _process_single_file_in_memory
from .self_mod_safety import (
    _get_linter_suggestion,
    run_startup_recovery,
    trigger_jcodemunch_sync,
)
from .source_resolver import ResolvedSource, resolve_source_slice
from .transaction import FileTransaction
from .tx_executor import (
    execute_file_plan,
    generate_diff,
    resolve_common_workspace_root,
)
from .validators import SyntaxValidationError, ValidationService
from .workspace import Workspace

_DEFAULT_GLOB_EXCLUDES = {".git", ".patchitRIGHT", "node_modules", ".venv", "__pycache__"}
_SOURCE_SLICE_KEYS = (
    "source_file",
    "source_start_line",
    "source_end_line",
    "source_symbol",
    "source_start_anchor",
    "source_end_anchor",
    "delete_source",
    "cut_source",
)
_TRANSFORM_KEYS = (
    "search_content",
    "replace_content",
    "start_line",
    "end_line",
    "patch_content",
    "replacements",
    "insert_line",
    "insert_content",
    "symbol_name",
)
_INHERITABLE_PATCH_KEYS = (
    "search_content",
    "replace_content",
    "start_line",
    "end_line",
    "patch_content",
    "replacements",
    "insert_line",
    "insert_content",
    "auto_indent",
    "symbol_name",
    "symbol_scope",
    "line_filter",
    "allow_multiple",
    "expected_occurrences",
    "did_you_mean",
    "skip_unmatched",
    "start_anchor",
    "end_anchor",
    "folder_filter",
    "file_filter",
    *_SOURCE_SLICE_KEYS,
)
_VALID_BATCH_ACTIONS = {"patch", "write", "delete"}


def _check_path_filters(
    target_path: Path,
    cwd: Path,
    folder_filter: Optional[str],
    file_filter: Optional[str],
) -> Optional[dict]:
    """Verify folder_filter and file_filter against target_path."""
    if folder_filter:
        resolved_folder = (cwd / folder_filter).resolve()
        try:
            target_path.relative_to(resolved_folder)
        except ValueError:
            return {"error": f"Target file does not reside inside folder_filter '{folder_filter}'"}

    if file_filter:
        file_name = target_path.name
        if file_filter not in file_name:
            return {"error": f"Target file name '{file_name}' does not match file_filter '{file_filter}'"}
    return None


def _read_existing_content(target_path: Path) -> Union[str, bytes]:
    """Read an existing file as UTF-8 text when valid and non-binary, else raw bytes."""
    with open(target_path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw:
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw


def _read_file_and_check_filters(
    target_path: Path,
    cwd: Path,
    folder_filter: Optional[str],
    file_filter: Optional[str],
) -> tuple[str, bool, Optional[dict]]:
    """Verify filters and read original file text content."""
    filter_err = _check_path_filters(target_path, cwd, folder_filter, file_filter)
    if filter_err:
        return "", False, filter_err

    if not target_path.exists():
        return "", False, None

    try:
        with open(target_path, "r", encoding="utf-8", newline="", errors="replace") as f:
            return f.read(), True, None
    except Exception as e:
        return "", True, {"error": f"Failed to read file: {e}"}


def _resolve_target_glob(pattern: str, workspace: Workspace, cwd: Path) -> tuple[list[str], Optional[dict]]:
    """Deterministically resolve a workspace-relative glob pattern into file paths."""
    if not isinstance(pattern, str) or not pattern.strip():
        return [], _create_error_response("Error: 'target_glob' must be a non-empty relative glob pattern.")

    norm_pattern = pattern.replace("\\", "/").strip()
    if Path(norm_pattern).is_absolute():
        return [], _create_error_response("Error: 'target_glob' must be relative to the current workspace.")

    explicit_includes = set(Path(norm_pattern).parts) & _DEFAULT_GLOB_EXCLUDES
    excludes = _DEFAULT_GLOB_EXCLUDES - explicit_includes

    matched: list[str] = []
    seen: set[str] = set()
    try:
        candidates = sorted(cwd.glob(norm_pattern))
    except Exception as e:
        return [], _create_error_response(f"Error: Invalid target_glob '{pattern}': {e}")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            safe_path = workspace.resolve_safe_path(str(candidate))
            rel_path = safe_path.relative_to(cwd)
        except ValueError as e:
            return [], _handle_patch_file_value_error(e, pattern)

        if any(part in excludes for part in rel_path.parts[:-1]):
            continue

        rel_str = os.path.normpath(str(rel_path))
        rel_key = os.path.normcase(rel_str)
        if rel_key not in seen:
            seen.add(rel_key)
            matched.append(rel_str)

    if not matched:
        return [], _create_error_response(f"Error: target_glob '{pattern}' matched zero files.")
    return matched, None


def _resolve_and_record_source(
    params: dict[str, Any],
    workspace: Workspace,
    cwd: Path,
    storage_path: Optional[str],
    bypass_validation: bool,
    allow_binary: bool,
    source_mutations: dict[Path, ResolvedSource],
) -> tuple[Optional[ResolvedSource], Optional[dict]]:
    """Resolve a source_file slice and track delete_source/cut_source mutations cleanly."""
    resolved, err = resolve_source_slice(
        workspace=workspace,
        cwd=cwd,
        source_file=params["source_file"],
        source_start_line=params.get("source_start_line"),
        source_end_line=params.get("source_end_line"),
        source_symbol=params.get("source_symbol"),
        source_start_anchor=params.get("source_start_anchor"),
        source_end_anchor=params.get("source_end_anchor"),
        delete_source=bool(params.get("delete_source", False)),
        cut_source=bool(params.get("cut_source", False)),
        allow_binary=allow_binary,
        storage_path=storage_path,
        bypass_validation=bypass_validation,
    )
    if err:
        return None, _create_error_response(err, target_file=err.get("target_file", params.get("source_file")))
    assert resolved is not None

    if resolved.source_action in ("delete", "patch"):
        if resolved.source_path in source_mutations:
            return None, _create_error_response(
                f"Error: Multiple conflicting cut_source/delete_source operations target '{resolved.source_file}'.",
                target_file=resolved.source_file,
            )
        source_mutations[resolved.source_path] = resolved
    return resolved, None


def _strip_source_keys(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(item)
    for k in _SOURCE_SLICE_KEYS:
        cleaned.pop(k, None)
    return cleaned


def _prepare_patch_item_sources(
    item: dict[str, Any],
    workspace: Workspace,
    cwd: Path,
    storage_path: Optional[str],
    bypass_validation: bool,
    source_mutations: dict[Path, ResolvedSource],
    resolve_top_source: bool = True,
) -> tuple[dict[str, Any], Optional[dict]]:
    """Resolve source_file payloads on a patch operation and any nested replacements[] entries."""
    prepared = dict(item)
    if resolve_top_source and prepared.get("source_file"):
        if prepared.get("replacements") is not None:
            return prepared, _create_error_response(
                "Error: Cannot combine top-level 'source_file' with 'replacements'; specify 'source_file' inside each replacements[] entry."
            )
        if prepared.get("patch_content") is not None:
            return prepared, _create_error_response("Error: Cannot combine 'source_file' with 'patch_content'.")
        if prepared.get("replace_content") is not None or prepared.get("insert_content") is not None:
            return prepared, _create_error_response(
                "Error: Cannot combine 'source_file' with inline 'replace_content' or 'insert_content'."
            )

        resolved, err = _resolve_and_record_source(
            prepared,
            workspace=workspace,
            cwd=cwd,
            storage_path=storage_path,
            bypass_validation=bypass_validation,
            allow_binary=False,
            source_mutations=source_mutations,
        )
        if err:
            return prepared, err
        assert resolved is not None
        prepared = _strip_source_keys(prepared)
        if prepared.get("insert_line") is not None:
            prepared["insert_content"] = resolved.content
        else:
            prepared["replace_content"] = resolved.content

    if isinstance(prepared.get("replacements"), list):
        new_replacements = []
        for idx, r in enumerate(prepared["replacements"]):
            if not isinstance(r, dict) or not r.get("source_file"):
                new_replacements.append(r)
                continue
            if r.get("replace_content") is not None or r.get("insert_content") is not None:
                return prepared, _create_error_response(
                    f"Error: replacements[{idx}] cannot combine 'source_file' with 'replace_content' or 'insert_content'."
                )
            resolved, err = _resolve_and_record_source(
                r,
                workspace=workspace,
                cwd=cwd,
                storage_path=storage_path,
                bypass_validation=bypass_validation,
                allow_binary=False,
                source_mutations=source_mutations,
            )
            if err:
                return prepared, err
            assert resolved is not None
            r_copy = _strip_source_keys(r)
            if r_copy.get("insert_line") is not None:
                r_copy["insert_content"] = resolved.content
            else:
                r_copy["replace_content"] = resolved.content
            new_replacements.append(r_copy)
        prepared["replacements"] = new_replacements

    return prepared, None


def _execute_write_item(
    item: dict[str, Any],
    target_path: Path,
    target_file: str,
    cwd: Path,
    workspace: Workspace,
    storage_path: Optional[str],
    bypass_validation: bool,
    source_mutations: dict[Path, ResolvedSource],
) -> tuple[Optional[dict[str, Any]], Optional[dict]]:
    """Prepare a single create/overwrite operation from code_content or source_file."""
    filter_err = _check_path_filters(target_path, cwd, item.get("folder_filter"), item.get("file_filter"))
    if filter_err:
        return None, _create_error_response(filter_err, target_file=target_file)

    file_exists = target_path.exists()
    allow_overwrite = bool(item.get("allow_overwrite", False))
    if file_exists and not allow_overwrite:
        return None, _create_error_response(
            f"File already exists at '{target_file}'. To overwrite it, set 'allow_overwrite' to true.",
            target_file=target_file,
        )

    original_content: Union[str, bytes] = ""
    if file_exists:
        try:
            original_content = _read_existing_content(target_path)
        except Exception as e:
            return None, _create_error_response(f"Failed to read existing file: {e}", target_file=target_file)

    code_content = item.get("code_content")
    source_file = item.get("source_file")
    has_source_opts = any(
        item.get(k) is not None and item.get(k) is not False
        for k in _SOURCE_SLICE_KEYS[1:]
    )

    if code_content is not None and source_file is not None:
        return None, _create_error_response("Error: Cannot provide both 'code_content' and 'source_file'.", target_file=target_file)
    if code_content is None and source_file is None:
        return None, _create_error_response("Error: Either 'code_content' or 'source_file' is required.", target_file=target_file)
    if source_file is None and has_source_opts:
        return None, _create_error_response("Error: Source slice/move parameters require 'source_file'.", target_file=target_file)

    has_transforms = any(item.get(k) is not None for k in _TRANSFORM_KEYS)
    is_binary = False
    if source_file is not None:
        resolved, err = _resolve_and_record_source(
            item,
            workspace=workspace,
            cwd=cwd,
            storage_path=storage_path,
            bypass_validation=bypass_validation,
            allow_binary=not has_transforms,
            source_mutations=source_mutations,
        )
        if err:
            return None, err
        assert resolved is not None
        initial_content: Union[str, bytes] = resolved.content
        is_binary = resolved.is_binary
        if resolved.is_sliced and isinstance(initial_content, str) and initial_content and not initial_content.endswith("\n"):
            eol = "\r\n" if isinstance(resolved.original_content, str) and "\r\n" in resolved.original_content else "\n"
            initial_content += eol
        if has_transforms and is_binary:
            return None, _create_error_response(
                f"Error: Cannot apply text edits to binary source_file '{source_file}'.",
                target_file=target_file,
            )
    else:
        initial_content = code_content  # type: ignore[assignment]

    occurrences = 1
    warnings: list[str] = []
    suggestion = ""
    engine = None

    if has_transforms:
        assert isinstance(initial_content, str)
        prepared, prep_err = _prepare_patch_item_sources(
            item,
            workspace=workspace,
            cwd=cwd,
            storage_path=storage_path,
            bypass_validation=bypass_validation,
            source_mutations=source_mutations,
            resolve_top_source=False,
        )
        if prep_err:
            return None, prep_err

        transform_kwargs = {
            k: v for k, v in prepared.items()
            if k not in ("target_file", "target_path", "file_content", "bypass_validation", "cwd", "storage_path", "code_content", "allow_overwrite", "action", *_SOURCE_SLICE_KEYS)
        }
        final_content, occurrences, warnings, suggestion, err, engine = _process_single_file_in_memory(
            target_file=target_file,
            target_path=target_path,
            file_content=initial_content,
            bypass_validation=bypass_validation,
            cwd=cwd,
            storage_path=storage_path,
            **transform_kwargs,
        )
        if err:
            return None, _create_error_response(err, target_file=target_file)
    else:
        final_content = initial_content
        if not is_binary and not bypass_validation:
            assert isinstance(final_content, str)
            orig_text = original_content if isinstance(original_content, str) else ""
            try:
                validator = ValidationService()
                validator.validate_file(target_file, final_content, orig_text)
                warnings = validator.lint_file(target_file, final_content)
                suggestion = _get_linter_suggestion(target_file) if warnings else ""
            except SyntaxValidationError as e:
                return None, _create_error_response(
                    f"Syntax Error: {str(e)}",
                    target_file=target_file,
                    filename=e.filename,
                    line=e.line,
                    column=e.column,
                )

    return {
        "target_file": target_file,
        "target_path": target_path,
        "patched_content": final_content,
        "file_content": original_content,
        "file_exists": file_exists,
        "action": "overwrite" if file_exists else "create",
        "is_binary": is_binary,
        "occurrences": occurrences,
        "warnings": warnings,
        "suggestion": suggestion,
        "engine": engine,
    }, None


def _execute_delete_item(
    item: dict[str, Any],
    target_path: Path,
    target_file: str,
    cwd: Path,
) -> tuple[Optional[dict[str, Any]], Optional[dict]]:
    """Prepare a transactional file deletion item."""
    filter_err = _check_path_filters(target_path, cwd, item.get("folder_filter"), item.get("file_filter"))
    if filter_err:
        return None, _create_error_response(filter_err, target_file=target_file)

    if not target_path.exists():
        return None, _create_error_response(f"Target file not found at {target_path}.", target_file=target_file)
    if target_path.is_dir():
        return None, _create_error_response(f"Error: Target path '{target_file}' is a directory, not a file.", target_file=target_file)

    try:
        original_content = _read_existing_content(target_path)
    except Exception as e:
        return None, _create_error_response(f"Failed to read target file before delete: {e}", target_file=target_file)

    return {
        "target_file": target_file,
        "target_path": target_path,
        "patched_content": None,
        "file_content": original_content,
        "file_exists": True,
        "action": "delete",
        "is_binary": isinstance(original_content, bytes),
        "occurrences": 1,
        "warnings": [],
        "suggestion": "",
        "engine": None,
    }, None


def _append_source_mutations(
    source_mutations: dict[Path, ResolvedSource],
    target_paths_norm: set[str],
    transaction: FileTransaction,
    original_contents: dict[str, Union[str, bytes]],
    modifications: dict[Path, Optional[Union[str, bytes]]],
    processed_files: list[dict[str, Any]],
) -> Optional[dict]:
    """Attach cut_source/delete_source mutations to transaction plan."""
    for source_path, resolved in source_mutations.items():
        if os.path.normcase(str(source_path)) in target_paths_norm:
            return _create_error_response(
                f"Error: Source file '{resolved.source_file}' cannot also be a target_file when cut_source or delete_source is enabled.",
                target_file=resolved.source_file,
            )
        transaction.register_file(source_path)
        original_contents[str(source_path)] = resolved.original_content
        original_contents[resolved.source_file] = resolved.original_content
        if resolved.source_action == "delete":
            modifications[source_path] = None
            processed_files.append({
                "target_file": resolved.source_file,
                "target_path": source_path,
                "patched_content": None,
                "file_content": resolved.original_content,
                "file_exists": True,
                "action": "delete",
                "is_binary": resolved.is_binary,
                "occurrences": 1,
                "warnings": [],
                "suggestion": "",
                "engine": None,
            })
        else:
            modifications[source_path] = resolved.source_modified_content
            processed_files.append({
                "target_file": resolved.source_file,
                "target_path": source_path,
                "patched_content": resolved.source_modified_content,
                "file_content": resolved.original_content,
                "file_exists": True,
                "action": "patch",
                "is_binary": False,
                "occurrences": 1,
                "warnings": resolved.warnings,
                "suggestion": resolved.suggestion,
                "engine": None,
            })
    return None


def _build_file_items(
    target_file: Optional[str],
    target_files: Optional[list[str]],
    target_glob: Optional[str],
    files_param: Optional[list[dict]],
    top_defaults: dict[str, Any],
    workspace: Workspace,
    cwd: Path,
) -> tuple[list[dict[str, Any]], bool, Optional[dict]]:
    """Normalize target_file, target_files, target_glob, or files[] into unified items."""
    selector_count = sum(x is not None for x in (target_file, target_files, target_glob))
    if files_param is not None and selector_count > 0:
        return [], False, _create_error_response(
            "Error: Cannot provide both 'files' array and top-level single-file edit parameters."
        )
    if selector_count > 1:
        return [], False, _create_error_response(
            "Error: Specify only one of 'target_file', 'target_files', or 'target_glob'."
        )
    if files_param is None and selector_count == 0:
        return [], False, _create_error_response(
            "Error: One of 'target_file', 'target_files', 'target_glob', or 'files' must be provided."
        )

    if files_param is not None:
        if not isinstance(files_param, list) or not files_param:
            return [], False, _create_error_response("Error: 'files' must be a non-empty array.")
        items: list[dict[str, Any]] = []
        for raw_item in files_param:
            if not isinstance(raw_item, dict):
                return [], True, _create_error_response("Error: Each entry in 'files' must be an object.")
            action = raw_item.get("action", "patch")
            if action not in _VALID_BATCH_ACTIONS:
                return [], True, _create_error_response(
                    f"Error: Invalid action '{action}'. Expected 'patch', 'write', or 'delete'.",
                    target_file=raw_item.get("target_file"),
                )
            if action == "delete":
                merged = {k: v for k, v in top_defaults.items() if k in ("folder_filter", "file_filter") and v is not None}
            else:
                merged = {k: copy.deepcopy(v) for k, v in top_defaults.items() if v is not None}
            merged.update(raw_item)
            merged["action"] = action
            items.append(merged)
        return items, True, None

    if target_glob is not None:
        glob_targets, err = _resolve_target_glob(target_glob, workspace, cwd)
        if err:
            return [], True, err
        return [{"target_file": tf, "action": "patch", **copy.deepcopy(top_defaults)} for tf in glob_targets], True, None

    if target_files is not None:
        if not isinstance(target_files, list) or not target_files or not all(isinstance(t, str) and t.strip() for t in target_files):
            return [], True, _create_error_response("Error: 'target_files' must be a non-empty list of file paths.")
        return [{"target_file": tf, "action": "patch", **copy.deepcopy(top_defaults)} for tf in target_files], True, None

    return [{"target_file": target_file, "action": "patch", **top_defaults}], False, None


def patch_file(  # noqa: C901 # NOSONAR
    target_file: Optional[str] = None,
    search_content: Optional[str] = None,
    replace_content: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: bool = False,
    patch_content: Optional[str] = None,
    replacements: Optional[list[dict]] = None,
    files: Optional[list[dict]] = None,
    patches: Optional[list[dict]] = None,
    insert_line: Optional[int] = None,
    insert_content: Optional[str] = None,
    auto_indent: bool = True,
    target_files: Optional[list[str]] = None,
    target_glob: Optional[str] = None,
    skip_unmatched: bool = False,
    **kwargs,
) -> dict:
    """Perform exact/AST search-and-replace, broadcast edits, or atomic mixed file batches."""
    files_param = files if files is not None else patches
    storage_path = kwargs.get("storage_path")
    bypass_validation = bool(kwargs.get("bypass_validation", False))
    cwd = Path.cwd().resolve()
    workspace = Workspace(cwd, storage_path)

    top_defaults: dict[str, Any] = {
        "search_content": search_content,
        "replace_content": replace_content,
        "start_line": start_line,
        "end_line": end_line,
        "patch_content": patch_content,
        "replacements": replacements,
        "insert_line": insert_line,
        "insert_content": insert_content,
        "auto_indent": auto_indent,
        "skip_unmatched": skip_unmatched,
    }
    for k in _INHERITABLE_PATCH_KEYS:
        if k in kwargs and k not in top_defaults:
            top_defaults[k] = kwargs[k]

    file_items, multi_file_mode, norm_err = _build_file_items(
        target_file=target_file,
        target_files=target_files,
        target_glob=target_glob,
        files_param=files_param,
        top_defaults=top_defaults,
        workspace=workspace,
        cwd=cwd,
    )
    if norm_err:
        return norm_err

    resolved_paths: list[Path] = []
    for item in file_items:
        tf = item.get("target_file")
        if not tf:
            return _create_error_response("Error: 'target_file' is required for each item in 'files' array.")
        item["target_file"] = os.path.normpath(tf)
        try:
            resolved_paths.append(workspace.resolve_safe_path(item["target_file"]))
        except Exception as e:
            return _handle_patch_file_value_error(e, item["target_file"])

    norm_resolved = [os.path.normcase(str(rp)) for rp in resolved_paths]
    if len(norm_resolved) != len(set(norm_resolved)):
        return _create_error_response(
            "Error: Duplicate resolved target_file paths found in 'files' batch. For multiple edits in the same file, use 'replacements' within a single item."
        )

    processed_files: list[dict[str, Any]] = []
    skipped_files: list[dict[str, str]] = []
    modifications: dict[Path, Optional[Union[str, bytes]]] = {}
    original_contents: dict[str, Union[str, bytes]] = {}
    source_mutations: dict[Path, ResolvedSource] = {}

    for item, target_path in zip(file_items, resolved_paths):
        tf = item["target_file"]
        action = item.get("action", "patch")

        if action == "write":
            pf, err = _execute_write_item(
                item=item,
                target_path=target_path,
                target_file=tf,
                cwd=cwd,
                workspace=workspace,
                storage_path=storage_path,
                bypass_validation=bypass_validation,
                source_mutations=source_mutations,
            )
            if err:
                return err
            assert pf is not None
            processed_files.append(pf)
            modifications[target_path] = pf["patched_content"]
            original_contents[str(target_path)] = pf["file_content"]
            original_contents[tf] = pf["file_content"]
            continue

        if action == "delete":
            pf, err = _execute_delete_item(item, target_path, tf, cwd)
            if err:
                return err
            assert pf is not None
            processed_files.append(pf)
            modifications[target_path] = None
            original_contents[str(target_path)] = pf["file_content"]
            original_contents[tf] = pf["file_content"]
            continue

        folder_filter = item.get("folder_filter")
        file_filter = item.get("file_filter")
        file_content, file_exists, filter_err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
        if filter_err:
            return _create_error_response(filter_err, target_file=tf)

        item_patch_content = item.get("patch_content")
        is_creation_diff = item_patch_content is not None and (
            "old_start=0" in item_patch_content or "@@ -0,0" in item_patch_content or "@@ -0 " in item_patch_content
        )
        if not file_exists and not is_creation_diff:
            return _create_error_response(
                f"Target file not found at {target_path}. Use write_file to create new files or supply a valid patch_content creation diff.",
                target_file=tf,
            )

        prepared_item, prep_err = _prepare_patch_item_sources(
            item,
            workspace=workspace,
            cwd=cwd,
            storage_path=storage_path,
            bypass_validation=bypass_validation,
            source_mutations=source_mutations,
            resolve_top_source=True,
        )
        if prep_err:
            return prep_err

        item_kwargs = dict(prepared_item)
        item_skip_unmatched = bool(item_kwargs.pop("skip_unmatched", False))
        for k in ("target_file", "target_path", "file_content", "bypass_validation", "cwd", "storage_path", "action"):
            item_kwargs.pop(k, None)

        patched_content, occurrences, warnings, suggestion, err, engine = _process_single_file_in_memory(
            target_file=tf,
            target_path=target_path,
            file_content=file_content,
            bypass_validation=bypass_validation,
            cwd=cwd,
            storage_path=storage_path,
            **item_kwargs,
        )
        if err:
            if item_skip_unmatched and isinstance(err, dict) and err.get("no_match"):
                skipped_files.append({"target_file": tf, "reason": err.get("error", "Pattern not found.")})
                continue
            if multi_file_mode:
                err_msg = err.get("error", str(err)) if isinstance(err, dict) else str(err)
                return _create_error_response(f"Validation failed for file {tf}: {err_msg}", target_file=tf, detail=err_msg)
            return _create_error_response(err, target_file=tf)

        original_contents[str(target_path)] = file_content
        original_contents[tf] = file_content
        modifications[target_path] = patched_content
        processed_files.append({
            "target_file": tf,
            "target_path": target_path,
            "patched_content": patched_content,
            "file_content": file_content,
            "file_exists": file_exists,
            "action": "patch",
            "is_binary": False,
            "occurrences": occurrences,
            "warnings": warnings,
            "suggestion": suggestion,
            "engine": engine,
        })

    if not processed_files:
        return _create_error_response(
            f"Error: Pattern matched 0 of {len(skipped_files)} targeted file(s); all files skipped.",
            skipped_files=skipped_files,
        )

    all_touched_paths = [pf["target_path"] for pf in processed_files] + list(source_mutations.keys())
    workspace_root = resolve_common_workspace_root(all_touched_paths, cwd)
    transaction = FileTransaction(workspace_root)
    for pf in processed_files:
        transaction.register_file(pf["target_path"])

    src_conflict = _append_source_mutations(
        source_mutations=source_mutations,
        target_paths_norm={os.path.normcase(str(pf["target_path"])) for pf in processed_files},
        transaction=transaction,
        original_contents=original_contents,
        modifications=modifications,
        processed_files=processed_files,
    )
    if src_conflict:
        return src_conflict

    return execute_file_plan(
        processed_files=processed_files,
        transaction=transaction,
        original_contents=original_contents,
        modifications=modifications,
        dry_run=dry_run,
        multi_file_mode=multi_file_mode,
        skipped_files=skipped_files,
    )


def write_file(
    target_file: str,
    code_content: Optional[str] = None,
    allow_overwrite: bool = False,
    dry_run: bool = False,
    source_file: Optional[str] = None,
    source_start_line: Optional[int] = None,
    source_end_line: Optional[int] = None,
    source_symbol: Optional[str] = None,
    source_start_anchor: Optional[str] = None,
    source_end_anchor: Optional[str] = None,
    delete_source: bool = False,
    cut_source: bool = False,
    **kwargs,
) -> dict:
    """Create or overwrite a file from code_content or a source_file copy/slice with validation."""
    storage_path = kwargs.get("storage_path")
    bypass_validation = bool(kwargs.get("bypass_validation", False))
    try:
        norm_tf = os.path.normpath(target_file)
        cwd = Path.cwd().resolve()
        workspace = Workspace(cwd, storage_path)
        target_path = workspace.resolve_safe_path(norm_tf)
    except ValueError as e:
        return _handle_patch_file_value_error(e, target_file)

    item: dict[str, Any] = {
        "target_file": norm_tf,
        "code_content": code_content,
        "allow_overwrite": allow_overwrite,
        "source_file": source_file,
        "source_start_line": source_start_line,
        "source_end_line": source_end_line,
        "source_symbol": source_symbol,
        "source_start_anchor": source_start_anchor,
        "source_end_anchor": source_end_anchor,
        "delete_source": delete_source,
        "cut_source": cut_source,
        **kwargs,
    }

    source_mutations: dict[Path, ResolvedSource] = {}
    pf, err = _execute_write_item(
        item=item,
        target_path=target_path,
        target_file=norm_tf,
        cwd=cwd,
        workspace=workspace,
        storage_path=storage_path,
        bypass_validation=bypass_validation,
        source_mutations=source_mutations,
    )
    if err:
        return err
    assert pf is not None

    all_touched = [target_path] + list(source_mutations.keys())
    transaction = FileTransaction(resolve_common_workspace_root(all_touched, cwd))
    transaction.register_file(target_path)

    original_contents: dict[str, Union[str, bytes]] = {
        str(target_path): pf["file_content"],
        norm_tf: pf["file_content"],
    }
    modifications: dict[Path, Optional[Union[str, bytes]]] = {target_path: pf["patched_content"]}
    processed_files = [pf]

    src_conflict = _append_source_mutations(
        source_mutations=source_mutations,
        target_paths_norm={os.path.normcase(str(target_path))},
        transaction=transaction,
        original_contents=original_contents,
        modifications=modifications,
        processed_files=processed_files,
    )
    if src_conflict:
        return src_conflict

    return execute_file_plan(
        processed_files=processed_files,
        transaction=transaction,
        original_contents=original_contents,
        modifications=modifications,
        dry_run=dry_run,
        multi_file_mode=False,
    )


def _resolve_workspace_root(patches: list[dict], workspace: Workspace, cwd: Path) -> Path:
    """Resolve single common workspace root for list of patch item dicts."""
    target_paths = []
    for p in patches:
        tf = p.get("target_file")
        if tf:
            try:
                target_paths.append(workspace.resolve_safe_path(tf))
            except ValueError:
                pass
    return resolve_common_workspace_root(target_paths, cwd)
