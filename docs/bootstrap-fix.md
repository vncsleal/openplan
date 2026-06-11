# OpenPlan v3 Bootstrap Fix

## Problem

OpenPlan v3 has 6 MCP tools — `observe`, `act`, `branch`, `plan`, `learn`, `export` — and a design philosophy that "CRUD is implicit." No `create` tool exists.

This breaks on day one: `act` and `branch` both require an existing source state, but no tool can create the first one. The only way to seed a project is a raw SQLite INSERT (the SQLite bootstrap workaround).

## Root Cause

`_ensure_node` exists in `graph.py` and creates orphan nodes — but it's not exposed as an MCP tool. A separate `init` tool was added but has two bugs:

1. **Not truly idempotent:** If a previous attempt created a row in `nodes` but the edge/event wasn't committed, a second call hits `UNIQUE constraint failed: nodes.id` because the ID generator doesn't skip or check existing.

2. **Orphan node detection doesn't exist:** A node can exist in `nodes` without any `edges` referencing it. `observe(scope=all)` returns it (it queries by project), but `observe(scope=frontier)` skips it because `is_frontier` requires activation state. An orphan root node with `is_frontier = 0` and `activation = 0.0` falls through all views.

## Solution: Idempotent `init` tool

A dedicated `init` tool is the right approach — not merging into `act`. Two reasons:

1. **Schema integrity:** `act` requires `["state", "action"]` in its JSON Schema. Making `state` optional for one special case breaks validation — the MCP validates against the schema before the server code runs, so `action="init"` never gets checked.

2. **Single responsibility:** `init` bootstraps a project. `act` transitions between existing states. They're semantically different, and the MCP surface benefits from explicit naming.

### Changes required

#### `tools/definitions.py` — add `init` tool

```python
t(
    "init",
    "Bootstrap a project by creating its first state. "
    "Idempotent — returns the existing root state if the project already has states.",
    {
        "project": {"type": "string", "maxLength": 200, "description": "Project slug to initialize"},
        "label": {"type": "string", "maxLength": 200, "description": "Label for the root state", "default": "Root"},
    },
    ["project"],
),
```

#### `core/graph.py` — make `_ensure_node` idempotent

```python
def _ensure_node(
    project: str,
    label: str,
    conn: sqlite3.Connection,
    id_hint: str | None = None,
) -> str:
    # If a hint is provided and the node already exists, return it
    if id_hint:
        existing = conn.execute("SELECT id FROM nodes WHERE id = ?", (id_hint,)).fetchone()
        if existing:
            return existing["id"]

    sid = generate_id(project, conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
    conn.execute(
        "INSERT INTO nodes (id, label, project, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (sid, label, project, now, now),
    )
    return sid
```

#### `server.py` — add `init` handler

```python
async def _handle_init(args: dict) -> CallToolResult:
    project = args["project"]
    label = args.get("label", "Root")

    # Check if project already has states
    existing = _conn.execute(
        "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()
    if existing:
        return ok({"data": {"state": existing["id"], "label": label, "project": project, "created": False}})

    root_id = _ensure_node(project, label, _conn)
    return ok({"data": {"state": root_id, "label": label, "project": project, "created": True}})


_HANDLERS: dict[str, Callable] = {
    "observe": _handle_observe,
    "act": _handle_act,
    "init": _handle_init,
    "export": _handle_export,
    "branch": _handle_branch,
    "plan": _handle_plan,
    "learn": _handle_learn,
}
```

## Result

```python
# Before: UNIQUE constraint crash on retry
openplan_init(project="quillby", label="Quillby MCP")
# → { ok: false, error: "UNIQUE constraint failed: nodes.id" }

# After: idempotent, works every time
openplan_init(project="quillby", label="Quillby MCP")
# → { ok: true, state: "S-000007", label: "Quillby MCP", created: true }

openplan_init(project="quillby", label="Quillby MCP")  # retry
# → { ok: true, state: "S-000007", label: "Quillby MCP", created: false }

openplan_observe(project="quillby", scope="frontier")
# → returns root state as frontier

openplan_branch(state="S-000007", options=[...])
# → works normally
```
