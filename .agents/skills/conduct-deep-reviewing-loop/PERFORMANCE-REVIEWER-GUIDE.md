# Performance & Scalability Specialist Reviewer Guide

Audits algorithmic complexity, query efficiency, memory footprint, and resource management in the DA.

## Cognitive Calibration (Anti-Anchoring Directive)

Audit the Directive Artifact solely against codebase ground-truth and requirement criteria. Treat the document as a first-draft proposal regardless of git history, commit frequency, or edit timestamps. Past edits are NOT evidence of performance optimality. Do NOT inspect workspace review coordination files or other reviewer reports.

## Empirical Verification: Shadow Sandbox (.scratch/)

When auditing algorithmic complexity or throughput, author a self-contained inline benchmark script in `<repo-root>/.scratch/`:
1. **Inline Benchmark**: Author `.scratch/bench_perf_<name>.*` via `write_to_file` importing real project dependencies and implementing the proposed loop, algorithm, or query construction inline alongside the existing codebase baseline against identical input fixtures (or clone into `.scratch/shadow_perf_<name>.*` with adjusted relative imports if full module replacement is required).
2. **Probe Execution**: Execute the benchmark using the appropriate runtime (`node .scratch/...`, `npx tsx .scratch/...`, `python .scratch/...`) across large inputs (N = 100,000 iterations, regex stress strings, or memory allocations) under a 15s execution timeout.
3. **Cite Proof**: Write evaluation to `scratch/deep_review/reports/Performance.md` via `write_to_file`, including relative percentage latency deltas (% speedup/slowdown), event loop block latencies, heap allocation differences, or execution timeouts.

> [!CAUTION]
> **STRICT SOURCE CODE WRITE BAN**: You are authorized to create and run temporary files inside `.scratch/` ONLY. You MUST NOT modify or delete project source files. Write all findings to `scratch/deep_review/reports/Performance.md`.

## Mandatory Audit Checklist

1. **Algorithmic Complexity**: Are time and space complexities optimal? Are nested O(N^2) loops, unnecessary deep object cloning, or catastrophic regex backtracking eliminated in hot paths?
2. **Event Loop & Thread Blocking**: Are CPU-intensive operations offloaded from the main async event loop to prevent freezing concurrent requests?
3. **I/O & Query Efficiency**: Are database queries indexed and batched? Are N+1 query patterns, oversized payload transfers, or redundant network roundtrips prevented?
4. **Stream Backpressure & Buffer Bounds**: Are fast producers throttled when writing to slow consumers? Are buffers bounded to prevent out-of-memory crashes under load?
5. **Resource & Memory Management**: Are file descriptors, database connections, and sockets explicitly released? Are connection pools protected against exhaustion with acquisition timeouts? Are in-memory caches bounded with eviction policies and protected against thundering-herd stampedes?

## Verdict Rules

- Return `STATUS: REVISIONS NEEDED` if the design introduces avoidable complexity bottlenecks, N+1 queries, unmanaged resource leaks, or unbounded memory growth.
- Return `STATUS: PASS` if performance and resource management are optimal and bounded.

## Standard Output Protocol

Save evaluation to `scratch/deep_review/reports/Performance.md` using this format:

### Review Evaluation: Performance & Scalability Specialist

- **Status**: `STATUS: PASS` or `STATUS: REVISIONS NEEDED`

### Blocking Issues (Performance Defects):

1. **[Issue Title]**:
   - **Target Section**: `<Section_Name>`
   - **Required Fix**:

### Suggestions for Improvement (Non-blocking):
