# OpenPlan v0.2.5 — Session Handoff

**Date:** 2026-06-12  
**Next session should read this first.**

## What OpenPlan Is

MCP server for AI-native state space planning. Python, SQLite. Single agent navigates a directed graph with probabilistic edges, auto-calibrating costs, and a recommendation engine. Core tools: `init`, `act`, `recommend`, `search`.

## Current State

- **Version:** v0.2.5 (current branch)
- **Location:** `/Users/vncsleal/Code/openplan`
- **MCP Config:** `~/.config/opencode/opencode.json` — server "openplan", local python command
- **Tests:** 154 pass (`pytest tests/ -v`)
- **Database:** `~/.local/share/openplan/planner_v3.db` — 66+ projects with real data
- **Session ID:** UUID persisted in `meta` table, survives restarts
- **Loop:** `.venv/bin/pip install -e ".[dev]"` after any code change

## MCP Surface

| Primitive | Details |
|-----------|---------|
| **9 tools** | init, act, recommend, search, read_state, update_state, reconstruct, plan, compare_paths, optimize, tune, abandon, diagnose — `openplan_*` in opencode |
| **Resources** | `openplan://projects`, `{project}/graph`, `openplan://analytics` — zero token read |
| **Prompts** | `agent_loop` — full workflow instructions |
| **Notifications** | `notifications/resources/updated` on graph mutations |

## Architecture

```
src/openplan/
├── server.py                 # MCP dispatch, RW lock, cursor, resources, prompts
├── config.py                 # Config loader with env var fallback
├── core/
│   ├── state.py              # init (goal+project_type), act (preconditions+postconditions), abandon
│   ├── graph.py              # search (token-level), diagnostics, scoring, graph health
│   ├── recommend.py          # goal-oriented A* + fallback to activation scoring
│   ├── planner.py            # A* pathfinding with precondition checks + predictive cost baselines
│   ├── activation.py         # Activation heuristic
│   ├── analytics.py          # Cross-project anomaly detection + self_diagnose()
│   ├── read.py               # read_state, update_state, reconstruct
│   ├── reasoning.py          # ReasoningPayload dataclass
│   ├── insight_propagation.py # Cross-project insight propagation (embedding/FTS5/LIKE)
│   ├── telemetry.py          # Usage tracking, suggestion conversion
│   ├── maintenance.py        # Background daemon
│   ├── embedding.py          # fastembed provider + NumPy cache + ANN
│   ├── export.py             # export + compress
│   ├── rlhf.py               # OpenCode RLHF data correlation
│   └── errors.py             # Error hierarchy (incl. PreconditionError, TerminalStateError, GoalNotFoundError)
├── db/
│   ├── schema.py             # SQLite schema + FTS5 + triggers + cost_baselines + self_diagnostics
│   └── connection.py         # WAL-mode connection
└── tools/
    └── definitions.py        # Tool schemas with outputSchema
```

## Key Files

| File | Lines | What |
|------|-------|------|
| `core/state.py` | 400+ | act() with precondition validation + postconditions, init_project() with goal/project_type, abandon() |
| `core/graph.py` | 555 | search(), observe(), diagnostics(), _graph_health(), _score_state(), _suggested_next_action() |
| `core/recommend.py` | 250+ | Two-phase recommend: goal-oriented A* first, then activation scoring fallback |
| `core/planner.py` | 330+ | plan() with A*, precondition edge filtering, risk-aware cost, predictive cost baselines |
| `core/activation.py` | 281 | ActivationContext class, _compute_activation, _compute_visit_ratio |
| `core/analytics.py` | 200+ | compute_analytics(), self_diagnose() with cost drift detection |
| `core/read.py` | 382 | read_state, update_state, reconstruct, compare_paths, optimize |
| `server.py` | 590+ | All MCP handlers including abandon, diagnose; updated prompt with goal/project_type |
| `db/schema.py` | 170+ | Added: goal/project_type/terminal columns on nodes, cost_baselines table, self_diagnostics table |
| `tools/definitions.py` | 310+ | Updated tool schemas: init(goal, project_type), act(postconditions), new abandon/diagnose tools |

## What Was Done (v0.2.5)

### Tier P0 — Goals & Preconditions/Postconditions
- [X] **Schema**: `nodes.goal TEXT`, `nodes.project_type TEXT`, `nodes.terminal INTEGER` columns (additive ALTER TABLE, zero data loss)
- [X] **`init` accepts `goal` and `project_type`** — goal describes the desired end state, project_type enables cost baselines
- [X] **`init` auto-updates existing projects** — re-calling init with a goal/project_type updates the root node
- [X] **Goal-oriented `recommend`** — When a goal is set (via init or passed as param), recommend finds the cheapest A* path from cursor to goal-aligned states. Falls back to activation scoring if no goal-aligned states are reachable.
- [X] **Precondition validation on `act`** — Edges with conditions JSON are validated before acting. Raises `PreconditionError` if field values don't match.
- [X] **Postconditions on `act`** — Optional dict merged into target state's props
- [X] **Terminal state check** — `act` raises `TerminalStateError` if source state is terminal

### Tier P1 — Predictive Costs & Risk-Aware Routing

- [X] **`cost_baselines` table** — Per-project-type, per-action cost averages tracked automatically on each act()
- [X] **Cost baseline auto-update** — Every `act()` records actual cost to `cost_baselines`, smoothing with existing samples
- [X] **Risk-aware `_get_edge_cost`** — Effective cost = `learned * (1 + cost_risk)`. Already existed, now properly integrated.
- [X] **Planner preconditions** — A* `plan()` calls `_meets_preconditions()` for each edge, skipping edges whose conditions aren't met

### Tier P2 — Branching & Self-Diagnose

- [X] **`abandon` tool** — Marks a state and all its descendants as `superseded`. Preserves history, excludes from recommendations.
- [X] **`diagnose` tool** — `self_diagnose()` in analytics.py checks: calibration rate, orphan rate, cost drift across top 100 edges. Stores results in `self_diagnostics` table.
- [X] **`self_diagnostics` table** — Stores metric, value, threshold, severity, detail for time-series tracking

## What Needs Doing Next

### For v0.3.0 (major)
1. **Workflow state machines** — Encode the agent loop as a state machine in the graph itself. States have preconditions (edges must be calibrated) and postconditions (edges created on completion). The graph enforces the workflow.
2. **Multi-thread cursors** — One graph, multiple cursors per project, per-agent sessions.
3. **Persistent suggestion history** — Adaptive weights reset on each recommend() call. Store full history for actual time-series learning.
4. **Goal completion detection** — Check if goal is satisfied (all goal-aligned states are done) and auto-mark project complete.

## Quick Commands

```bash
.venv/bin/python -m pytest tests/ -v      # run tests (154)
.venv/bin/python -m openplan.server       # start MCP server
.venv/bin/pip install -e ".[dev]"         # reinstall editable
sqlite3 ~/.local/share/openplan/planner_v3.db  # inspect DB
```
