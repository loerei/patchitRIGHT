from pathlib import Path
from typing import Dict, Type
from .base import BaseValidator
from .python import PythonValidator
from .javascript import JsTsValidator
from .config_files import JsonValidator, TomlValidator, YamlValidator

class ValidationService:
    """Facade service to manage and coordinate validation and linting across languages."""

    def __init__(self):
        self._registry: Dict[str, Type[BaseValidator]] = {
            ".py": PythonValidator,
            ".js": JsTsValidator,
            ".ts": JsTsValidator,
            ".jsx": JsTsValidator,
            ".tsx": JsTsValidator,
            ".json": JsonValidator,
            ".toml": TomlValidator,
            ".yaml": YamlValidator,
            ".yml": YamlValidator,
        }

    def _get_validator(self, filename: str) -> BaseValidator | None:
        ext = Path(filename).suffix.lower()
        validator_class = self._registry.get(ext)
        if validator_class:
            return validator_class()
        return None

    def validate_file(self, filename: str, content: str, original_content: str = "") -> None:
        """Validates file syntax and raises SyntaxValidationError if invalid."""
        validator = self._get_validator(filename)
        if validator:
            validator.validate(content, filename, original_content)

    def lint_file(self, filename: str, content: str) -> list[str]:
        """Runs the corresponding linter and returns standardized warning strings."""
        validator = self._get_validator(filename)
        if validator:
            raw_warnings = validator.lint(content, filename)
            # Standardize and format warnings to clean out noise
            clean_warnings = []
            for w in raw_warnings:
                w = w.strip()
                if w:
                    # Prefix warning with language indicator if needed, e.g. [Python]
                    clean_warnings.append(w)
            return clean_warnings
        return []
