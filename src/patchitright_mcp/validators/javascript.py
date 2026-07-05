import os
import shutil
import subprocess
from pathlib import Path
from .base import BaseValidator
from .errors import SyntaxValidationError
from ..utils_log import log_step

def _clean_biome_output(text: str) -> str:
    replacements = {
        "│": "|",
        "┌": "+",
        "─": "-",
        "▲": "^",
        "×": "x",
        "␍": "",
        "━━": "--",
        "━": "-",
        "\xa0": " ",      # Replace non-breaking space with normal space
        "\u200b": "",      # Replace zero-width space with empty
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Ignore any other unicode characters instead of turning them into '?'
    return text.encode("ascii", errors="ignore").decode("ascii")

class JsTsValidator(BaseValidator):
    """Validator adapter for JavaScript and TypeScript using Biome or Node.js --check fallback."""

    BIOME_PACKAGE = "@biomejs/biome"

    def _get_biome_command(self) -> tuple[str, list[str]] | None:
        log_step("JsTsValidator: Checking for biome...")
        # Check standard PATH
        exe = shutil.which("biome")
        if exe:
            log_step(f"JsTsValidator: Found biome globally: {exe}")
            return exe, ["check"]
        
        # Check local node_modules
        local_paths = [
            Path.cwd() / "node_modules" / ".bin" / "biome",
            Path.cwd() / "node_modules" / ".bin" / "biome.cmd"
        ]
        for p in local_paths:
            if p.exists():
                log_step(f"JsTsValidator: Found biome locally: {p}")
                return str(p), ["check"]

        # Check package runners globally/locally without requiring package.json
        npx = shutil.which("npx")
        if npx:
            log_step(f"JsTsValidator: Falling back to npx: {npx}")
            return npx, ["--offline", self.BIOME_PACKAGE, "check"]
        yarn = shutil.which("yarn")
        if yarn:
            log_step(f"JsTsValidator: Falling back to yarn dlx: {yarn}")
            return yarn, ["dlx", self.BIOME_PACKAGE, "check"]
        pnpm = shutil.which("pnpm")
        if pnpm:
            log_step(f"JsTsValidator: Falling back to pnpm dlx: {pnpm}")
            return pnpm, ["dlx", self.BIOME_PACKAGE, "check"]

        log_step("JsTsValidator: No biome runner found")
        return None

    def _check_original_validity(self, biome_exe: str, base_args: list[str], temp_path: Path, original_content: str) -> bool:
        """Return True if original content has syntax errors, indicating we should skip validation."""
        log_step("JsTsValidator.validate: writing original content to temp path...")
        temp_path.write_text(original_content, encoding="utf-8")
        log_step(f"JsTsValidator.validate: running orig check with {biome_exe}...")
        try:
            orig_process = subprocess.run(
                [biome_exe] + base_args + [str(temp_path)],
                text=True,
                capture_output=True,
                check=False,
                shell=(os.name == 'nt'),
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                timeout=10
            )
            log_step(f"JsTsValidator.validate: orig check done. Code={orig_process.returncode}")
            if orig_process.returncode != 0:
                orig_output = orig_process.stderr or orig_process.stdout
                if "error[" in orig_output.lower() and ("pars" in orig_output.lower() or "syntax" in orig_output.lower()):
                    log_step("JsTsValidator.validate: original content has syntax error, skipping validation")
                    return True
        except OSError as e:
            log_step(f"JsTsValidator.validate: biome original check failed: {e}")
        return False

    def _parse_biome_error(self, output: str, temp_name: str, filename: str) -> SyntaxValidationError | None:
        """Parse Biome output for syntax error diagnostics and return SyntaxValidationError if found."""
        if "pars" in output.lower() or "syntax" in output.lower() or "error[" in output.lower():
            # Filter out empty lines and box-drawing header lines (containing ━━)
            clean_lines = [
                l.strip() for l in output.splitlines()
                if l.strip() and "━━" not in l
            ]
            err_line = clean_lines[0] if clean_lines else "Unknown parse error"
            err_line = _clean_biome_output(err_line)
            import re
            line, column = None, None
            match = re.search(rf"{re.escape(temp_name)}:(\d+):(\d+)", output)
            if match:
                line = int(match.group(1))
                column = int(match.group(2))
            return SyntaxValidationError(
                message=f"Biome Syntax Error: {err_line}",
                filename=filename,
                line=line,
                column=column
            )
        return None

    def _validate_with_biome(self, biome_cmd: tuple[str, list[str]], content: str, filename: str, original_content: str) -> None:
        biome_exe, base_args = biome_cmd
        # Create a temporary file next to the target file to get full diagnostics (Biome stdin does not output line/col)
        # Keep the original extension so that Biome/linter processes it correctly
        suffix = f".patchitright_temp{Path(filename).suffix}"
        temp_path = Path(filename).with_suffix(suffix)
        log_step(f"JsTsValidator.validate: using temp path {temp_path}")
        try:
            if self._check_original_validity(biome_exe, base_args, temp_path, original_content):
                return

            # Check new
            log_step("JsTsValidator.validate: writing new content to temp path...")
            temp_path.write_text(content, encoding="utf-8")
            log_step(f"JsTsValidator.validate: running new check with {biome_exe}...")
            process = subprocess.run(
                [biome_exe] + base_args + [str(temp_path)],
                text=True,
                capture_output=True,
                check=False,
                shell=(os.name == 'nt'),
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                timeout=10
            )
            log_step(f"JsTsValidator.validate: new check done. Code={process.returncode}")
            # Biome formats syntax errors in stderr/stdout with "pars" or "error"
            if process.returncode != 0:
                output = process.stderr or process.stdout
                err = self._parse_biome_error(output, temp_path.name, filename)
                if err:
                    log_step(f"JsTsValidator.validate: raising SyntaxValidationError for line={err.line} col={err.column}")
                    raise err
        except SyntaxValidationError:
            raise
        except OSError as e:
            log_step(f"JsTsValidator.validate: biome execution failed: {e}")
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                    log_step("JsTsValidator.validate: deleted temp path")
                except Exception as e:
                    log_step(f"JsTsValidator.validate: failed to delete temp path: {e}")
        log_step("JsTsValidator.validate: Biome check finished successfully")

    def _validate_with_node(self, content: str, filename: str, original_content: str) -> None:
        # Fallback to node --check if node is available (JS/CJS/MJS only)
        if not filename.endswith((".js", ".jsx", ".cjs", ".mjs")):
            return
        node_exe = shutil.which("node")
        log_step(f"JsTsValidator.validate: fallback node check path: {node_exe}")
        if not node_exe:
            return
        try:
            # Check if original was valid
            orig_process = subprocess.run(
                [node_exe, "--check"],
                input=original_content,
                text=True,
                capture_output=True,
                check=False,
                timeout=10
            )
            if orig_process.returncode != 0:
                log_step("JsTsValidator.validate: original failed node check, skipping")
                return

            process = subprocess.run(
                [node_exe, "--check"],
                input=content,
                text=True,
                capture_output=True,
                check=False,
                timeout=10
            )
            if process.returncode != 0:
                err_msg = process.stderr.strip() if process.stderr else "Syntax Error"
                # Convert to ASCII-safe representation to prevent Windows stdout encoding crashes
                err_msg = err_msg.encode("ascii", errors="replace").decode("ascii")
                import re
                line, column = None, None
                match = re.search(r"\[stdin\]:(\d+)(?::(\d+))?", err_msg)
                if match:
                    line = int(match.group(1))
                    if match.group(2):
                        column = int(match.group(2))
                log_step(f"JsTsValidator.validate: raising node SyntaxValidationError for line={line}")
                raise SyntaxValidationError(
                    message=f"Node JS Syntax Error: {err_msg}",
                    filename=filename,
                    line=line,
                    column=column
                )
        except SyntaxValidationError:
            raise
        except OSError as e:
            log_step(f"JsTsValidator.validate: node execution failed: {e}")

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        log_step(f"JsTsValidator.validate: starting for {filename}...")
        biome_cmd = self._get_biome_command()
        if biome_cmd:
            self._validate_with_biome(biome_cmd, content, filename, original_content)
        else:
            self._validate_with_node(content, filename, original_content)

    def lint(self, content: str, filename: str) -> list[str]:
        log_step(f"JsTsValidator.lint: starting for {filename}...")
        biome_cmd = self._get_biome_command()
        if not biome_cmd:
            log_step("JsTsValidator.lint: biome not found, skipping lint")
            return []
        
        biome_exe, base_args = biome_cmd
        suffix = f".patchitright_temp{Path(filename).suffix}"
        temp_path = Path(filename).with_suffix(suffix)
        log_step(f"JsTsValidator.lint: using temp path {temp_path}")
        try:
            temp_path.write_text(content, encoding="utf-8")
            log_step(f"JsTsValidator.lint: running check with {biome_exe}...")
            process = subprocess.run(
                [biome_exe] + base_args + [str(temp_path)],
                text=True,
                capture_output=True,
                check=False,
                shell=(os.name == 'nt'),
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                timeout=10
            )
            log_step(f"JsTsValidator.lint: check completed with code={process.returncode}")
            if process.returncode != 0:
                output = process.stderr or process.stdout
                warnings = []
                for line in output.splitlines():
                    # Preserve indentation spaces (which are important for alignment)
                    # but strip trailing spaces
                    line = line.rstrip()
                    if not line.strip() or "━━" in line or "Found" in line or "Checked" in line or "emitted" in line:
                        continue
                    # Clean up the temp filename from warnings
                    line = line.replace(temp_path.name, Path(filename).name)
                    # Convert to ASCII-safe representation (preserving normal spaces)
                    line = _clean_biome_output(line)
                    warnings.append(line)
                log_step(f"JsTsValidator.lint: returning {len(warnings)} warnings")
                return warnings
        except OSError as e:
            log_step(f"JsTsValidator.lint: biome execution failed: {e}")
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                    log_step("JsTsValidator.lint: deleted temp path")
                except Exception as e:
                    log_step(f"JsTsValidator.lint: failed to delete temp path: {e}")
        return []
