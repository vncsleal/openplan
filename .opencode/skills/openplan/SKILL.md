---
name: openplan
description: MCP server for project planning, cost tracking, and estimation with self-calibration and cross-project learning
---

## What OpenPlan Is

OpenPlan is an MCP server that helps AI agents plan, track, and learn from software projects. It maintains cost estimates and phase sequences in a local SQLite database, with optional Mesh sync for cross-project learning. All 3 tools (plan, checkpoint, review) are available via MCP tool calls.

## When to Use

- Starting a new project (`plan(goal=...)`)
- Tracking progress on each phase (`checkpoint(phase=..., actual_cost=...)`)
- Resuming work after context loss (`checkpoint()` with no args)
- Reviewing completed projects (`review()`)
- Re-planning when a dead end is hit (`plan(replan=true)`)

## Workflow

### Starting a Project
1. `plan(goal="Build a landing page")` — creates a route with costed phases
2. Implement each phase
3. `checkpoint(phase="Scaffold + setup", actual_cost=2100)` — record progress
4. Repeat for all phases
5. `review()` — get summary, learnings, self-diagnostics

### Session Resume
1. `checkpoint()` with no args — returns full route state and position
2. Continue working from where you left off

### Re-planning
1. `plan(goal="Same goal", replan=true)` — archives current route, creates fresh decomposition
2. Old route preserved with abandon_reason for path learning

## Output Interpretation

### Plan Response
- `route.phases` — ordered phases with `expected_cost` and `ci` (confidence interval)
- `route_evidence.based_on` — source of the phase sequence (historical match or default)
- `personal_bias` — your historical accuracy ratio across all checkpoints

### Checkpoint Response
- `deviation.ratio` — actual / expected (1.0 = on target)
- `deviation.outcome` — "success" (≤1.3x), "partial" (≤2.0x), "failure" (>2.0x)
- `hazards` — warnings about high-variance upcoming phases
- `route_completed` — true when last phase is checkpointed

### Review Response
- `summary.accuracy` — min(actual/expected, expected/actual), higher is better
- `accuracy_by_action` — per-action accuracy stats for personal calibration
- `path_learning` — similar completed sequences for route evidence

## Best Practices

1. **Call `checkpoint()` with no args after context loss.** It returns full state — no IDs needed.
2. **Use `plan(replan=true)` when stuck.** It archives the old route (preserving data) and creates a fresh decomposition.
3. **Write descriptive phase labels.** Labels like "Auth (Better Auth, magic link)" carry stack signal for cost estimation.
4. **Report `actual_cost` accurately.** The Mesh converges through volume — individual noise averages out over thousands of checkpoints.
