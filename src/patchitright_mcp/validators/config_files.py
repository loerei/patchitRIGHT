import json
from .base import BaseValidator
from .errors import SyntaxValidationError

try:
    import tomllib  # type: ignore
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

class JsonValidator(BaseValidator):
    """Validator adapter for JSON files using Python's built-in json module."""

    def _strip_comments(self, content: str) -> str:
        import re
        # Remove single-line comments // ...
        content = re.sub(r'//.*', '', content)
        # Remove multi-line comments /* ... */
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        return content

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        if not content.strip():
            return
        
        orig_clean = self._strip_comments(original_content)
        content_clean = self._strip_comments(content)
        
        # Check if original content was valid JSON
        if orig_clean.strip():
            try:
                json.loads(orig_clean)
            except json.JSONDecodeError:
                # Original content was already invalid, skip syntax validation
                return

        try:
            json.loads(content_clean)
        except json.JSONDecodeError as e:
            raise SyntaxValidationError(
                message=f"JSON Syntax Error: {e.msg}",
                filename=filename,
                line=e.lineno,
                column=e.colno
            )

    def lint(self, content: str, filename: str) -> list[str]:
        # Reuse Biome command runner from JsTsValidator to lint JSON/JSONC
        from .javascript import JsTsValidator
        js_val = JsTsValidator()
        return js_val.lint(content, filename)


class TomlValidator(BaseValidator):
    """Validator adapter for TOML files using tomllib (Python 3.11+) or tomli fallback."""

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        if not content.strip():
            return
        if tomllib is None:
            return

        if original_content.strip():
            try:
                tomllib.loads(original_content)
            except Exception:
                # Original content was already invalid, skip syntax validation
                return

        try:
            tomllib.loads(content)
        except Exception as e:
            line = None
            column = None
            err_msg = str(e)
            import re
            match = re.search(r"line\s+(\d+),\s+column\s+(\d+)", err_msg)
            if match:
                line = int(match.group(1))
                column = int(match.group(2))
            raise SyntaxValidationError(
                message=f"TOML Syntax Error: {err_msg}",
                filename=filename,
                line=line,
                column=column
            )


class YamlValidator(BaseValidator):
    """Validator adapter for YAML files using PyYAML (yaml)."""

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        if not content.strip():
            return
        try:
            import yaml
            if original_content.strip():
                try:
                    yaml.safe_load(original_content)
                except Exception:
                    return
            yaml.safe_load(content)
        except ImportError:
            # Degrade gracefully if PyYAML is not installed
            pass
        except Exception as e:
            line = None
            column = None
            if hasattr(e, "problem_mark") and e.problem_mark is not None:
                line = e.problem_mark.line + 1
                column = e.problem_mark.column + 1
            raise SyntaxValidationError(
                message=f"YAML Syntax Error: {e}",
                filename=filename,
                line=line,
                column=column
            )
