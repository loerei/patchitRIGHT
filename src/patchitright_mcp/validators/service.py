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
    def parse_ignored_categories(env_val: str | None = None) -> set[str]:
        """Parses and normalizes warning category flags from environment variable or argument."""
        if env_val is None:
            env_val = os.environ.get("PATCHITRIGHT_IGNORE_WARNINGS", "")
        if not env_val:
            return set()

        tokens = [t.strip().lower() for t in env_val.split(",") if t.strip()]
        if any(t in ("all", "*", "true", "1") for t in tokens):
            return {"all", "symbol", "insertion", "lint", "format"}

        categories = set()
        for t in tokens:
            if t in ("symbol", "symbols", "symbol_omission", "symbol_omissions", "omission"):
                categories.add("symbol")
            elif t in ("insertion", "insertions", "line", "lines", "indent", "indentation"):
                categories.add("insertion")
            elif t in ("lint", "linter", "codesmell", "codesmells"):
                categories.add("lint")
            elif t in ("format", "formatting", "formatter"):
                categories.add("format")
        return categories

    @staticmethod
    def filter_warnings(warnings: list[str], ignored_categories: set[str] | None = None) -> list[str]:
        """Filters warnings according to PATCHITRIGHT_IGNORE_WARNINGS environment variable or explicit categories."""
        if not warnings:
            return []

        if ignored_categories is None:
            ignored_categories = ValidationService.parse_ignored_categories()

        if not ignored_categories:
            return warnings

        if "all" in ignored_categories:
            return []

        import re
        diff_line_pattern = re.compile(r"^\s*\d+\s*\|")

        filtered = []
        in_formatter_block = False

        for w in warnings:
            lower_w = w.lower()
            cats: set[str] = set()

            if "symbol omission alert:" in lower_w or lower_w.startswith("symbol omission"):
                cats.add("symbol")
                in_formatter_block = False

            if (
                "exceeds total file lines" in lower_w
                or "clamped insertion to end-of-file" in lower_w
                or "could not infer reference indentation" in lower_w
                or "insert_line" in lower_w
                or "auto_indent=false" in lower_w
                or "contains tabs while auto_indent" in lower_w
                or "contains spaces while auto_indent" in lower_w
            ):
                cats.add("insertion")
                in_formatter_block = False

            is_explicit_format = (
                "formatter would have printed" in lower_w
                or "format violations" in lower_w
                or "formatting violations" in lower_w
                or "tab vs space" in lower_w
                or "indentation" in lower_w
                or "contains tabs while auto_indent" in lower_w
                or "contains spaces while auto_indent" in lower_w
                or lower_w.startswith("formatter")
                or lower_w.startswith("format ")
            )

            if is_explicit_format:
                cats.add("format")
                in_formatter_block = True
            elif in_formatter_block:
                is_diff_line = (
                    diff_line_pattern.match(w) is not None
                    or w.startswith(("|", "->", "=>"))
                    or (len(w) > 2 and w[0] in ("-", "+") and w[1] in (" ", "\t"))
                )
                if is_diff_line:
                    cats.add("format")
                else:
                    in_formatter_block = False

            if not cats:
                cats.add("lint")
                in_formatter_block = False

            if cats & ignored_categories:
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

    def lint_file(self, filename: str, content: str, ignored_categories: set[str] | None = None) -> list[str]:
        """Runs the corresponding linter and returns standardized warning strings."""
        if ignored_categories is None:
            ignored_categories = self.parse_ignored_categories()

        if "all" in ignored_categories or ("lint" in ignored_categories and "format" in ignored_categories):
            return []

        validator = self._get_validator(filename)
        if validator:
            ignore_format = "format" in ignored_categories
            ignore_codesmell = "lint" in ignored_categories
            try:
                raw_warnings = validator.lint(
                    content, filename, ignore_format=ignore_format, ignore_codesmell=ignore_codesmell
                )
            except TypeError:
                raw_warnings = validator.lint(content, filename)

            # Standardize and format warnings to clean out noise
            clean_warnings = []
            for w in raw_warnings:
                w = w.strip()
                if w:
                    # Prefix warning with language indicator if needed, e.g. [Python]
                    clean_warnings.append(w)
            return self.filter_warnings(clean_warnings, ignored_categories=ignored_categories)
        return []

