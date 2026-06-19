---
name: openplan
description: MCP server for project planning, cost tracking, and estimation with self-calibration and cross-project learning
---

## What OpenPlan Is

OpenPlan is an MCP server that helps AI agents plan, track, and learn from software projects. 3 tools (plan, checkpoint, review), local SQLite, optional Mesh sync for cross-project learning.

## When to Use

- Starting a new project (`plan(goal=..., project=...)`)
- Tracking progress on each phase (`checkpoint(phase=..., actual_cost=..., route_id=...)`)
- Resuming after context loss (`checkpoint(route_id=...)` returns full state)
- Reviewing completed projects (`review(route_id=...)`)
- Re-planning when stuck (`plan(goal=..., replan=True)`)
- Correcting a checkpoint cost (`checkpoint(phase=..., correct=..., route_id=...)`)

## Workflow

1. `plan(goal="Build a landing page", context="Astro + Tailwind", project="landing-page")` — creates a route with costed phases
2. Implement each phase
3. `checkpoint(phase="Scaffold", actual_cost=2100, route_id=routeId)` — record progress
4. Repeat for all phases
5. `review(route_id=routeId)` — summary, learnings, self-diagnostics

## Key Concepts

- **Phase subsumption:** Checkpoints match pending phases by label substring
- **Cumulative costs:** Multiple sessions can contribute to the same phase
- **Correction:** `checkpoint(phase="X", correct=<value>, route_id=id)` fixes the last actual_cost
- **Personal bias:** Bayesian shrinkage blends your calibration history with the pool prior — more checkpoints = sharper estimates
- **Anchor file (`.openplan`):** Created by `plan()` at project root — enables session resume without knowing a route_id
- **Mesh sync:** Background sync to api.openplan.cc every 5 minutes for cross-session cost learning

## Best Practices

1. **Call `checkpoint(route_id=...)` after context loss.** Returns full route state with all phases.
2. **Use `plan(replan=True)` when stuck.** Archives old route, creates fresh decomposition.
3. **Write descriptive phase labels.** Labels like "Auth (Better Auth, magic link)" carry signal for estimation.
4. **Correct inaccurate checkpoints.** The correction is logged, not silently overwritten.
5. **Set `OPENPLAN_MESH_URL` for cloud sync** — enables cost learning across sessions.
