<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **patchitRIGHT** (1061 symbols, 1870 relationships, 68 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/patchitRIGHT/context` | Codebase overview, check index freshness |
| `gitnexus://repo/patchitRIGHT/clusters` | All functional areas |
| `gitnexus://repo/patchitRIGHT/processes` | All execution flows |
| `gitnexus://repo/patchitRIGHT/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Rules for Patching & Self-Modification

- **MUST call `patchitright_guide` first** before making any file modifications to dynamically retrieve latest limits, formatting constraints, and safety guidelines.
- **DO NOT** pass large multi-line blocks of code (over 50 lines) into `search_content`/`replace_content` for `patch_file`. Instead, always scope the edit using the `symbol_name` parameter (targeting classes or functions) to prevent indentation mismatch and token bloat.
- **Use Caution on Self-Modification**: Sourcing edits to the MCP server's own codebase (`src/patchitright_mcp/`) will trigger dev reloads. Proactively use `dry_run` first to preview the changes.
