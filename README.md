# OpenPlan

**AI-native state space planner. MCP server. Python. 4 tools.**

OpenPlan is an MCP server that gives AI agents a structured planning and memory system. Instead of tasks and milestones, everything is a **state** in a directed graph with probabilistic edges, auto-calibrating costs, and A* pathfinding that tells the agent where to go next.

## Tools (4)

`init` — Create a new project context (idempotent). Accepts `project_type` for cost baselines and `goal` for tracked achievement markers.

`act` — The only mutation tool. Traverses edges, creates branches (auto-sequenced by default, `parallel=True` for fan-out), sets status, attaches evidence (with filesystem verification), prunes subtrees, reverts, and verifies goal satisfaction.

`recommend` — Returns the best next target with an A* path, confidence intervals, effective costs, project health, goal progress, cross-project estimation by type, and self-tuning bandit state.

`export` — Export the full graph as JSON, GraphML, or adjacency matrix.

## Quick Start

```bash
pip install -e ".[dev]"

# Start the MCP server
openplan-server
```

Configure MCP in your opencode.json / claude_desktop_config.json:

```json
{
  "mcp": {
    "openplan": {
      "type": "local",
      "command": ["/path/to/.venv/bin/python", "-m", "openplan.server"],
      "cwd": "/path/to/openplan"
    }
  }
}
```

## Architecture

```
  MCP transport (stdio)
       │
  server.py (dispatch)
  ┌──────┴──────┐
  core/         db/
  ├─ state.py   ├─ connection.py
  ├─ graph.py   ├─ schema.py
  ├─ planner.py ├─ ...
  ├─ activation.py
  ├─ embedding.py
  ├─ export.py
  ├─ recommend.py
  ├─ telemetry.py
  └─ maintenance.py
```

**Shell imports core. Core never imports shell.**
SQLite with WAL mode, foreign keys, savepoints. RW lock for concurrency.

## Data Model

```
nodes:    id, label, activation, frontier, project, props, parent_id, status, project_type
edges:    source_id, target_id, action, cost_tokens, cost_risk, prob, weight_history
events:   id, project, node_id, event_type, payload, version, idempotency_key, session_id
goal_markers: project, criterion, achieved, achieved_by
evidence: id, project, state_id, evidence_type, uri, status, metadata (size, mtime)
```

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
