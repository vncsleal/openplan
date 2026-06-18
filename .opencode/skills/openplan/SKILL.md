---
name: openplan
description: MCP server for project planning, cost tracking, and estimation with self-calibration and cross-project learning
---

## What OpenPlan Is

OpenPlan is an MCP server that helps AI agents plan, track, and learn from software projects. 3 tools (plan, checkpoint, review), local SQLite, optional Mesh sync for cross-project learning.

## When to Use

- Starting a new project (`plan(goal=...)`)
- Tracking progress on each phase (`checkpoint(phase=..., actual_cost=...)`)
- Resuming after context loss (`checkpoint()` with no args)
- Reviewing completed projects (`review()`)
- Re-planning when stuck (`plan(replan=True)`)
- Correcting a checkpoint cost (`checkpoint(phase=..., correct=...)`)

## Workflow

1. `plan(goal="Build a landing page", context="Astro + Tailwind")` — creates a route with costed phases
2. Implement each phase
3. `checkpoint(phase="Scaffold", actual_cost=2100)` — record progress
4. Repeat for all phases
5. `review()` — summary, learnings, self-diagnostics

## Key Concepts

- **Phase subsumption:** Checkpoints match pending phases by label substring
- **Cumulative costs:** Multiple sessions can contribute to the same phase
- **Correction:** `checkpoint(phase="X", correct=<value>)` fixes the last actual_cost
- **Personal bias:** Auto-adjusted per identity, applied to future estimates
- **Anchor file (`.openplan`):** Created by `plan()` at project root — enables session resume without knowing a route_id

## Output Interpretation

- `deviation.ratio` — actual / expected (1.0 = on target)
- `deviation.outcome` — "success" (≤1.3×), "partial" (≤2.0×), "failure" (>2.0×)
- `hazards` — high-variance warnings for upcoming phases
- `summary.accuracy` — min(actual/expected, expected/actual)
- `self_diagnostics` — skip/merge/reorder rates, hazard precision/recall

## Best Practices

1. **Call `checkpoint()` after context loss.** Returns full state — no IDs needed.
2. **Use `plan(replan=True)` when stuck.** Archives old route, creates fresh decomposition.
3. **Write descriptive phase labels.** Labels like "Auth (Better Auth, magic link)" carry signal for estimation.
4. **Correct inaccurate checkpoints.** The correction is logged, not silently overwritten.
