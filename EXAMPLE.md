# OpenPlan v0.1 — End-to-End Example

A complete walkthrough of the agent loop: building a Node.js API server with authentication, database, and deployment.

---

## 1. Init — Bootstrap the Project

```
init(project="my-api", label="Node.js API Server")
```

Creates the root state `S-000001`. Idempotent — safe to call again.

---

## 2. Branch — Explore Your Options

From the root, declare your first decision point:

```
branch(state="S-000001", options=[
  {label: "Set up project scaffolding", action: "implement", prob: 0.95, expected_cost: {tokens: 5000, risk: 0.05}},
  {label: "Evaluate framework choices",   action: "research",  prob: 0.80, expected_cost: {tokens: 8000, risk: 0.10}},
])
```

Creates two new states (S-000002, S-000003) with auto-boost. The graph now looks like:

```
S-000001 (Root)
  ├── implement → S-000002 (Set up project scaffolding) [p=0.95, cost=5000]
  └── research  → S-000003 (Evaluate framework choices)  [p=0.80, cost=8000]
```

---

## 3. Observe — Find the Frontier

```
observe(project="my-api", scope="frontier")
```

Returns the root state with activation > 0.5 and outgoing edges. The recommended field points to the highest-value next state.

---

## 4. Act — Traverse an Edge

Pick the scaffolding route and execute it:

```
act(
  state="S-000001",
  action="implement",
  target="S-000002",
  evidence="https://github.com/user/my-api/commit/abc123",
  thought="Initialized Express + TypeScript project structure",
  expected_cost={tokens: 5000, risk: 0.05}
)
```

Returns `cursor: S-000002`. Your position in the graph has moved. Also returns `activation_delta` (how the graph's focus shifted), `cost_actual` (real cost), and `new_frontier` (fresh states to act on).

---

## 5. Branch Again — Decompose the Work

From the scaffolding state, branch into sub-tasks:

```
branch(state="S-000002", options=[
  {label: "Add authentication middleware", action: "implement", prob: 0.85, expected_cost: {tokens: 12000, risk: 0.15}},
  {label: "Add database models",           action: "implement", prob: 0.90, expected_cost: {tokens: 10000, risk: 0.10}},
  {label: "Add API routes",               action: "implement", prob: 0.80, expected_cost: {tokens: 8000,  risk: 0.10}},
])
```

---

## 6. Plan — Find the Optimal Path

Before acting, check the best route to your goal:

```
plan(from_id="S-000002", target_id="S-000005", constraints={max_cost: 25000, min_prob: 0.7})
```

```
{
  path: ["S-000002", "S-000004", "S-000005"],
  expected_cost: {tokens: 22000, risk: 0.10, steps: 2},
  traversal: [
    {from: "S-000002", action: "implement", to: "S-000004", prob: 0.90},
    {from: "S-000004", action: "implement", to: "S-000005", prob: 0.95},
  ]
}
```

If no path exists or uncertainty is high, plan flags it — a signal to branch again.

---

## 7. Act, Then Learn — Calibrate

After each act, call learn to adjust edge weights:

```
learn(
  from_state="S-000002",
  to_state="S-000004",
  outcome="success",
  actual_cost=11000,
  insight="Used Prisma ORM — faster than SQL boilerplate"
)
```

The edge's cost estimate drops toward reality. The next plan will have a better heuristic.

After multiple acts, edges with 3+ calibration entries use a weighted moving average:

```
After 1 act:  cost = initial estimate (no change)
After 3+ acts: cost = 0.3 × actual_avg + 0.7 × initial
```

Probability also adjusts: success → boost, failure → decay.

---

## 8. Diagnostics — Check Graph Health

```
diagnostics(project="my-api")
```

```
{
  overview: {states: 12, edges: 15, events: 18, max_depth: 4, ...},
  health:   {calibrated_edges: 3, action_types: 2, ...},
  issues: [
    {code: "HIGH_ORPHAN_COUNT",   severity: "medium", fix: "Branch from orphan states..."},
    {code: "NO_CALIBRATION",      severity: "high",   fix: "Call learn() after each act()"},
  ]
}
```

The agent loop calls diagnostics when the frontier empties. Issues guide the next iteration.

---

## Full Agent Loop

```
cursor = observe(project).recommended

loop:
  1. observe(project, scope="frontier")      # where am I?
  2. if frontier empty:                       # stuck?
       diagnostics(project)                   # assess health
       if diagnostics.issues: fix them
       else: done
  3. plan(from_id=cursor, target_id=...)      # how to get there?
  4. if high_uncertainty:                     # risky path?
       branch(state=cursor, options=[...])    # explore alternatives
       continue                               # re-observe
  5. act(state=cursor, action=<verb>,         # go
         target="S-XXXXXX",
         expected_cost={tokens, risk})
  6. learn(from_state=cursor,                 # calibrate
           to_state=result.next_state,
           outcome="success",
           actual_cost=<tokens>)
  7. cursor = result.cursor                   # move forward
```

---

## Export — Snapshot for Analysis

```
export(project="my-api", format="json")    # Full data dump
export(project="my-api", format="matrix")  # Adjacency matrix (for numpy)
export(project="my-api", format="graphml") # GraphML (for Gephi/vis)
```

---

## Reference: Tool Input/Output Shapes

| Tool | Required Inputs | Returns |
|------|----------------|---------|
| `init` | project | state_id, label, created |
| `observe` | project | mode, states[], recommended, graph |
| `act` | state, action | cursor, next_state, activation_delta, cost_actual |
| `branch` | state, options[] | branch_id, states_created[] |
| `plan` | from_id, target_id | path[], expected_cost, traversal[] |
| `learn` | from_state, to_state, outcome, actual_cost | calibration, activation_shifts |
| `diagnostics` | project | overview, health, issues[] |
| `export` | project, format | nodes/edges/events or matrix or graphml |
| `project_list` | — | projects[], roots{} |
| `compress` | project, older_than_days | archived_events, merged_orphans |
