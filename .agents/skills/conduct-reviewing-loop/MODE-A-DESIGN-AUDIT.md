# Mode A: Pre-Implementation Design Audit Reference

Templates, checklist builders, and anti-anchoring protocols for stress-testing and auditing unapproved plans, RFCs, PRDs, or skill drafts BEFORE writing code.

---

## 1. Mode A: Pre-Implementation Design Audit Prompt Template

Use when auditing an unapproved plan, RFC, PRD, or skill draft BEFORE writing code.

> [!NOTE]
> **Checklist Synthesis Guidance (Main Agent)**:
> - **For All Draft Artifacts (PRDs, RFCs, Skills, Plans):** Include items #1–#4 in the subagent prompt.
> - **For Implementation Plans (`implementation_plan.md`):** MUST also include items #5 and #6, and require subagents to read the plan template (in `AGENTS.md` Section 2).

```markdown
You are <Domain> Reviewer #<N>. Audit the proposed <Artifact Type> draft.

### Required Reading (MUST read using view_file / jcodemunch / agents read):
1. Target Artifact Draft: `<draft_path>`
2. System Guidelines / Rules: `<rule_paths>`
3. Plan Layout Template (for Implementation Plans): `AGENTS.md` Section 2
4. Task-Specific Domain Skills: `<task_domain_skill_paths>` (e.g., /write-a-skill, /write-for-ai, /writing-great-skills for skill drafts, /tdd for tests, /design-taste-frontend for UI)

### Synthesized Audit Checklist:
1. **User Requirements**: <User-defined high-level constraints and preferences>
2. **System Guidelines**: <Rules from AGENTS.md, /codebase-design, etc.>
3. **Task-Specific Domain Skill Adherence**: <Adherence to /write-a-skill, /write-for-ai, /tdd, etc.>
4. **Domain & Edge-Case Completeness**: <High-level correctness, safety, or performance checks>
5. **Template & Layout Adherence** *(Include when auditing Implementation Plans)*: Verify strict adherence to the mandatory plan layout scaffold (`AGENTS.md` Section 2), including required sections (`## Architectural Summary & Key Decisions`, `## User Review Required`, `## Proposed Changes & Execution Checklist`, `## Verification Plan`).
6. **Execution Checklist Completeness** *(Include when auditing Implementation Plans)*: Verify that the Execution Checklist (`- [ ]`) covers 100% of proposed file modifications, schema changes, and edge-case handling steps outlined in the plan's architectural summary.

### Output Directive:
Return your evaluation to the parent agent using `send_message` containing:
1. Explicit status (`STATUS: PASS` or `STATUS: REVISIONS NEEDED`)
2. Numbered list of findings/required edits (blocking issues)
3. (Optional) `Suggestions for Improvement (Non-blocking)`: Polish or future considerations that do NOT affect PASS status.

Conclude explicitly with either:
- **STATUS: REVISIONS NEEDED** (with a numbered list of required edits to the draft document), OR
- **STATUS: PASS** (if the draft is 100% complete, edge-case safe, and fully compliant).
```

---

## 2. Pre-Implementation Audit Checklist

### General Checks (All Draft Artifacts):
- [ ] User goals & constraints explicitly addressed
- [ ] Adherence to task-specific domain skills (<task_domain_skill_paths>) verified
- [ ] Neutral document check: No past reviewer references, meta-changelogs, or anchoring tags inside draft content
- [ ] No hardcoded env values, magic numbers, or fixed pixel layouts
- [ ] Surgical changes: only touch required files
- [ ] Empirical verification plan included (build, test, lint)
- [ ] Rollback or failure recovery strategy present
- [ ] Boundary validation, path normalization, and payload ambiguity prevented
- [ ] Out-of-Scope / Non-Goals exclusions explicitly recorded

### Plan-Specific Checks (Implementation Plans Only):
- [ ] Strict adherence to mandatory layout template (`AGENTS.md` Section 2) verified
- [ ] Execution Checklist (`- [ ]`) verified covering 100% of proposed file edits, schema changes, and edge cases

---

## 3. Clean & Neutral Artifact Protocol (Anti-Anchoring)

When updating draft artifacts between review iterations, integrate all fixes seamlessly as native, first-class specifications. NEVER include past reviewer references, version tags based on reviewers, or meta-changelogs inside the document body.

For example, after Reviewer #3 points out an edge case or problem:

- **BAD (Meta-Contaminated / Anchoring Bias)**:
  > `# Plan v4 (Per Reviewer #3 Feedback)`  
  > `This plan resolves 100% of technical requirements and barriers raised by Reviewer #3.`  
  > `Fixes edgecase A pointed out by Reviewer #3 by...` *(Impact on Reviewer #4: Anchors on "This is THE resolved edgecase" rather than auditing freshly)*

- **GOOD (Clean Specification / Neutral)**:
  > `# Comprehensive Feature Upgrade Plan`  
  > `### Media-Only Message PairKey Deduplication`  
  > `Normalizes effectiveText using cleanText || media.url to ensure unique pairKey generation for media-only messages.` *(Impact on Reviewer #4: Evaluates the problem/solution neutrally as a standard, first-class specification)*
