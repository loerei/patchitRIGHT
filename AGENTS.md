# patchitRIGHT Repository Guidelines

> [!IMPORTANT]
> Global Policies apply to this repository by default. This file contains repository-specific rules for patchitRIGHT.

## Rules for Patching & Self-Modification

- **MUST call `patchitright_guide` first** before making any file modifications to dynamically retrieve latest limits, formatting constraints, and safety guidelines.
- **Surgical Precision**: Keep `search_content` snippets focused on the minimum necessary surrounding code for a unique match. PREFER using the `replacements` parameter for editing multiple separate blocks in a single file in one call.
- **Use Caution on Self-Modification**: Sourcing edits to the MCP server's own codebase (`src/patchitright_mcp/`) will trigger dev reloads. Proactively use `dry_run` first to preview the changes.

## Versioning & Releases

- **SemVer (`0.MINOR.PATCH` prior to `1.0.0`)**: Bump `PATCH` for bug fixes/docs/internal refactors and `MINOR` for new tool parameters, language support, or breaking MCP schema/CLI changes.
- **Release Tag Parity**: Keep `project.version` in `pyproject.toml` aligned with `vX.Y.Z` tags; `.github/workflows/release.yml` enforces tag-to-version parity before publishing to PyPI.
