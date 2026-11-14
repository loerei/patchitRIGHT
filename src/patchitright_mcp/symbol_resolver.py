"""Clean-room single-file AST symbol resolver for patchitRIGHT."""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .body_parser import EXTENSION_TO_LANGUAGE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolMatch:
    """Resolved 1-indexed inclusive source boundaries for a named declaration."""

    name: str
    qualified_name: str
    start_line: int
    end_line: int
    kind: str
    has_body: bool = True
    is_overload: bool = False
    nesting_depth: int = 0


def _normalize_qualified(name: str) -> str:
    """Normalize scope separators (e.g. '::' -> '.') for uniform matching."""
    return name.strip().replace("::", ".")


def _decorator_is_overload(dec: ast.AST) -> bool:
    """Return True when a Python decorator represents typing.overload."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id == "overload"
    if isinstance(target, ast.Attribute):
        return target.attr == "overload"
    return False


def _extract_python_ast_symbols(source_text: str) -> list[SymbolMatch]:
    """Extract Python functions, async functions, and classes via stdlib ast."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    symbols: list[SymbolMatch] = []

    def _visit_block(stmts: list[ast.stmt], scope: list[str], depth: int) -> None:
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                dec_lines = [d.lineno for d in node.decorator_list if hasattr(d, "lineno")]
                start_line = min(dec_lines) if dec_lines else node.lineno
                end_line = getattr(node, "end_lineno", None) or node.lineno
                qual = ".".join([*scope, node.name]) if scope else node.name
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        target = dec.func if isinstance(dec, ast.Call) else dec
                        if isinstance(target, ast.Name) and target.id == "property":
                            kind = "getter"
                            break
                        if isinstance(target, ast.Attribute) and target.attr in ("setter", "deleter"):
                            kind = "setter"
                            break
                is_overload = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    _decorator_is_overload(d) for d in node.decorator_list
                )
                has_body = bool(node.body)
                if (
                    len(node.body) == 1
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and node.body[0].value.value is Ellipsis
                ):
                    has_body = False
                symbols.append(
                    SymbolMatch(
                        name=node.name,
                        qualified_name=qual,
                        start_line=start_line,
                        end_line=end_line,
                        kind=kind,
                        has_body=has_body,
                        is_overload=is_overload,
                        nesting_depth=depth,
                    )
                )
                _visit_block(node.body, [*scope, node.name], depth + 1)
            else:
                for attr in ("body", "orelse", "finalbody"):
                    child_stmts = getattr(node, attr, None)
                    if isinstance(child_stmts, list):
                        _visit_block(child_stmts, scope, depth)
                handlers = getattr(node, "handlers", None)
                if isinstance(handlers, list):
                    for handler in handlers:
                        if hasattr(handler, "body"):
                            _visit_block(handler.body, scope, depth)

    _visit_block(tree.body, [], 0)
    return symbols


_MAX_BYTES = 5 * 1024 * 1024

_JS_TS_VALUE_FUNC_TYPES = frozenset({
    "arrow_function",
    "function_expression",
    "function",
    "generator_function",
    "class",
})

_JS_TS_WRAPPER_EXPR_TYPES = frozenset({
    "parenthesized_expression",
    "as_expression",
    "satisfies_expression",
    "type_assertion",
    "non_null_expression",
})


def _get_ts_parser(language: str):
    """Return a tree-sitter parser for language, or None if unavailable."""
    try:
        from tree_sitter_language_pack import get_parser
        return get_parser(language)
    except Exception as exc:
        logger.debug("tree-sitter parser unavailable for %s: %s", language, exc)
        return None


def _node_text(node, source_bytes: bytes) -> str:
    """Decode UTF-8 slice corresponding to a tree-sitter node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _expand_leading_span(anchor_node, source_bytes: bytes) -> int:
    """Return 0-indexed start row including contiguous decorators, attributes, and doc comments."""
    cur = anchor_node
    start_row = cur.start_point[0]

    # Walk backward across sibling decorators / attributes attached cleanly before cur.
    prev = cur.prev_named_sibling
    while prev is not None and prev.type in ("decorator", "attribute_item", "attribute_list"):
        if prev.end_point[0] >= start_row - 1:
            start_row = prev.start_point[0]
            cur = prev
            prev = cur.prev_named_sibling
        else:
            break

    # Check if an attached doc comment sits immediately above the earliest decorator/declaration.
    if prev is not None and "comment" in prev.type:
        comment_text = _node_text(prev, source_bytes).lstrip()
        if comment_text.startswith(("/**", "///")) and prev.end_point[0] >= start_row - 1:
            start_row = prev.start_point[0]

    return start_row


def _unwrap_js_value(node):
    """Unwrap parenthesized or TS cast expressions around a function/class value."""
    while node is not None and node.type in _JS_TS_WRAPPER_EXPR_TYPES:
        inner = node.child_by_field_name("expression") or next(
            (c for c in node.named_children if c.type != "type"),
            None,
        )
        if inner is None:
            break
        node = inner
    return node


def _extract_js_ts_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract functions, classes, methods, getters/setters, and arrow bindings for JS/TS/TSX."""
    symbols: list[SymbolMatch] = []

    def _visit(node, scope: list[str], depth: int, export_wrapper=None) -> None:
        ntype = node.type

        if ntype == "export_statement":
            decl = node.child_by_field_name("declaration") or node.child_by_field_name("value")
            if decl is None:
                for ch in node.named_children:
                    if ch.type not in ("decorator",):
                        decl = ch
                        break
            if decl is not None:
                _visit(decl, scope, depth, export_wrapper=node)
            return

        if ntype in ("function_declaration", "generator_function_declaration", "function_signature"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source_bytes)
                qual = ".".join([*scope, name]) if scope else name
                outer = export_wrapper or node
                has_body = ntype != "function_signature" and node.child_by_field_name("body") is not None
                symbols.append(
                    SymbolMatch(
                        name=name,
                        qualified_name=qual,
                        start_line=_expand_leading_span(outer, source_bytes) + 1,
                        end_line=outer.end_point[0] + 1,
                        kind="function",
                        has_body=has_body,
                        is_overload=(ntype == "function_signature"),
                        nesting_depth=depth,
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    for ch in body.named_children:
                        _visit(ch, [*scope, name], depth + 1)
            return

        if ntype in (
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        ):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source_bytes)
                qual = ".".join([*scope, name]) if scope else name
                outer = export_wrapper or node
                symbols.append(
                    SymbolMatch(
                        name=name,
                        qualified_name=qual,
                        start_line=_expand_leading_span(outer, source_bytes) + 1,
                        end_line=outer.end_point[0] + 1,
                        kind="class" if "class" in ntype else "type",
                        has_body=(ntype != "type_alias_declaration"),
                        nesting_depth=depth,
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    for ch in body.named_children:
                        _visit(ch, [*scope, name], depth + 1)
            return

        if ntype in ("method_definition", "method_signature", "abstract_method_signature"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source_bytes)
                qual = ".".join([*scope, name]) if scope else name
                has_body = ntype == "method_definition" and node.child_by_field_name("body") is not None
                accessor_kind = "method"
                if ntype == "method_definition":
                    for ch in node.children:
                        if ch == name_node:
                            break
                        if ch.type in ("get", "set"):
                            accessor_kind = "getter" if ch.type == "get" else "setter"
                            break
                symbols.append(
                    SymbolMatch(
                        name=name,
                        qualified_name=qual,
                        start_line=_expand_leading_span(node, source_bytes) + 1,
                        end_line=node.end_point[0] + 1,
                        kind=accessor_kind,
                        has_body=has_body,
                        is_overload=(ntype != "method_definition"),
                        nesting_depth=depth,
                    )
                )
            return

        if ntype in ("public_field_definition", "field_definition", "property_definition"):
            name_node = node.child_by_field_name("name")
            val_node = _unwrap_js_value(node.child_by_field_name("value"))
            if name_node is not None and val_node is not None and val_node.type in _JS_TS_VALUE_FUNC_TYPES:
                name = _node_text(name_node, source_bytes)
                qual = ".".join([*scope, name]) if scope else name
                symbols.append(
                    SymbolMatch(
                        name=name,
                        qualified_name=qual,
                        start_line=_expand_leading_span(node, source_bytes) + 1,
                        end_line=node.end_point[0] + 1,
                        kind="method",
                        has_body=True,
                        nesting_depth=depth,
                    )
                )
            return

        if ntype in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in node.named_children if c.type == "variable_declarator"]
            for decl in declarators:
                name_node = decl.child_by_field_name("name")
                val_node = _unwrap_js_value(decl.child_by_field_name("value"))
                if name_node is not None and name_node.type == "identifier" and val_node is not None and val_node.type in _JS_TS_VALUE_FUNC_TYPES:
                    name = _node_text(name_node, source_bytes)
                    qual = ".".join([*scope, name]) if scope else name
                    outer = (export_wrapper or node) if len(declarators) == 1 else decl
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=qual,
                            start_line=_expand_leading_span(outer, source_bytes) + 1,
                            end_line=outer.end_point[0] + 1,
                            kind="function" if val_node.type != "class" else "class",
                            has_body=True,
                            nesting_depth=depth,
                        )
                    )
            return

        if ntype in ("program", "statement_block", "internal_module", "namespace_declaration"):
            for ch in node.named_children:
                _visit(ch, scope, depth)

    _visit(root_node, [], 0)
    return symbols


def _strip_type_identifier(node, source_bytes: bytes) -> str:
    """Extract base identifier name from wrapper type nodes (pointers, generics, scoped types)."""
    if node is None:
        return ""
    cur = node
    while cur is not None:
        if cur.type in ("identifier", "type_identifier", "field_identifier", "namespace_identifier"):
            return _node_text(cur, source_bytes)
        type_field = cur.child_by_field_name("type") or cur.child_by_field_name("name")
        if type_field is not None:
            cur = type_field
            continue
        next_child = next(
            (
                c
                for c in cur.named_children
                if c.type in ("identifier", "type_identifier", "field_identifier", "pointer_type", "generic_type", "scoped_type_identifier", "qualified_type")
            ),
            None,
        )
        if next_child is None:
            break
        cur = next_child
    return _node_text(node, source_bytes).lstrip("*&").strip()


def _extract_go_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract Go functions, receiver methods, and type declarations."""
    symbols: list[SymbolMatch] = []

    def _visit(node) -> None:
        for ch in node.named_children:
            ntype = ch.type
            if ntype == "function_declaration":
                name_node = ch.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node, source_bytes)
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=name,
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="function",
                            has_body=ch.child_by_field_name("body") is not None,
                        )
                    )
            elif ntype == "method_declaration":
                name_node = ch.child_by_field_name("name")
                recv_node = ch.child_by_field_name("receiver")
                recv_name = ""
                if recv_node is not None:
                    param = next((p for p in recv_node.named_children if p.type == "parameter_declaration"), None)
                    if param is not None:
                        recv_name = _strip_type_identifier(param.child_by_field_name("type"), source_bytes)
                if name_node is not None:
                    name = _node_text(name_node, source_bytes)
                    qual = f"{recv_name}.{name}" if recv_name else name
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=qual,
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="method",
                            has_body=ch.child_by_field_name("body") is not None,
                            nesting_depth=1 if recv_name else 0,
                        )
                    )
            elif ntype == "type_declaration":
                specs = [s for s in ch.named_children if s.type == "type_spec"]
                for spec in specs:
                    name_node = spec.child_by_field_name("name")
                    if name_node is not None:
                        name = _node_text(name_node, source_bytes)
                        outer = ch if len(specs) == 1 else spec
                        symbols.append(
                            SymbolMatch(
                                name=name,
                                qualified_name=name,
                                start_line=_expand_leading_span(outer, source_bytes) + 1,
                                end_line=outer.end_point[0] + 1,
                                kind="type",
                                has_body=True,
                            )
                        )

    _visit(root_node)
    return symbols


def _extract_rust_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract Rust functions, structs, enums, traits, modules, and impl methods."""
    symbols: list[SymbolMatch] = []

    def _visit(node, scope: list[str], depth: int) -> None:
        for ch in node.named_children:
            ntype = ch.type
            if ntype in ("function_item", "function_signature_item", "struct_item", "enum_item", "trait_item", "type_item", "mod_item"):
                name_node = ch.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node, source_bytes)
                    qual = ".".join([*scope, name]) if scope else name
                    body = ch.child_by_field_name("body")
                    has_body = (ntype != "function_signature_item") and (body is not None or ntype == "type_item")
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=qual,
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="function" if "function" in ntype else "type",
                            has_body=has_body,
                            nesting_depth=depth,
                        )
                    )
                    if body is not None and ntype in ("trait_item", "mod_item"):
                        _visit(body, [*scope, name], depth + 1)
            elif ntype == "impl_item":
                type_name = _strip_type_identifier(ch.child_by_field_name("type"), source_bytes)
                body = ch.child_by_field_name("body")
                if body is not None:
                    next_scope = [*scope, type_name] if type_name else scope
                    _visit(body, next_scope, depth + (1 if type_name else 0))

    _visit(root_node, [], 0)
    return symbols


def _unwrap_c_declarator(decl_node, source_bytes: bytes) -> tuple[str, list[str]]:
    """Extract (short_name, qualified_parts) from C/C++ declarators."""
    cur = decl_node
    while cur is not None:
        if cur.type in ("identifier", "field_identifier"):
            text = _node_text(cur, source_bytes)
            return text, [text]
        if cur.type == "qualified_identifier":
            parts = [p for p in _node_text(cur, source_bytes).split("::") if p]
            return (parts[-1], parts) if parts else ("", [])
        inner = cur.child_by_field_name("declarator")
        if inner is not None:
            cur = inner
            continue
        break
    return "", []


def _extract_c_cpp_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract C/C++ functions, prototypes, structs, classes, enums, and namespaces."""
    symbols: list[SymbolMatch] = []

    def _visit(node, scope: list[str], depth: int) -> None:
        for ch in node.named_children:
            ntype = ch.type
            if ntype == "function_definition":
                short, qual_parts = _unwrap_c_declarator(ch.child_by_field_name("declarator"), source_bytes)
                if short:
                    full_parts = qual_parts if len(qual_parts) > 1 else [*scope, short]
                    symbols.append(
                        SymbolMatch(
                            name=short,
                            qualified_name=".".join(full_parts),
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="function",
                            has_body=ch.child_by_field_name("body") is not None,
                            nesting_depth=len(full_parts) - 1,
                        )
                    )
            elif ntype in ("declaration", "field_declaration"):
                decl = ch.child_by_field_name("declarator")
                has_fn = decl is not None and ("function_declarator" in str(decl.type) or _node_text(decl, source_bytes).find("(") != -1)
                if has_fn and decl is not None and decl.type == "function_declarator":
                    short, qual_parts = _unwrap_c_declarator(decl, source_bytes)
                    if short:
                        full_parts = qual_parts if len(qual_parts) > 1 else [*scope, short]
                        symbols.append(
                            SymbolMatch(
                                name=short,
                                qualified_name=".".join(full_parts),
                                start_line=_expand_leading_span(ch, source_bytes) + 1,
                                end_line=ch.end_point[0] + 1,
                                kind="function",
                                has_body=False,
                                nesting_depth=len(full_parts) - 1,
                            )
                        )
            elif ntype in ("class_specifier", "struct_specifier", "enum_specifier", "namespace_definition"):
                name_node = ch.child_by_field_name("name")
                body = ch.child_by_field_name("body")
                if name_node is not None and body is not None:
                    name = _node_text(name_node, source_bytes)
                    qual = ".".join([*scope, name]) if scope else name
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=qual,
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="class" if ntype != "namespace_definition" else "namespace",
                            has_body=True,
                            nesting_depth=depth,
                        )
                    )
                    _visit(body, [*scope, name], depth + 1)

    _visit(root_node, [], 0)
    return symbols


def _extract_class_lang_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract classes, interfaces, structs, namespaces, methods, and constructors for Java/C#."""
    symbols: list[SymbolMatch] = []

    def _visit(node, scope: list[str], depth: int) -> None:
        for ch in node.named_children:
            ntype = ch.type
            if ntype in (
                "namespace_declaration",
                "file_scoped_namespace_declaration",
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
                "enum_declaration",
            ):
                name_node = ch.child_by_field_name("name")
                body = ch.child_by_field_name("body")
                if name_node is not None:
                    name = _node_text(name_node, source_bytes)
                    qual = ".".join([*scope, name]) if scope else name
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=qual,
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="class",
                            has_body=body is not None,
                            nesting_depth=depth,
                        )
                    )
                    if body is not None:
                        _visit(body, [*scope, name], depth + 1)
                    elif ntype == "file_scoped_namespace_declaration":
                        # Subsequent top-level siblings in the compilation_unit belong to this namespace.
                        scope = [*scope, name]
            elif ntype in ("method_declaration", "constructor_declaration", "property_declaration"):
                name_node = ch.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node, source_bytes)
                    qual = ".".join([*scope, name]) if scope else name
                    has_body = (
                        ch.child_by_field_name("body") is not None
                        or ch.child_by_field_name("accessors") is not None
                        or any(c.type in ("block", "arrow_expression_clause", "accessor_list") for c in ch.named_children)
                    )
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=qual,
                            start_line=_expand_leading_span(ch, source_bytes) + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="method",
                            has_body=has_body,
                            nesting_depth=depth,
                        )
                    )

    _visit(root_node, [], 0)
    return symbols


def _extract_css_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract CSS/SCSS rule_set selectors."""
    symbols: list[SymbolMatch] = []

    def _visit(node, scope: list[str], depth: int) -> None:
        for ch in node.named_children:
            if ch.type == "rule_set":
                sel_node = next((c for c in ch.named_children if c.type == "selectors"), None)
                if sel_node is not None:
                    sel = _node_text(sel_node, source_bytes).strip()
                    if sel:
                        qual = " ".join([*scope, sel]) if scope else sel
                        symbols.append(
                            SymbolMatch(
                                name=sel,
                                qualified_name=qual,
                                start_line=ch.start_point[0] + 1,
                                end_line=ch.end_point[0] + 1,
                                kind="rule",
                                has_body=any(c.type == "block" for c in ch.named_children),
                                nesting_depth=depth,
                            )
                        )
                        block = next((c for c in ch.named_children if c.type == "block"), None)
                        if block is not None:
                            _visit(block, [*scope, sel], depth + 1)
            elif ch.type in ("media_statement", "keyframes_statement", "mixin_statement"):
                name_node = next((c for c in ch.named_children if c.type in ("identifier", "keyframes_name")), None)
                if name_node is not None:
                    name = _node_text(name_node, source_bytes)
                    symbols.append(
                        SymbolMatch(
                            name=name,
                            qualified_name=name,
                            start_line=ch.start_point[0] + 1,
                            end_line=ch.end_point[0] + 1,
                            kind="at_rule",
                            has_body=True,
                            nesting_depth=depth,
                        )
                    )
                for sub in ch.named_children:
                    if sub.type == "block":
                        _visit(sub, scope, depth + 1)

    _visit(root_node, [], 0)
    return symbols


_HTML_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def _extract_html_symbols(source_bytes: bytes, root_node) -> list[SymbolMatch]:
    """Extract HTML element spans keyed by tag name, #id, and tag#id."""
    symbols: list[SymbolMatch] = []

    def _visit(node, depth: int) -> None:
        for ch in node.named_children:
            if ch.type in ("element", "script_element", "style_element"):
                open_tag = next(
                    (c for c in ch.named_children if c.type in ("start_tag", "self_closing_tag")),
                    None,
                )
                if open_tag is None:
                    continue
                tag_node = next((c for c in open_tag.named_children if c.type == "tag_name"), None)
                if tag_node is None:
                    continue
                tag = _node_text(tag_node, source_bytes).lower()
                is_self_closing = open_tag.type == "self_closing_tag" or tag in _HTML_VOID_ELEMENTS
                id_val = None
                for attr in open_tag.named_children:
                    if attr.type == "attribute":
                        attr_name_node = next((c for c in attr.named_children if c.type == "attribute_name"), None)
                        if attr_name_node is not None and _node_text(attr_name_node, source_bytes).lower() == "id":
                            val_node = next((c for c in attr.named_children if c.type in ("quoted_attribute_value", "attribute_value")), None)
                            if val_node is not None:
                                id_val = _node_text(val_node, source_bytes).strip("'\"")
                            break
                start_l = ch.start_point[0] + 1
                end_l = ch.end_point[0] + 1
                qual = f"{tag}#{id_val}" if id_val else tag
                symbols.append(
                    SymbolMatch(
                        name=tag,
                        qualified_name=qual,
                        start_line=start_l,
                        end_line=end_l,
                        kind="element",
                        has_body=not is_self_closing,
                        nesting_depth=depth,
                    )
                )
                if id_val:
                    symbols.append(
                        SymbolMatch(
                            name=f"#{id_val}",
                            qualified_name=f"#{id_val}",
                            start_line=start_l,
                            end_line=end_l,
                            kind="element",
                            has_body=not is_self_closing,
                            nesting_depth=depth,
                        )
                    )
                _visit(ch, depth + 1)

    _visit(root_node, 0)
    return symbols


_FALLBACK_DEF_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\*?|class|func|fn|def)\s+([A-Za-z_][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][\w$]*\s*=>)"
)


def _extract_fallback_symbols(source_text: str, language: str) -> list[SymbolMatch]:
    """Regex and brace/indent boundary fallback when AST parsing is unavailable."""
    lines = source_text.split("\n")
    symbols: list[SymbolMatch] = []
    n = len(lines)

    for idx, line in enumerate(lines):
        m = _FALLBACK_DEF_RE.match(line)
        if not m:
            continue
        name = m.group(1) or m.group(2)
        if not name:
            continue
        start_line = idx + 1
        end_line = start_line

        if language == "python":
            base_indent = len(line) - len(line.lstrip())
            j = idx + 1
            while j < n:
                nxt = lines[j]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= base_indent:
                    break
                if nxt.strip():
                    end_line = j + 1
                j += 1
        else:
            brace_depth = 0
            saw_open = False
            j = idx
            while j < n:
                for ch in lines[j]:
                    if ch == "{":
                        brace_depth += 1
                        saw_open = True
                    elif ch == "}":
                        brace_depth -= 1
                end_line = j + 1
                if (saw_open and brace_depth <= 0) or (not saw_open and lines[j].rstrip().endswith(";")):
                    break
                j += 1

        symbols.append(
            SymbolMatch(
                name=name,
                qualified_name=name,
                start_line=start_line,
                end_line=end_line,
                kind="fallback",
                has_body=True,
                nesting_depth=0,
            )
        )
    return symbols


_TS_LANGUAGE_EXTRACTORS = {
    "javascript": _extract_js_ts_symbols,
    "typescript": _extract_js_ts_symbols,
    "tsx": _extract_js_ts_symbols,
    "go": _extract_go_symbols,
    "rust": _extract_rust_symbols,
    "c": _extract_c_cpp_symbols,
    "cpp": _extract_c_cpp_symbols,
    "java": _extract_class_lang_symbols,
    "csharp": _extract_class_lang_symbols,
    "css": _extract_css_symbols,
    "scss": _extract_css_symbols,
    "html": _extract_html_symbols,
}


def extract_symbols(file_path: Union[str, Path], source_text: str) -> list[SymbolMatch]:
    """Extract top-level and nested declaration spans from a single source file."""
    path_str = str(file_path)
    ext = Path(path_str).suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(ext)
    if not language:
        return []

    norm_text = source_text.replace("\r\n", "\n").replace("\r", "")
    if language == "python":
        syms = _extract_python_ast_symbols(norm_text)
        if syms:
            return syms

    extractor = _TS_LANGUAGE_EXTRACTORS.get(language)
    source_bytes = norm_text.encode("utf-8")
    if extractor is not None and len(source_bytes) <= _MAX_BYTES:
        parser = _get_ts_parser(language)
        if parser is not None:
            tree = parser.parse(source_bytes)
            return extractor(source_bytes, tree.root_node)

    return _extract_fallback_symbols(norm_text, language)


def resolve_symbol(
    file_path: Union[str, Path],
    source_text: str,
    symbol_name: str,
    hint_start: Optional[int] = None,
    hint_end: Optional[int] = None,
) -> SymbolMatch:
    """Resolve a symbol name inside a single file to its 1-indexed line span."""
    if not symbol_name or not symbol_name.strip():
        raise ValueError("Error: symbol_name cannot be empty.")

    candidates = extract_symbols(file_path, source_text)
    norm_query = _normalize_qualified(symbol_name)
    is_qualified = "." in norm_query

    if is_qualified:
        matches = [
            c for c in candidates
            if _normalize_qualified(c.qualified_name) == norm_query
            or _normalize_qualified(c.qualified_name).endswith(f".{norm_query}")
        ]
    else:
        matches = [c for c in candidates if c.name == norm_query or c.qualified_name == norm_query]

    if matches and (hint_start is not None or hint_end is not None):
        h_start = hint_start if hint_start is not None else 1
        h_end = hint_end if hint_end is not None else 10**9
        hinted = [c for c in matches if not (c.end_line < h_start or c.start_line > h_end)]
        if hinted:
            matches = hinted

    # Prefer concrete implementations over overload stubs or bodiless declarations.
    if len(matches) > 1:
        concrete = [c for c in matches if c.has_body and not c.is_overload]
        if concrete:
            matches = concrete

    # Deduplicate identical spans before ambiguity checks.
    seen: set[tuple[str, int, int]] = set()
    deduped: list[SymbolMatch] = []
    for m in matches:
        key = (m.qualified_name, m.start_line, m.end_line)
        if key not in seen:
            seen.add(key)
            deduped.append(m)
    matches = deduped

    if len(matches) > 1 and not is_qualified:
        min_depth = min(m.nesting_depth for m in matches)
        shallow = [m for m in matches if m.nesting_depth == min_depth]
        if len(shallow) < len(matches):
            matches = shallow

    if (
        len(matches) > 1
        and len({m.qualified_name for m in matches}) == 1
        and {m.kind for m in matches} <= {"getter", "setter"}
    ):
        getters = [m for m in matches if m.kind == "getter"]
        if getters:
            matches = getters

    if not matches:
        available = list(dict.fromkeys(c.qualified_name for c in candidates))
        suffix = f" Available symbols: {', '.join(available[:15])}" if available else ""
        raise ValueError(f"Symbol '{symbol_name}' not found in '{file_path}'.{suffix}")

    if len(matches) > 1:
        details = ", ".join(f"{m.qualified_name} (L{m.start_line}-{m.end_line})" for m in matches[:8])
        raise ValueError(
            f"Multiple symbols match '{symbol_name}' in '{file_path}': {details}. "
            "Pass a qualified name or start_line/end_line."
        )

    return matches[0]
