# OpenPlan v0.8.1 — Agent Instructions

## Identity

OpenPlan is an MCP server for AI-native state space planning. Lives at `/Users/vncsleal/Code/openplan`. Python package `openplan`, version `0.8.1`.

## MCP Primitives

- **4 tools**: init, act, recommend, export
- **Resources**: `openplan://projects`, `openplan://analytics`, `openplan://tuning`, `{p}/graph`, `{p}/edges`, `{p}/health`
- **Prompts**: `agent_loop`, `feature-plan`, `debug-blocked`, `review-progress`
- **Notifications**: `notifications/resources/updated` on graph mutations

## Commands

- `.venv/bin/python -m pytest tests/ -v` — run 165 tests
- `.venv/bin/python -m openplan.server` — start MCP server (stdio)
- `.venv/bin/pip install -e ".[dev]"` — install dev dependencies

## Architecture

| Module | Responsibility |
|--------|---------------|
| `server.py` | MCP dispatch, RW lock, cursor, resources, prompts |
| `core/state.py` | init, act, branch, savepoints, auto-calibrate, prune |
| `core/graph.py` | search, diagnostics, scoring, graph health |
| `core/recommend.py` | recommend + cross-project + adaptive weights |
| `core/activation.py` | activation heuristic (in-degree, frontier, recency, boost, visit) |
| `core/planner.py` | A* pathfinding with bimodal heuristic + visible effective costs |
| `core/maintenance.py` | background daemon (diagnostics, compress, telemetry flush) |
| `core/analytics.py` | cross-project anomaly detection + health trends |
| `core/export.py` | export (JSON/GraphML/matrix) + prune + compress |
| `core/learnings.py` | cross-project learning patterns with variability analysis |
| `core/estimator.py` | project cost estimation from historical baselines |
| `core/retro.py` | planned vs actual cost comparison |
| `core/embedding.py` | fastembed provider + NumPy cache + ANN |
| `core/telemetry.py` | usage tracking, suggestion conversion |
| `db/schema.py` | SQLite schema + FTS5 + triggers + migrations |

## Key Rules

1. Shell imports core. Core never imports shell.
2. Savepoints for atomicity on all write tools.
3. Cursor persists via sessions table + UUID meta key.
4. Background daemon runs every 5 minutes (diagnostics, compress, propagate).
5. Adaptive weights adjust scoring based on suggestion conversion rate.
6. MCP Resources for zero-token graph access.
7. Options auto-sequence by default; use `parallel=true` for flat siblings.
8. Goal markers tick via `satisfies_goal` on verify or automatic label substring matching.
9. Evidence files are stat'd on verify; missing files get `status: unverified` with error metadata.
