import json
import re
from .base import BaseValidator
from .errors import SyntaxValidationError

try:
    import json5  # type: ignore
except ImportError:
    json5 = None  # type: ignore

try:
    import tomllib  # type: ignore
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


class JsonValidator(BaseValidator):
    """Validator adapter for JSON and JSONC files using the spec-compliant json5 parser (or json fallback)."""

    def _parse_content(self, text: str, filename: str):
        if filename.endswith((".jsonc", ".json5")):
            if json5 is not None:
                return json5.loads(text)
            return json.loads(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            if json5 is not None:
                # Check if json5 parses comments, but enforce no trailing comma for standard .json
                parsed = json5.loads(text)
                if re.search(r',\s*[}\]]', text):
                    raise e
                return parsed
            raise e

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        if not content.strip():
            return

        # Check if original content was valid JSON/JSONC
        if original_content.strip():
            try:
                self._parse_content(original_content, filename)
            except Exception:
                # Original content was already invalid, skip syntax validation
                return

        try:
            self._parse_content(content, filename)
        except Exception as e:
            line = getattr(e, "lineno", None)
            column = getattr(e, "colno", None)
            msg = getattr(e, "msg", str(e))
            raise SyntaxValidationError(
                message=f"JSON Syntax Error: {msg}",
                filename=filename,
                line=line,
                column=column,
            )

    def lint(self, content: str, filename: str, ignore_format: bool = False, ignore_codesmell: bool = False) -> list[str]:
        # Reuse Biome command runner from JsTsValidator to lint JSON/JSONC
        from .javascript import JsTsValidator
        js_val = JsTsValidator()
        return js_val.lint(content, filename, ignore_format=ignore_format, ignore_codesmell=ignore_codesmell)


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
