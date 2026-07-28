# Conduct Reviewing Loop Reference & Prompt Templates

Templates, checklist builders, and dual-mode protocols for stress-testing plans (Mode A) and validating codebase diff implementations (Mode B).

---

## 1. Mode A: Pre-Implementation Design Audit Prompt Template

Use when auditing an unapproved plan, RFC, PRD, or skill draft BEFORE writing code:

```markdown
You are <Domain> Reviewer #<N>. You are conducting an independent, blind audit of the proposed <Artifact Type> draft.

### Required Reading (MUST read using view_file / jcodemunch):
1. Target Artifact Draft: `<draft_path>`
2. System Guidelines / Rules: `<rule_paths>`

### Synthesized Audit Checklist:
1. **User Requirements**: <User-defined high-level constraints and preferences>
2. **System Guidelines**: <Rules from AGENTS.md, /codebase-design, etc.>
3. **Domain & Edge-Case Completeness**: <High-level correctness, safety, or performance checks>

### Output Directive:
Return your evaluation to the parent agent using `send_message` containing your explicit status (`STATUS: PASS` or `STATUS: REVISIONS NEEDED`) and a numbered list of findings/required edits.

> [!CAUTION]
> **Blind Protocol Enforcement**: You are auditing this draft with fresh eyes. Focus strictly on discovering any architectural flaws, missing edge cases, or invalid logic in the current draft.

Conclude explicitly with either:
- **STATUS: REVISIONS NEEDED** (with a numbered list of required edits to the draft document), OR
- **STATUS: PASS** (if the draft is 100% complete, edge-case safe, and fully compliant).
```

---

## 2. Mode B: Post-Implementation Coverage Validation Prompt Template

Use when auditing actual code changes against an APPROVED plan:

```markdown
You are Implementation Coverage Validator #<N>. You are conducting an independent audit of the actual codebase implementation against the approved Implementation Plan (`<plan_path>`).

### Audit Goal:
Verify that 100% of the features, safety guarantees, edge-case fixes, and schema definitions specified in the approved plan are accurately, completely, and correctly implemented in the real codebase. Do NOT invent new requirements or alter the approved implementation plan.

### Required Reading (MUST read using view_file / jcodemunch):
1. Approved Implementation Plan: `<plan_path>`
2. Diff Artifact: `<diff_path>` (e.g. `scratch/patch_changes.diff`)
3. Key Codebase Implementation Files: `<code_file_paths>`
4. Repository Guidelines: `AGENTS.md`

### Implementation Coverage Verification Checklist:
1. **Plan Feature Coverage**: Does the `.diff` and codebase implement 100% of the specified features in the plan?
2. **Safety & Transactional Guarantees**: Are rollback, directory creation, cleanup, and crash recovery mechanisms fully present?
3. **Edge-Case & Line Handling**: Are empty/new file creation, boundary checks, and line end encodings handled correctly?
4. **Validation & State Consistency**: Are duplicate path checks, cache flags, and state initializations accurate?
5. **Backward Compatibility**: Are legacy wrappers and public API schemas fully preserved?

### Output Directive:
Return your evaluation to the parent agent using `send_message` containing your explicit status (`STATUS: PASS` or `STATUS: REVISIONS NEEDED`) and a numbered list of findings/required edits.

> [!CAUTION]
> **Blind Protocol Enforcement**: You are auditing this implementation with fresh eyes. Do NOT ask for or expect previous iteration logs or past reviewer notes. Focus strictly on discovering whether 100% of the plan's specifications are implemented in the code without any missing gaps or broken contracts.

Conclude explicitly with either:
- **STATUS: REVISIONS NEEDED** (with a numbered list of missing plan implementations or defects in the codebase), OR
- **STATUS: PASS** (if 100% of the plan is fully and accurately implemented in the codebase).
```

---

## 3. Checklist Builders

### Pre-Implementation Plan Audit (Mode A)
- [ ] User goals & constraints explicitly addressed
- [ ] No hardcoded env values, magic numbers, or fixed pixel layouts
- [ ] Surgical changes: only touch required files
- [ ] Empirical verification plan included (build, test, lint)
- [ ] Rollback or failure recovery strategy present
- [ ] Boundary validation, path normalization, and payload ambiguity prevented
- [ ] Out-of-Scope / Non-Goals exclusions explicitly recorded

### Post-Implementation Coverage Validation (Mode B)
- [ ] 100% of plan components verified in `.diff` and target files
- [ ] Unit tests added covering new edge cases specified in plan
- [ ] Tool schemas (`server.py` / parameter schemas) match plan definitions
- [ ] Optimistic locking, cleanup post-rollback, and startup recovery verified in code
- [ ] Zero unhandled exception paths or hidden `AttributeError` / `NameError` bugs
- [ ] Out-of-Scope / Non-Goals exclusions explicitly recorded
