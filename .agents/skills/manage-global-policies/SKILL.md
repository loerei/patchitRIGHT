---
name: manage-global-policies
description: >
  Create, update, or edit global policy rules for AI agents. Use when the user wants to add, modify, delete, or rearrange rules in the global policy files across any IDE or machine.
---

# Manage Global Policies

This skill guides the agent through modifying the global policy files, ensuring consistency between the local IDE configurations and the remote custom skills repository.

## Workflows

### 1. Locate & Read Target Files Dynamically
Before making edits, locate or read policy configuration files on the current system:
1. **Repository Source Files (`AGENTS.md` and Platform Deltas):** Run `agents info policy.general` for universal root policy or `agents info policy.<platform>` (e.g. `agents info policy.gemini`) to get the exact `sourceFile` path in `myskills` and `destinationFile` path in the home directory.
   - Universal policy: `<custom-skills-repo-root>/AGENTS.md`
   - Platform-specific overrides (e.g. Gemini): `<custom-skills-repo-root>/gemini/AGENTS.md`
2. **Read Policy Subdocs Directly:** Run `agents read policy.<subdocname>` (e.g. `agents read policy.gemini.override_coverage_report`) to print raw subdoc Markdown content directly to stdout without searching file paths.
3. **Active IDE Global Config File:** Automatically resolved via `destinationFile` from `agents info policy.<platform>`.

### Cross-Repository Policy Protocol

When requested to update global policy rules while working inside an external project repository:
1. **Query Policy Source:** Run `agents info policy.<platform>` to locate the target `sourceFile` inside `myskills`.
2. **Edit Source File:** Edit `sourceFile` inside `myskills` directly.
3. **Distribute Back:** Run `agents --distribute` to update all active IDE global configs (`~/.gemini`, `~/.claude`, `~/.cursor`) and project workspaces.
4. **Commit & Push `myskills`:** Commit and push the updated policy file in `myskills`.

### 2. Apply Changes & Distribute
Whenever a policy change is made:
1. Update `<custom-skills-repo-root>/AGENTS.md` (and platform delta file such as `gemini/AGENTS.md` if platform-specific micro-anchors/rules apply).
2. Run `agents --distribute` to automatically deploy policy files (with per-platform override logic) and custom skills to all active IDE config targets (`~/.gemini`, `~/.claude`, `~/.cursor`) and workspace repositories.

### 3. Verify Policy Skill Coverage
Run the automated CLI coverage audit tool to ensure 100% of skills are documented across policies:
```powershell
agents audit
```
If any custom skills are missing from the policy table, run auto-insertion:
```powershell
agents audit --add
```

### 4. Commit & Push to GitHub
Navigate to `<custom-skills-repo-root>/`, commit the policy updates, and push to the remote repository:
```powershell
git add AGENTS.md gemini/AGENTS.md
git commit -m "Update global policies: <brief description of changes>"
git push
```
