import shutil
import subprocess
from pathlib import Path
from .base import BaseValidator
from .errors import SyntaxValidationError

class JsTsValidator(BaseValidator):
    """Validator adapter for JavaScript and TypeScript using Biome or Node.js --check fallback."""

    def _get_biome_command(self) -> tuple[str, list[str]] | None:
        # Check standard PATH
        exe = shutil.which("biome")
        if exe:
            return exe, ["check"]
        
        # Check local node_modules
        local_paths = [
            Path.cwd() / "node_modules" / ".bin" / "biome",
            Path.cwd() / "node_modules" / ".bin" / "biome.cmd"
        ]
        for p in local_paths:
            if p.exists():
                return str(p), ["check"]

        # Only check package runners if we are in a package.json directory
        if (Path.cwd() / "package.json").exists():
            npx = shutil.which("npx")
            if npx:
                return npx, ["@biomejs/biome", "check"]
            yarn = shutil.which("yarn")
            if yarn:
                return yarn, ["dlx", "@biomejs/biome", "check"]
            pnpm = shutil.which("pnpm")
            if pnpm:
                return pnpm, ["dlx", "@biomejs/biome", "check"]

        return None

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        biome_cmd = self._get_biome_command()
        if biome_cmd:
            biome_exe, base_args = biome_cmd
            args = base_args + [f"--stdin-file-path={filename}"]
            try:
                # First check if original content was valid
                orig_process = subprocess.run(
                    [biome_exe] + args,
                    input=original_content,
                    text=True,
                    capture_output=True,
                    check=False
                )
                if orig_process.returncode != 0:
                    orig_output = orig_process.stderr or orig_process.stdout
                    if "parse" in orig_output.lower() or "syntax" in orig_output.lower() or "error[" in orig_output.lower():
                        # Original content is invalid, skip validation
                        return

                process = subprocess.run(
                    [biome_exe] + args,
                    input=content,
                    text=True,
                    capture_output=True,
                    check=False
                )
                # Biome formats syntax errors in stderr/stdout with "parse" or "error"
                if process.returncode != 0:
                    output = process.stderr or process.stdout
                    if "parse" in output.lower() or "syntax" in output.lower() or "error[" in output.lower():
                        err_line = output.splitlines()[0] if output.splitlines() else "Unknown parse error"
                        raise SyntaxValidationError(
                            message=f"Biome Syntax Error: {err_line}",
                            filename=filename
                        )
            except SyntaxValidationError:
                raise
            except Exception:
                pass
            return

        # Fallback to node --check if node is available (JS/CJS/MJS only)
        if filename.endswith((".js", ".jsx", ".cjs", ".mjs")):
            node_exe = shutil.which("node")
            if node_exe:
                try:
                    # Check if original was valid
                    orig_process = subprocess.run(
                        [node_exe, "--check"],
                        input=original_content,
                        text=True,
                        capture_output=True,
                        check=False
                    )
                    if orig_process.returncode != 0:
                        return

                    process = subprocess.run(
                        [node_exe, "--check"],
                        input=content,
                        text=True,
                        capture_output=True,
                        check=False
                    )
                    if process.returncode != 0:
                        err_msg = process.stderr.strip() if process.stderr else "Syntax Error"
                        raise SyntaxValidationError(
                            message=f"Node JS Syntax Error: {err_msg}",
                            filename=filename
                        )
                except SyntaxValidationError:
                    raise
                except Exception:
                    pass

    def lint(self, content: str, filename: str) -> list[str]:
        biome_cmd = self._get_biome_command()
        if not biome_cmd:
            return []
        
        biome_exe, base_args = biome_cmd
        args = base_args + [f"--stdin-file-path={filename}"]

        try:
            process = subprocess.run(
                [biome_exe] + args,
                input=content,
                text=True,
                capture_output=True,
                check=False
            )
            warnings = []
            output = process.stdout or process.stderr
            if output:
                for line in output.splitlines():
                    line = line.strip()
                    if line and ("warning" in line.lower() or "error" in line.lower() or line.startswith("  ")):
                        warnings.append(line)
            return warnings
        except Exception:
            return []
