import json
import sys
from .base import BaseValidator
from .errors import SyntaxValidationError

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


class TomlValidator(BaseValidator):
    """Validator adapter for TOML files using tomllib (Python 3.11+) or tomli fallback."""

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        if not content.strip():
            return

        # Python 3.11+ has built-in tomllib
        if sys.version_info >= (3, 11):
            import tomllib
            if original_content.strip():
                try:
                    tomllib.loads(original_content)
                except tomllib.TOMLDecodeError:
                    return
            try:
                tomllib.loads(content)
            except tomllib.TOMLDecodeError as e:
                raise SyntaxValidationError(
                    message=f"TOML Syntax Error: {e}",
                    filename=filename
                )
        else:
            try:
                import tomli
                if original_content.strip():
                    try:
                        tomli.loads(original_content)
                    except Exception:
                        return
                tomli.loads(content)
            except ImportError:
                # If tomli is not installed yet, skip validation to degrade gracefully
                pass
            except Exception as e:
                raise SyntaxValidationError(
                    message=f"TOML Syntax Error: {e}",
                    filename=filename
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
            raise SyntaxValidationError(
                message=f"YAML Syntax Error: {e}",
                filename=filename
            )
