import os
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
            ".mjs": JsTsValidator,
            ".cjs": JsTsValidator,
            ".ts": JsTsValidator,
            ".jsx": JsTsValidator,
            ".tsx": JsTsValidator,
            ".json": JsonValidator,
            ".toml": TomlValidator,
            ".yaml": YamlValidator,
            ".yml": YamlValidator,
        }

    @staticmethod
    def filter_warnings(warnings: list[str]) -> list[str]:
        """Filters warnings according to PATCHITRIGHT_IGNORE_WARNINGS environment variable."""
        if not warnings:
            return []

        env_val = os.environ.get("PATCHITRIGHT_IGNORE_WARNINGS", "").strip().lower()
        if not env_val:
            return warnings

        tokens = [t.strip() for t in env_val.split(",") if t.strip()]
        if any(t in ("all", "*", "true", "1") for t in tokens):
            return []

        ignore_format = any(t in ("format", "formatting") for t in tokens)
        ignore_codesmell = any(t in ("codesmell", "lint", "linter") for t in tokens)

        filtered = []
        for w in warnings:
            # Check if this warning is format-related
            is_format = (
                "Formatter would have printed" in w
                or "Formatter" in w
                or "tab vs space" in w.lower()
                or "indentation" in w.lower()
            )

            if is_format and ignore_format:
                continue

            if not is_format and ignore_codesmell:
                continue

            filtered.append(w)

        return filtered

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
            return self.filter_warnings(clean_warnings)
        return []

