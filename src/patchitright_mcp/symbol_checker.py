import os
import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".py"}
MAX_FILE_LINES = 5000
MAX_FILE_BYTES = 500 * 1024

DEFAULT_TRANSIENT_SYMBOLS = {
    "err", "val", "key", "id", "req", "res", "data", "item", "config", "buf", "str", "self", "cls"
}

def is_transient_symbol(symbol: str) -> bool:
    """
    Dynamic transient symbol checker.
    1. Any 1-character identifier (len <= 1) is automatically transient (e.g. i, j, k, x, y, t, e).
    2. Respects PATCHITRIGHT_TRANSIENT_SYMBOLS environment variable (comma-separated list).
    3. Checks DEFAULT_TRANSIENT_SYMBOLS.
    """
    if not symbol or len(symbol) <= 1:
        return True
    
    custom_env = os.environ.get("PATCHITRIGHT_TRANSIENT_SYMBOLS", "")
    if custom_env:
        extra_set = {s.strip() for s in custom_env.split(",") if s.strip()}
        if symbol in extra_set:
            return True
            
    return symbol in DEFAULT_TRANSIENT_SYMBOLS

def normalize_lf(content: str) -> str:
    """Enforces LF line endings."""
    return content.replace("\r\n", "\n")

def is_supported_file(filename: str) -> bool:
    if not filename:
        return False
    ext = Path(filename).suffix.lower()
    return ext in SUPPORTED_EXTENSIONS

def mask_comments_and_strings(content: str, filename: str) -> str:
    """
    Space-preserving masking of string literals and comments while preserving all \\n linebreaks.
    Unmasks JS template literal ${expr} and Python f-string {expr} expressions.
    """
    ext = Path(filename).suffix.lower() if filename else ""
    is_python = ext == ".py"
    norm = normalize_lf(content)
    n = len(norm)
    chars = list(norm)

    i = 0
    while i < n:
        ch = chars[i]

        # Handle Python comments (#)
        if is_python and ch == '#':
            while i < n and chars[i] != '\n':
                chars[i] = ' '
                i += 1
            continue

        # Handle JS/TS comments (// and /* */)
        if not is_python and ch == '/':
            if i + 1 < n and chars[i + 1] == '/':
                while i < n and chars[i] != '\n':
                    chars[i] = ' '
                    i += 1
                continue
            elif i + 1 < n and chars[i + 1] == '*':
                chars[i] = ' '
                chars[i + 1] = ' '
                i += 2
                while i < n:
                    if chars[i] == '*' and i + 1 < n and chars[i + 1] == '/':
                        chars[i] = ' '
                        chars[i + 1] = ' '
                        i += 2
                        break
                    if chars[i] != '\n':
                        chars[i] = ' '
                    i += 1
                continue

        # Handle Python docstrings (triple quotes)
        if is_python and (norm.startswith('"""', i) or norm.startswith("'''", i)):
            q = norm[i:i+3]
            chars[i] = chars[i+1] = chars[i+2] = ' '
            i += 3
            while i < n:
                if norm.startswith(q, i):
                    chars[i] = chars[i+1] = chars[i+2] = ' '
                    i += 3
                    break
                if chars[i] != '\n':
                    chars[i] = ' '
                i += 1
            continue

        # Handle JS Template Literals (`...`) with brace-stack ${expr} unmasking
        if not is_python and ch == '`':
            chars[i] = ' '
            i += 1
            while i < n:
                if chars[i] == '`' and (i == 0 or chars[i-1] != '\\'):
                    chars[i] = ' '
                    i += 1
                    break
                if chars[i] == '$' and i + 1 < n and chars[i+1] == '{':
                    # Interpolation start - keep intact until matching }
                    i += 2
                    brace_count = 1
                    while i < n and brace_count > 0:
                        if chars[i] == '{':
                            brace_count += 1
                        elif chars[i] == '}':
                            brace_count -= 1
                        i += 1
                    continue
                if chars[i] != '\n':
                    chars[i] = ' '
                i += 1
            continue

        # Handle Python f-strings (f"..." or f'...') with brace-stack {expr} unmasking
        if is_python and (ch in ('f', 'F')) and i + 1 < n and chars[i+1] in ('"', "'"):
            quote = chars[i+1]
            chars[i] = chars[i+1] = ' '
            i += 2
            while i < n:
                if chars[i] == quote and (i == 0 or chars[i-1] != '\\'):
                    chars[i] = ' '
                    i += 1
                    break
                if chars[i] == '{' and (i == 0 or chars[i-1] != '{'):
                    # Interpolation start - keep intact until matching }
                    i += 1
                    brace_count = 1
                    while i < n and brace_count > 0:
                        if chars[i] == '{':
                            brace_count += 1
                        elif chars[i] == '}':
                            brace_count -= 1
                        i += 1
                    continue
                if chars[i] != '\n':
                    chars[i] = ' '
                i += 1
            continue

        # Handle standard single and double quoted strings
        if ch in ('"', "'"):
            quote = ch
            chars[i] = ' '
            i += 1
            while i < n:
                if chars[i] == quote and (i == 0 or chars[i-1] != '\\'):
                    chars[i] = ' '
                    i += 1
                    break
                if chars[i] != '\n':
                    chars[i] = ' '
                i += 1
            continue

        i += 1

    return "".join(chars)

def extract_declarations(code_slice: str, filename: str) -> set[str]:
    """
    Extracts declared identifiers from code_slice based on language semantics.
    """
    if not code_slice:
        return set()

    ext = Path(filename).suffix.lower() if filename else ""
    is_python = ext == ".py"
    decls = set()
    norm = normalize_lf(code_slice)

    if is_python:
        # def func_name(...)
        for m in re.finditer(r'\bdef\s+([a-zA-Z_]\w*)', norm):
            decls.add(m.group(1))
        # class ClassName(...)
        for m in re.finditer(r'\bclass\s+([a-zA-Z_]\w*)', norm):
            decls.add(m.group(1))
        # function parameters
        for m in re.finditer(r'\bdef\s+[a-zA-Z_]\w*\s*\(([^)]*)\)', norm):
            params_str = m.group(1)
            for p in re.finditer(r'\b([a-zA-Z_]\w*)\b', params_str):
                pname = p.group(1)
                if pname not in ("self", "cls"):
                    decls.add(pname)
        # local variable assignments (var = val or a, b = val) - exclude self.attr
        for m in re.finditer(r'(?<!\.)\b([a-zA-Z_]\w*)\s*(?:=\s*|,\s*([a-zA-Z_]\w*))', norm):
            for group in m.groups():
                if group and group not in ("self", "cls"):
                    decls.add(group)
    else:
        # JS/TS declarations: const/let/var/function/class/interface/type
        for m in re.finditer(r'\b(?:const|let|var|function|class|interface|type)\s+([a-zA-Z_$][\w$]*)', norm):
            decls.add(m.group(1))

        # Function parameters: function foo(a, b), constructor(a, b), or (a, b) =>
        for m in re.finditer(r'\b(?:function(?:\s+[\w$]+)?|constructor)\s*\(([^)]*)\)|\(([^)]*)\)\s*=>', norm):
            params_str = m.group(1) or m.group(2)
            if params_str:
                for p in re.finditer(r'\b([a-zA-Z_$][\w$]*)\b', params_str):
                    pname = p.group(1)
                    if pname not in ("number", "string", "boolean", "any", "void", "object", "const", "let", "var"):
                        decls.add(pname)

        # Destructuring with aliases: const { a, b: alias = defaultVal } = obj
        for m in re.finditer(r'\{\s*([^}]+)\s*\}\s*=', norm):
            block = m.group(1)
            # Extract key: alias
            for item in block.split(','):
                item = item.strip()
                if ':' in item:
                    alias_part = item.split(':')[1].split('=')[0].strip()
                    if alias_part and re.match(r'^[a-zA-Z_$][a-zA-Z0-9_$]*$', alias_part):
                        decls.add(alias_part)
                else:
                    var_part = item.split('=')[0].strip()
                    if var_part and re.match(r'^[a-zA-Z_$][a-zA-Z0-9_$]*$', var_part):
                        decls.add(var_part)

        # Imports: import { x as alias } or import alias from 'mod'
        for m in re.finditer(r'\bimport\s+(?:\{([^}]+)\}|([a-zA-Z_$][a-zA-Z0-9_$]*))\s+from', norm):
            named, default_imp = m.groups()
            if default_imp:
                decls.add(default_imp)
            if named:
                for item in named.split(','):
                    item = item.strip()
                    if ' as ' in item:
                        alias = item.split(' as ')[1].strip()
                        if alias:
                            decls.add(alias)
                    else:
                        var_name = item.strip()
                        if var_name:
                            decls.add(var_name)

    # Filter out transient short symbols
    return {d for d in decls if not is_transient_symbol(d)}

def extract_net_diff_declarations(patch_content: str, filename: str) -> set[str]:
    """Extracts net deleted symbol declarations across unified diff hunks."""
    if not patch_content:
        return set()

    minus_lines = []
    plus_lines = []

    for line in patch_content.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            minus_lines.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            plus_lines.append(line[1:])

    deleted_decls = extract_declarations("\n".join(minus_lines), filename)
    readded_decls = extract_declarations("\n".join(plus_lines), filename)
    return deleted_decls - readded_decls

def _find_symbol_reference_lines(symbol: str, patched_lines: list[str]) -> list[int]:
    """Finds line numbers where symbol is referenced outside properties or interface keys."""
    pattern = re.compile(r'\b' + re.escape(symbol) + r'\b')
    ref_lines = []
    for line_idx, line in enumerate(patched_lines, start=1):
        for match in pattern.finditer(line):
            start_col = match.start()
            prefix = line[:start_col].rstrip()
            if prefix.endswith((".", "?.")):
                continue
            suffix = line[match.end():].lstrip()
            if suffix.startswith(("?", "?:")):
                continue
            if suffix.startswith(":") and not suffix.startswith("::"):
                if prefix.endswith(("{", ",", "interface")) or "interface " in prefix:
                    continue
            ref_lines.append(line_idx)
    return sorted(set(ref_lines))

def detect_omitted_symbols(
    file_content: str,
    match_start: int,
    match_end: int,
    original_slice: str,
    replace_content: str,
    filename: str
) -> list[str]:
    """
    Detects if symbols declared in original_slice are removed from replace_content
    yet still referenced elsewhere in the file.
    """
    if not is_supported_file(filename):
        return []

    norm_file = normalize_lf(file_content)
    lines = norm_file.splitlines()
    if len(lines) > MAX_FILE_LINES or len(norm_file.encode('utf-8')) > MAX_FILE_BYTES:
        return []

    declared_symbols = extract_declarations(original_slice, filename)
    if not declared_symbols:
        return []

    retained_symbols = extract_declarations(replace_content, filename)
    omitted_symbols = declared_symbols - retained_symbols
    if not omitted_symbols:
        return []

    patched_content = norm_file[:match_start] + replace_content + norm_file[match_end:]
    patched_masked = mask_comments_and_strings(patched_content, filename)
    patched_lines = patched_masked.splitlines()

    warnings = []
    for symbol in sorted(omitted_symbols):
        ref_lines = _find_symbol_reference_lines(symbol, patched_lines)
        if ref_lines:
            line_str = ", ".join(map(str, ref_lines))
            warnings.append(
                f"Symbol Omission Alert: '{symbol}' was declared in original slice and referenced on lines {line_str}, but is missing from replace_content."
            )
    return warnings

def detect_net_omitted_symbols(
    patched_content: str,
    deleted_symbols: set[str],
    filename: str
) -> list[str]:
    """
    Evaluates net symbol omissions across batch replacements on patched_content.
    """
    if not is_supported_file(filename) or not deleted_symbols:
        return []

    norm_patched = normalize_lf(patched_content)
    retained_symbols = extract_declarations(norm_patched, filename)
    omitted_symbols = deleted_symbols - retained_symbols
    if not omitted_symbols:
        return []

    patched_masked = mask_comments_and_strings(norm_patched, filename)
    patched_lines = patched_masked.splitlines()

    warnings = []
    for symbol in sorted(omitted_symbols):
        ref_lines = _find_symbol_reference_lines(symbol, patched_lines)
        if ref_lines:
            line_str = ", ".join(map(str, ref_lines))
            warnings.append(
                f"Symbol Omission Alert: '{symbol}' was declared in original slice and referenced on lines {line_str}, but is missing from replace_content."
            )
    return warnings
