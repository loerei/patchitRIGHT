from typing import Optional

class SyntaxValidationError(ValueError):
    """Exception raised when patched code or config file contains syntax errors."""

    def __init__(
        self,
        message: str,
        filename: str,
        line: Optional[int] = None,
        column: Optional[int] = None
    ):
        super().__init__(message)
        self.filename = filename
        self.line = line
        self.column = column
