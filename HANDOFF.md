# OpenPlan v0.2.2 — Session Handoff

**Date:** 2026-06-11  
**Next session should read this first.**  

## What OpenPlan Is

MCP server for AI-native state space planning. Python, SQLite. Single agent navigates a directed graph with probabilistic edges, auto-calibrating costs, and a recommendation engine. 4 tools: `init`, `act`, `recommend`, `search`.

## Current State

- **Version:** v0.2.2 (branch `feature/v0.2.2-polish`)
- **Location:** `/Users/vncsleal/Code/openplan`
- **MCP Config:** `~/.config/opencode/opencode.json` — server "openplan", local python command
- **Tests:** 112 pass (`pytest tests/ -v`)
- **Database:** `~/.local/share/openplan/planner_v3.db` — 48 projects with real data
- **Session ID:** UUID persisted in `meta` table, survives restarts
- **Loop:** `.venv/bin/pip install -e ".[dev]"` after any code change

## MCP Surface

| Primitive | Details |
|-----------|---------|
| **4 tools** | init, act, recommend, search — `openplan_*` in opencode |
| **Resources** | `openplan://projects`, `{project}/graph`, `openplan://analytics` — zero token read |
| **Prompts** | `agent_loop` — full workflow instructions |
| **Notifications** | `notifications/resources/updated` on graph mutations |

## Architecture

```
src/openplan/
├── server.py                 # MCP dispatch, RW lock, cursor, resources, prompts
├── config.py                 # Config loader with env var fallback
├── core/
│   ├── state.py              # init, act, savepoints, auto-calibrate, prune
│   ├── graph.py              # search (token-level), diagnostics, scoring, graph health
│   ├── recommend.py          # recommend + cross-project + adaptive weights
│   ├── planner.py            # A* pathfinding
│   ├── activation.py         # Activation heuristic
│   ├── analytics.py          # Cross-project anomaly detection + health trends
│   ├── insight_propagation.py # Cross-project insight propagation (embedding/FTS5/LIKE)
│   ├── telemetry.py          # Usage tracking, suggestion conversion
│   ├── maintenance.py        # Background daemon
│   ├── embedding.py          # fastembed provider + NumPy cache
│   ├── export.py             # export + compress
│   ├── rlhf.py               # OpenCode RLHF data correlation
│   └── errors.py             # Error hierarchy
├── db/
│   ├── schema.py             # SQLite schema + FTS5 + triggers
│   └── connection.py         # WAL-mode connection
└── tools/
    └── definitions.py        # Tool schemas with outputSchema
```

## Key Files

| File | Lines | What |
|------|-------|------|
| `core/state.py` | 307 | act(), init_project(), _ensure_node, _increment_visit, _auto_calibrate, _prune_stale_branches, _detect_cycle, savepoints |
| `core/graph.py` | 484 | search(), observe(), diagnostics(), _graph_health(), _score_state(), _suggested_next_action(), _observe_search() |
| `core/recommend.py` | 205 | recommend(), recommend_all(), adaptive weights with conversion rate |
| `core/planner.py` | 326 | plan() with A*, _get_edge_cost(), learn() with calibration |
| `core/activation.py` | 276 | ActivationContext class, _compute_activation, _compute_visit_ratio |
| `core/analytics.py` | 98 | compute_analytics(), z-score anomaly detection, health trends |
| `core/insight_propagation.py` | 100 | propagate() with embedding/FTS5/LIKE fallback |
| `core/maintenance.py` | 65 | _run_cycle(), start_background_maintenance() |
| `core/telemetry.py` | 168 | TelemetryTracker, get_global_conversion_rate(), flush/reload from events |
| `core/rlhf.py` | ~80 | fetch_opencode_session(), correlate_events(), build_rlhf_dataset() |
| `server.py` | 423 | All MCP handlers, resources, prompts, cursor, notifications |
| `db/schema.py` | 132 | nodes, edges, events, sessions, cross_project_insights, meta tables |
| `scripts/rlhf_pipeline.py` | ~80 | CLI entry for RLHF dataset generation |

## What Was Done (v0.2.2)

- [X] **Token-level search** — `search()` now uses token intersection scoring (`_tokenize`, `_token_score`). Falls back to `LIKE` if no token matches. `search("WebSocket gorilla")` finds "Implement WebSocket hub with gorilla/websocket".
- [X] **Test coverage** — 57 new tests across `test_analytics.py` (19), `test_insight_propagation.py` (8), `test_maintenance.py` (7), `test_telemetry.py` (24). 112 total.
- [X] **RLHF pipeline** — `scripts/rlhf_pipeline.py` + `src/openplan/core/rlhf.py`. Fetches opencode session messages, correlates with OpenPlan events by timestamp window, outputs JSON dataset.

## What Needs Doing Next

### For v0.3.0 (major)
1. **Workflow state machines** — Encode the agent loop as a state machine in the graph itself. States have preconditions (edges must be calibrated) and postconditions (edges created on completion). The graph enforces the workflow.
2. **Multi-agent** — One graph, multiple cursors per project, per-agent sessions.
3. **Persistent suggestion history** — Adaptive weights reset on each recommend() call. Store full history for actual time-series learning.

## Quick Commands

```bash
.venv/bin/python -m pytest tests/ -v      # run tests (112)
.venv/bin/python -m openplan.server       # start MCP server
.venv/bin/pip install -e ".[dev]"         # reinstall editable
.venv/bin/python scripts/rlhf_pipeline.py --help  # RLHF pipeline
sqlite3 ~/.local/share/openplan/planner_v3.db  # inspect DB
```
