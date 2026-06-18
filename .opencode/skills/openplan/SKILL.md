---
name: openplan
description: AI-native MCP planner for project planning, tracking, and implementation with cost-aware A* pathfinding, self-calibration, and cross-project estimation
---

## What OpenPlan Is

OpenPlan is an MCP server that helps AI agents plan, track, and implement projects. It maintains a directed state graph with learned edge costs, enabling cost-aware A* pathfinding between project states. All 4 tools (init, act, recommend, export) are available via MCP tool calls.

## When to Use

- Starting a new project from scratch (`init` → `act(options)` → `recommend` cycle)
- Finding the cheapest path between project milestones (`recommend(target=...)`)
- Evaluating strategies before committing (`recommend(sequence=...)`)
- Reviewing project health and progress (`recommend(detail=true)`, `export`)
- Debugging blocked states (`act(dry_run=true)` → `recommend`)

## Workflow

### Starting a Project
1. `init(project, label, project_type, goal)` — creates root state with goal markers
2. `act(project, options=[...])` — creates auto-sequenced work items (use `parallel=true` for fan-out)
3. `act(project, target="...", status="done")` — mark states complete with evidence
4. `recommend(project)` — finds next highest-value target with A* path

### Planning and Pathfinding
1. `recommend(project, target="...")` — A* path with risk-adjusted cost and effective_cost
2. `recommend(project, sequence=[...])` — multi-step what-if simulation
3. `recommend(project, top_k=3)` — compare multiple alternative targets

### Goal Tracking
- Goals parse into markers from comma-separated goal text on `init()`
- Auto-achieved when state label contains criterion text (bidirectional substring match)
- Explicitly tick via `act(action="verify", satisfies_goal="criterion text")`
- Goal progress visible in `recommend()` output under `goal.markers`

### Evidence and Verification
- `act(action="verify", evidence=[...])` — attach evidence with filesystem stat check
- `file` type evidence: stat'd on disk; missing files get `status: unverified`
- `commit`, `test`, `checkpoint` types: always verified (no stat needed)
- Evidence metadata (size, mtime, error reason) returned in read-back
- `evidence_total` and `evidence_verified` in project_health

### Monitoring and Health
- `recommend(project, detail=true)` — full health, self-tuning, estimation by type
- `recommend(mode="plan")` — estimate costs for a new project
- `recommend(mode="retro")` — compare planned vs actual costs
- `recommend(mode="learnings")` — cross-project patterns with variability analysis
- `export(project, format="json")` — full graph dump
- `export(project, format="graphml")` — graph for external viz tools

## Output Interpretation

### Health Metrics
- `calibration_rate`: fraction of edges with real data (>0.5 = good)
- `orphan_count`: states with no outgoing edges
- `completed / remaining`: state completion
- `blockers`: states with status "blocked" or "cascade_blocked"
- `evidence_total`, `evidence_verified`: evidence counts
- `goal.markers`: per-criterion achievement tracking

### Effective Cost vs. Cost Tokens
- `effective_cost`: A*-adjusted cost after weight history, action penalties, success-rate penalties, and risk adjustment
- `cost_tokens`: raw edge cost before adjustments
- `estimation_by_type`: per-project-type cost baselines (e.g., python_cli vs web_app)

### Self-Tuning Bandit
- `bandit_arm`: current arm (threshold + penalty combo)
- `acceptance_rate`: how often the bandit's choices are accepted
- `convergence`: "low_data", "exploring", or "converging"
- `acts_since_tune`: acts since last tuning run

## Best Practices (Learned from Self-Hosting)

1. **ALWAYS set project_type on init.** Without it, `estimation_by_type`, `learnings`, and `plan` mode cannot accumulate data for this project type. The work becomes invisible to cross-project learning.

2. **Use `satisfies_goal` to tick markers explicitly.** The automatic label-matching is a best-effort convenience. To guarantee a marker is achieved after completing work, call `act(action="verify", satisfies_goal="criterion text")`.

3. **Pass `expected_cost` on traversals.** Without it, `cost_delta` is always zero and the edge calibrates against the default 1000 cost. With `expected_cost`, the delta is meaningful and `cost_source` becomes `"agent"`, marking the calibration as real data.

## Cost Model

Edges auto-calibrate via chain-calibrate on every `act()`. Costs are stored per-project and per-project-type in `cost_baselines`. Self-tuning:
- Penalizes actions with <30% success rate (1.5x cost multiplier)
- Rewards actions with improving success rates (0.9x discount)
- Adjusts activation threshold based on overall calibration rate
- Options auto-sequence by default with cost from action-type baselines

## Resources

Project data is available as MCP resources:
- `openplan://projects` — all projects
- `openplan://analytics` — cross-project analytics
- `openplan://tuning` — global tuning statistics
- `openplan://{project}/graph` — health snapshot
- `openplan://{project}/edges` — all edges as JSON
- `openplan://{project}/health` — health metrics
