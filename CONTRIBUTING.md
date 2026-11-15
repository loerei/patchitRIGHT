# Contributing to patchitRIGHT

## Local Setup

```bash
uv sync
uv run pytest -q
```

If an MCP client holds file locks on `.venv` on Windows, run tests in an isolated environment instead:

```bash
uv run --isolated python -m pytest -q
```

## Pull Requests

- Keep changes focused and include tests for behavior changes.
- Use Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `ci:`, `test:`, `chore:`).
- Do not include local `.scratch/`, logs, or build artifacts.

## Versioning and Releases

The project uses `0.MINOR.PATCH` SemVer prior to `1.0.0`, scoped to the MCP tool contract (`patch_file`, `write_file`, `apply_last_dry_run`, `patchitright_guide` schemas and CLI flags):

- **Patch (`0.1.x`)**: Bug fixes, AST resolver edge cases, docs, and internal refactors with no breaking schema or CLI changes.
- **Minor (`0.x.0`)**: New tool parameters, new language support, or breaking schema/behavior changes before `1.0.0`.

To cut a release:

1. Update `version` in `pyproject.toml`.
2. Commit and push to `main`.
3. Create and push a matching tag (`git tag vX.Y.Z && git push origin vX.Y.Z`) to trigger `.github/workflows/release.yml`.
