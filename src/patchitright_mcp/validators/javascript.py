import os
import shutil
import subprocess
import json
import re
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

    def _parse_biome_json(self, output: str) -> tuple[dict | None, bool]:
        """Attempt to parse output as structured JSON. Returns (data, success)."""
        try:
            json_start = output.find('{')
            json_end = output.rfind('}')
            if json_start != -1 and json_end != -1 and json_end > json_start:
                data = json.loads(output[json_start:json_end+1])
                return data, True
        except Exception:
            pass
        return None, False

    def _find_json_syntax_error(self, diagnostics: list) -> dict | None:
        """Find the earliest syntax error diagnostic in the list, if any."""
        syntax_errors = [
            d for d in diagnostics
            if d.get("category", "").startswith("parse") or d.get("category", "").startswith("syntax")
        ]
        if not syntax_errors:
            return None
        
        # Sort syntax errors so the earliest in the file is selected
        def get_sort_key(d):
            loc = d.get("location", {})
            start = loc.get("start") or loc.get("range", {}).get("start", {})
            line_val = start.get("line") if start else None
            col_val = start.get("column") if start else None
            return (line_val if line_val is not None else 999999, col_val if col_val is not None else 999999)
        
        syntax_errors.sort(key=get_sort_key)
        return syntax_errors[0]

    def _find_text_syntax_error(self, output: str, temp_name: str) -> tuple[str, int | None, int | None] | None:
        """Fallback check for syntax errors in plain-text output."""
        if ("pars" in output.lower() or "syntax" in output.lower() or "error[" in output.lower()) and "error[lint/" not in output.lower():
            clean_lines = [
                l.strip() for l in output.splitlines()
                if l.strip() and "━━" not in l
            ]
            err_line = clean_lines[0] if clean_lines else "Unknown parse error"
            err_line = _clean_biome_output(err_line)
            line, column = None, None
            match = re.search(rf"{re.escape(temp_name)}:(\d+):(\d+)", output)
            if match:
                line = int(match.group(1))
                column = int(match.group(2))
            return err_line, line, column
        return None

    def _get_tsc_command(self) -> list[str] | None:
        """Check for tsc globally or via npx."""
        tsc_exe = shutil.which("tsc")
        log_step(f"JsTsValidator.validate: fallback tsc check path: {tsc_exe}")
        if tsc_exe:
            return [tsc_exe]
        npx = shutil.which("npx")
        if npx:
            return [npx, "tsc"]
        return None

    def _run_tsc_check(self, tsc_cmd: list[str], temp_path: Path, content: str) -> subprocess.CompletedProcess:
        """Run tsc check on a temp file."""
        with open(temp_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return subprocess.run(
            tsc_cmd + ["--noEmit", "--skipLibCheck", str(temp_path)],
            text=True,
            capture_output=True,
            check=False,
            shell=(os.name == 'nt'),
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=15
        )

    def _parse_tsc_error(self, err_msg: str, temp_name: str, filename: str) -> SyntaxValidationError:
        """Parse tsc error output and return SyntaxValidationError."""
        err_msg = err_msg.encode("ascii", errors="replace").decode("ascii")
        line, column = None, None
        match = re.search(rf"{re.escape(temp_name)}\((\d+),(\d+)\)", err_msg)
        if match:
            line = int(match.group(1))
            column = int(match.group(2))
        return SyntaxValidationError(
            message=f"TSC TS Syntax Error: {err_msg.strip()}",
            filename=filename,
            line=line,
            column=column
        )

    def _cleanup_temp_path(self, temp_path: Path) -> None:
        """Clean up the temporary validation file."""
        if temp_path.exists():
            try:
                temp_path.unlink()
                log_step("JsTsValidator.validate: deleted temp path")
            except Exception as e:
                log_step(f"JsTsValidator.validate: failed to delete temp path: {e}")

    def _extract_json_error_details(self, err: dict) -> tuple[str, int | None, int | None]:
        """Extract error message, line, and column from JSON diagnostic."""
        err_line = err.get("description") or err.get("message", "Syntax error")
        err_line = _clean_biome_output(err_line)
        line, column = None, None
        location = err.get("location", {})
        if location:
            start_pos = location.get("start") or location.get("range", {}).get("start", {})
            if start_pos:
                line = start_pos.get("line")
                column = start_pos.get("column")
        return err_line, line, column

    def _check_original_validity(self, biome_exe: str, base_args: list[str], temp_path: Path, original_content: str) -> bool:
        """Return True if original content has syntax errors, indicating we should skip validation."""
        log_step("JsTsValidator.validate: writing original content to temp path...")
        with open(temp_path, "w", encoding="utf-8", newline="") as f:
            f.write(original_content)
        log_step(f"JsTsValidator.validate: running orig check with {biome_exe}...")
        try:
            orig_process = subprocess.run(
                [biome_exe] + base_args + ["--reporter=json", str(temp_path)],
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
                orig_output = (orig_process.stdout or "") + "\n" + (orig_process.stderr or "")
                data, json_parsed_successfully = self._parse_biome_json(orig_output)
                if json_parsed_successfully:
                    diagnostics = data.get("diagnostics", []) if data else []
                    if self._find_json_syntax_error(diagnostics) is not None:
                        log_step("JsTsValidator.validate: original content has syntax error, skipping validation")
                        return True
                else:
                    if self._find_text_syntax_error(orig_output, temp_path.name) is not None:
                        log_step("JsTsValidator.validate: original content has syntax error, skipping validation")
                        return True
        except OSError as e:
            log_step(f"JsTsValidator.validate: biome original check failed: {e}")
        return False

    def _parse_biome_error(self, output: str, temp_name: str, filename: str) -> SyntaxValidationError | None:
        """Parse Biome output for syntax error diagnostics and return SyntaxValidationError if found."""
        is_syntax_err = False
        err_line = "Unknown parse error"
        line, column = None, None

        # Attempt to parse as structured JSON
        data, json_parsed_successfully = self._parse_biome_json(output)
        if json_parsed_successfully:
            diagnostics = data.get("diagnostics", []) if data else []
            err = self._find_json_syntax_error(diagnostics)
            if err:
                is_syntax_err = True
                err_line, line, column = self._extract_json_error_details(err)
        else:
            # Fallback to plain text check
            text_err = self._find_text_syntax_error(output, temp_name)
            if text_err:
                is_syntax_err = True
                err_line, line, column = text_err

        if is_syntax_err:
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
            with open(temp_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            log_step(f"JsTsValidator.validate: running new check with {biome_exe}...")
            process = subprocess.run(
                [biome_exe] + base_args + ["--reporter=json", str(temp_path)],
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
                output = (process.stdout or "") + "\n" + (process.stderr or "")
                err = self._parse_biome_error(output, temp_path.name, filename)
                if err:
                    log_step(f"JsTsValidator.validate: raising SyntaxValidationError for line={err.line} col={err.column}")
                    raise err
        except SyntaxValidationError:
            raise
        except OSError as e:
            log_step(f"JsTsValidator.validate: biome execution failed: {e}")
        finally:
            self._cleanup_temp_path(temp_path)
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

    def _validate_with_tsc(self, content: str, filename: str, original_content: str) -> None:
        if not filename.endswith((".ts", ".tsx")):
            return
        tsc_cmd = self._get_tsc_command()
        if not tsc_cmd:
            return

        suffix = f".patchitright_temp{Path(filename).suffix}"
        temp_path = Path(filename).with_suffix(suffix)
        try:
            # Check original first
            orig_process = self._run_tsc_check(tsc_cmd, temp_path, original_content)
            if orig_process.returncode != 0:
                log_step("JsTsValidator.validate: original failed tsc check, skipping validation")
                return

            # Check new content
            process = self._run_tsc_check(tsc_cmd, temp_path, content)
            if process.returncode != 0:
                err_msg = process.stdout or process.stderr
                log_step("JsTsValidator.validate: raising tsc SyntaxValidationError")
                raise self._parse_tsc_error(err_msg, temp_path.name, filename)
        except SyntaxValidationError:
            raise
        except OSError as e:
            log_step(f"JsTsValidator.validate: tsc execution failed: {e}")
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def validate(self, content: str, filename: str, original_content: str = "") -> None:
        log_step(f"JsTsValidator.validate: starting for {filename}...")
        biome_cmd = self._get_biome_command()
        if biome_cmd:
            self._validate_with_biome(biome_cmd, content, filename, original_content)
        else:
            if filename.endswith((".ts", ".tsx")):
                self._validate_with_tsc(content, filename, original_content)
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
            with open(temp_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
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
                    if not line.strip() or "\u2501\u2501" in line or "Found" in line or "Checked" in line or "emitted" in line:
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
