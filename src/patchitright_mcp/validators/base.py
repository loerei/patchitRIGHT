class BaseValidator:
    """Interface defining the validation seam for code and config files."""

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        """Parses the content and raises SyntaxValidationError if syntax errors are found.

        Args:
            content: The patched content to validate.
            filename: The name/path of the target file.
            original_content: The original content of the file before patching.
        """
        pass

    def lint(self, content: str, filename: str, ignore_format: bool = False, ignore_codesmell: bool = False) -> list[str]:
        """Runs linting/code-smell checks on the content.

        Args:
            content: The patched content to lint.
            filename: The name/path of the target file.
            ignore_format: Whether formatting checks should be skipped.
            ignore_codesmell: Whether code smell/linter checks should be skipped.

        Returns:
            A list of linter warnings (already parsed and standardized).
        """
        _ = (filename, ignore_format, ignore_codesmell)
        return []
