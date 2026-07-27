# patchitRIGHT Repository Guidelines

> [!IMPORTANT]
> Global Policies apply to this repository by default. This file contains repository-specific rules for patchitRIGHT.

## Rules for Patching & Self-Modification

- **MUST call `patchitright_guide` first** before making any file modifications to dynamically retrieve latest limits, formatting constraints, and safety guidelines.
- **Surgical Precision**: Keep `search_content` snippets focused on the minimum necessary surrounding code for a unique match. PREFER using the `replacements` parameter for editing multiple separate blocks in a single file in one call.
- **Use Caution on Self-Modification**: Sourcing edits to the MCP server's own codebase (`src/patchitright_mcp/`) will trigger dev reloads. Proactively use `dry_run` first to preview the changes.
