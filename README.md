# OpenPlan v0.1

**AI-native state space planner. MCP server. Python.**

OpenPlan is a project planning **MCP server** built on a state space navigation model. Instead of managing tasks, milestones, and goals as separate concepts, OpenPlan treats everything as a **state** in a directed graph. The AI agent **observes**, **plans**, **acts**, **branches**, and **learns** — navigating the graph and calibrating edge weights from actual outcomes.

## How It Works

```
States — positions in the project's semantic space (each with activation 0-1)
Edges — typed transitions between states (each with cost, probability, weight history)
Plans — optimal A* paths through the graph (cost × risk tradeoff)
Learning — automatic edge weight adjustment after every act
```

### The Agent Loop

```
observe → plan → branch(clarify) → act(traverse) → learn(calibrate) → repeat
```

## Tools (10)

| Tool | Purpose |
|------|---------|
| `init` | Bootstrap a project (idempotent) |
| `observe` | View frontier states or search by query |
| `act` | Traverse an edge between states |
| `branch` | Declare a decision point with options |
| `plan` | Find optimal A* path through the graph |
| `learn` | Calibrate edge weights from outcome |
| `diagnostics` | Graph health metrics |
| `export` | Export as JSON / adjacency matrix / GraphML |
| `project_list` | Discover known projects |
| `compress` | Archive old events, merge orphan states |

## Quick Start

```bash
pip install openplan-v3

# Start the MCP server
openplan-v3-server
```

Configure in `~/.config/openplan/config.json`:

```json
{
  "db_path": "/Users/me/.local/share/openplan/planner_v3.db",
  "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
  "learning": {"smoothing_factor": 0.3, "min_acts_for_calibration": 3}
}
```

### OpenAI / Anthropic Function Calling

```json
{
  "project": "my-app",
  "label": "Build auth system"
}
```

## Data Model

```
nodes: id, label, activation, frontier, project, props
edges: source_id, target_id, action, cost_tokens, cost_risk, prob, weight_history
events: id, project, node_id, event_type, payload, version, idempotency_key, session_id
events_archive: same schema, for compressed history
state_embeddings: id, label, embedding (384-dim float32), model, props_hash
```

## Architecture

```
  MCP transport (stdio)
       │
  server.py (thin dispatch)
  ┌──────┴──────┐
  │             │
  core/         adapters/
  ├─ graph.py   ├─ mcp/ (tool defs)
  ├─ activation ├─ db/ (connection, schema)
  ├─ embedding  └─ ...
  └─ ...
```

- **Shell imports core. Core never imports shell.**
- SQLite with WAL mode, foreign keys, savepoints for atomicity
- RW lock: concurrent readers, serialized writers
- Activation computed from in-degree, frontier ratio, recency, and agent boost
- Embeddings via fastembed ONNX (optional), with ANN fallback via sqlite-vec
- A* planner with bimodal heuristic (cross-cluster via embedding distance, within-cluster via min edge cost)

## Deterministic Guarantees

| Guarantee | Mechanism |
|-----------|-----------|
| Design documented before complex work | `branch()` requires explicit options |
| No cycles on transition | Recursive CTE check before `act()` |
| Idempotent mutations | SHA-256 idempotency keys on all events |
| Atomic writes | Savepoints on all mutation tools |
| State transition validity | Edge existence + action verb check |
| Evidence tracking | Evidence and thought recorded on every act |
| Learning from outcomes | Calibrated edges with weight_history |

## Testing

```bash
pip install openplan-v3[dev]
pytest tests/ -v
# 55 tests: act, activation, branch, compress, diagnostics,
# embedding, export, learn, observe, plan, scale
```

## Why State Space Navigation?

OpenPlan v2 asked the AI to translate its thinking into human structures (tasks, milestones, goals). v3 meets the AI where it is — a state space navigator. The core abstraction is a directed graph of states with probabilistic edges, not discrete units of work. Planning is A* pathfinding. Learning is automatic weight calibration. The AI doesn't manage workflows — it navigates a space.

## License

MIT
