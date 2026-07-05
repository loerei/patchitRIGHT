import ast
import shutil
import subprocess
import sys
from pathlib import Path
from .base import BaseValidator
from .errors import SyntaxValidationError

class PythonValidator(BaseValidator):
    """Validator adapter for Python using built-in ast module and local Ruff CLI."""

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        # Only validate syntax if the original file was valid Python code
        try:
            ast.parse(original_content)
        except SyntaxError:
            return

        try:
            ast.parse(content)
        except SyntaxError as e:
            raise SyntaxValidationError(
                message=str(e),
                filename=filename,
                line=e.lineno,
                column=e.offset
            )

    def lint(self, content: str, filename: str) -> list[str]:
        executable_dir = Path(sys.executable).parent
        ruff_exe = shutil.which("ruff", path=str(executable_dir)) or shutil.which("ruff")
        if not ruff_exe:
            return []

        try:
            process = subprocess.run(
                [ruff_exe, "check", "-", "--no-cache"],
                input=content,
                text=True,
                capture_output=True,
                check=False,
                timeout=10
            )
            warnings = []
            if process.stdout:
                for line in process.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith("Found ") and not line.startswith("[*] "):
                        if line.startswith("-:"):
                            line = line[2:]
                        warnings.append(line)
            return warnings
        except Exception:
            return []
