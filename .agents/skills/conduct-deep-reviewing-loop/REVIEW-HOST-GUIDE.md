# Layer 2 Review Host Operational Guide

Instructions for Review Host to route Layer 3 reviewers, filter feedback, and enforce DAG gates.

## Core Responsibilities

0. **Scope Analysis & Dynamic Roster Selection**:
   - If `scratch/deep_review/host/Reviewer_Choice_Rationale.md` exists (Round N+1): Load and preserve the active roster without re-evaluating exclusions.
   - Else (Round 1): Inspect target DA scope and criteria, write `scratch/deep_review/host/Reviewer_Choice_Rationale.md` using the standard table layout:
     ```markdown
     | Role Identifier | Selection Status (INCLUDED / EXCLUDED) | Technical Rationale |
     ```
     Ensure `Architect` and `Logic` are `INCLUDED`, and explicitly mark remaining 8 roles as `INCLUDED` or `EXCLUDED`.
1. **Workspace Preparation**: Purge all files in `scratch/deep_review/reports/` and recursively purge all diagnostic files in `<repo-root>/.scratch/` (`.scratch/*`, idempotently handling missing directories) strictly at round start (before executing the first active tier) and before launching a Full Sweep pass (preserving intra-tier reports within an active pass). Validate `scratch/deep_review/Context.md` without overwriting criteria or `SP`.
2. **DAG Routing & Targeted Execution**:
   - If `scratch/deep_review/host/Changelog.md` exists: Reset `PassCount = 0`, determine highest modified tier for Targeted DAG routing per `PROTOCOL.md` Section 6, then delete `scratch/deep_review/host/Changelog.md` before invoking reviewers.
   - If `scratch/deep_review/host/Changelog.md` is absent:
     - If previous `host/Analyzation.md` recorded `ROUND_PASS`: Read active `PassCount` and run Full Sweep on the static DA across all active roles.
     - Else (Round 1): Initialize `PassCount = 0` and run Full DAG across all active roles (Tier 3.1 -> 3.2 -> 3.3 -> 3.4).
   - Vacuous Tier Handling: If all roles in an active tier are `EXCLUDED`, treat the tier as passed and advance immediately.
   - MUST use the invariant invocation template from `PROTOCOL.md` Section 3 with `<guide_path>` dynamically resolved relative to the active skill location and neutral tool metadata (`toolAction: "Summoning reviewer"`, `toolSummary: "Domain review"`). NEVER inject round numbers or phase names into reviewer prompts.
3. **Subagent Liveness & Heartbeat Monitoring (Deadlock Prevention)**:
   - When summoning Layer 3 reviewers, set a 60s liveness check timer via `schedule` (`DurationSeconds=60, TimerCondition="any"`).
   - If a reviewer stops responding without outputting its report to `scratch/deep_review/reports/<Role>.md`:
     1. Inspect subagent status via `manage_subagents(Action="list")`.
     2. If idle, hung, or stuck in background execution, send a status check ping via `send_message` (`"Status check: Please finalize your review report or disclose blockers."`).
     3. If non-responsive or errored, terminate and respawn that specific reviewer.
4. **Early Suspension**: If a tier returns `REVISION NEEDED`, cancel downstream tiers for that round.
5. **Full Sweep Clearance**: When all targeted roles pass (`TARGETED_PASS`), purge `reports/` and recursively purge all diagnostic files in `<repo-root>/.scratch/` (idempotently handling missing directories), and run a Full Sweep across all active roles in the frozen roster on the static DA snapshot before issuing `FINAL_PASS` or incrementing `PassCount`.
6. **Reporting & Final Teardown**: Evaluate Layer 3 reports from `scratch/deep_review/reports/` using `HOW-TO-PICK-UP-THE-RIGHT-OPINIONS.md`. Record `Current PassCount: <N> / <SP>`, write `scratch/deep_review/host/Analyzation.md` and `scratch/deep_review/host/Changelog.md` via `write_to_file`. When issuing `FINAL_PASS` (where `PassCount == SP`), recursively purge all temporary diagnostic files in `<repo-root>/.scratch/*` (idempotently handling missing directories).

## Role Summoning Table

| Role Identifier | Guide Reference Path | Output Artifact Path |
| :--- | :--- | :--- |
| `Architect` | `<skill-root>/ARCHITECT-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Architect.md` |
| `Readiness` | `<skill-root>/READINESS-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Readiness.md` |
| `Security` | `<skill-root>/SECURITY-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Security.md` |
| `DataMigration` | `<skill-root>/DATA-MIGRATION-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/DataMigration.md` |
| `Testability` | `<skill-root>/TESTABILITY-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Testability.md` |
| `Logic` | `<skill-root>/LOGIC-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Logic.md` |
| `Edgecase` | `<skill-root>/EDGECASE-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Edgecase.md` |
| `Performance` | `<skill-root>/PERFORMANCE-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Performance.md` |
| `Observability` | `<skill-root>/OBSERVABILITY-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/Observability.md` |
| `UXUI` | `<skill-root>/UXUI-REVIEWER-GUIDE.md` | `scratch/deep_review/reports/UXUI.md` |
