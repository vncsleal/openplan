# OpenPlan

**AI-native state space planner. MCP server. Python. 4 tools.**

OpenPlan is an MCP server that gives AI agents a structured planning and memory system. Instead of tasks, milestones, and statuses, everything is a **state** in a directed graph with probabilistic edges, auto-calibrating costs, and a recommendation engine that tells the agent where to go next.

## Tools (4)

`init` — Create a new project context (idempotent)
`act` — Traverse from your position to a target. Auto-creates targets, auto-calibrates edges, records evidence and thought.
`recommend` — "What should I do?" Scores all reachable states across a project and returns the best target with an A* path plan.
`search` — "What do I know about X?" Full-text + insight search across all projects. Omit query to list all projects.

## How It Works

```
States — positions in the project's semantic space (activation 0-1)
Edges — typed transitions between states (cost, probability, weight history)
Planning — A* pathfinding with bimodal heuristic
Calibration — Every act auto-records weight_history; learn() provides explicit outcome
Maintenance — Background daemon runs diagnostics, auto-fix, compress, and telemetry flush
```

## Quick Start

```bash
pip install openplan

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

## Data Model

```
nodes:    id, label, activation, frontier, project, props
edges:    source_id, target_id, action, cost_tokens, cost_risk, prob, weight_history
events:   id, project, node_id, event_type, payload, version, idempotency_key, session_id
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
  ├─ planner.py └─ ...
  ├─ activation.py
  ├─ embedding.py
  ├─ export.py
  ├─ recommend.py
  ├─ telemetry.py
  └─ maintenance.py
```

**Shell imports core. Core never imports shell.**
SQLite with WAL mode, foreign keys, savepoints. RW lock for concurrency.

## Testing

```bash
pip install openplan[dev]
pytest tests/ -v
```

## License

MIT
