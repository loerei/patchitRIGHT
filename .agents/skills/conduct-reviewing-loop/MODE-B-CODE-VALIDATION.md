# Mode B: Post-Implementation Coverage Validation Reference

Templates, checklist builders, and .diff artifact protocols for validating codebase implementation against an approved plan.

---

## 1. Mode B: Post-Implementation Coverage Validation Prompt Template

Use when auditing actual code changes against an APPROVED plan:

```markdown
You are Implementation Coverage Validator #<N>. Audit the codebase implementation against the approved Implementation Plan (`<plan_path>`).

### Audit Goal:
Verify that 100% of the features, safety guarantees, edge-case fixes, and schema definitions specified in the approved plan are accurately, completely, and correctly implemented in the real codebase. Do NOT invent new requirements or alter the approved implementation plan.

### Required Reading (MUST read using view_file / jcodemunch):
1. Approved Implementation Plan: `<plan_path>`
2. Diff Artifact: `<diff_path>` (e.g. `scratch/patch_changes.diff`)
3. Key Codebase Implementation Files: `<code_file_paths>`
4. Repository Guidelines: `AGENTS.md`
5. Task-Specific Domain Skills: `<task_domain_skill_paths>`

### Implementation Coverage Verification Checklist:
1. **Plan Feature Coverage**: Does the `.diff` and codebase implement 100% of the specified features in the plan?
2. **Task Checklist Verification**: Is every checkbox item (`- [x]`) in `implementation_plan.md` backed by actual, verified implementation code in the `.diff` patch?
3. **Safety & Transactional Guarantees**: Are rollback, directory creation, cleanup, and crash recovery mechanisms fully present?
4. **Edge-Case & Line Handling**: Are empty/new file creation, boundary checks, and line end encodings handled correctly?
5. **Validation & State Consistency**: Are duplicate path checks, cache flags, and state initializations accurate?
6. **Backward Compatibility**: Are legacy wrappers and public API schemas fully preserved?

### Output Directive:
Return your evaluation to the parent agent using `send_message` containing:
1. Explicit status (`STATUS: PASS` or `STATUS: REVISIONS NEEDED`)
2. Numbered list of missing plan implementations or defects in the codebase (blocking issues)
3. (Optional) `Suggestions for Improvement (Non-blocking)`: Polish or future considerations that do NOT affect PASS status.

Conclude explicitly with either:
- **STATUS: REVISIONS NEEDED** (with a numbered list of missing plan implementations or defects in the codebase), OR
- **STATUS: PASS** (if 100% of the plan is fully and accurately implemented in the codebase).
```

---

## 2. Post-Implementation Coverage Validation Checklist

- [ ] 100% of plan components and Execution Checklist items (`- [x]`) verified in `.diff` and target files
- [ ] Adherence to task-specific domain skills (<task_domain_skill_paths>) verified
- [ ] Unit tests added covering new edge cases specified in plan
- [ ] Tool schemas (`server.py` / parameter schemas) match plan definitions
- [ ] Optimistic locking, cleanup post-rollback, and startup recovery verified in code
- [ ] Zero unhandled exception paths or hidden `AttributeError` / `NameError` bugs
- [ ] Out-of-Scope / Non-Goals exclusions explicitly recorded

---

## 3. Mode B .diff Artifact & Immutable Plan Protocol

1. **Generate `.diff` Artifact**: Run `git diff origin/<default-branch>` (or target base branch, capturing both committed and working tree edits) and save the untruncated patch to `<appDataDir>\brain\<conversation-id>\scratch\patch_changes.diff`.
2. **Provide 3-Way Context**: Supply the subagent reviewer with:
   - Approved `implementation_plan.md` path.
   - `.diff` artifact path (`scratch/patch_changes.diff`).
   - Core codebase implementation files (`src/`, `tests/`).
3. **Iterative `.diff` Regeneration**: In Mode B, after applying codebase fixes in iteration $N$, the Main Agent MUST re-generate `scratch/patch_changes.diff` before spawning Reviewer $N+1$.
4. **Immutable Plan Principle**: In Mode B, the approved plan is treated as immutable. Reviewers must NOT request edits to the plan; any discrepancy between plan and code must be resolved by fixing the **codebase**.
