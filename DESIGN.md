# OpenPlan v0.1 — AI-Native Planner

**Version:** 0.1.0  
**Model:** State Space Navigation  
**Date:** 2026-06-02  
**Status:** Design Draft (revised after OpenCode review)  
**Target scale:** 5k+ nodes per project, 50k+ events

---

## 1. Mental Model

### What Changes From v2

OpenPlan v2 asks the AI to translate its thinking into human structures:

```
AI thought        → create task → start → complete → close milestone
(probabilistic,     (discrete,    (human    (human      (human gate)
 continuous,         linear,       workflow) workflow)
 embedding-based)    hierarchical)
```

OpenPlan v3 meets the AI where it is — a **state space navigator**. The core abstraction is not a "task" or "milestone" but a **directed graph of states** where:

- **States** are nodes with vector embeddings (semantic meaning of "what's going on")
- **Actions** are weighted edges between states (cost, probability, conditions)
- **Plans** are optimal paths through the graph (cost × risk tradeoff)
- **Learning** is weight adjustment on edges after execution

The AI doesn't create/start/complete tasks. It **observes** the state space, **plans** a path, **acts** to traverse an edge, and **learns** from the outcome.

### Why This Model

| Aspect | v2 (Human-Native) | v3 (AI-Native) |
|--------|-------------------|-----------------|
| **Primitive** | Task (discrete unit of work) | State (position in a semantic space) |
| **Navigation** | Workflow (backlog → active → done) | Transitions (matrix multiplication) |
| **Prioritization** | Priority labels (p1-p4) | Activation (heuristic from graph) |
| **Dependencies** | Hard edges (depends_on) | Probabilistic weights (P(unblock \| action)) |
| **Constraints** | Explicit gates (approach, evidence) | Cost functions (tokens, risk, value) |
| **Learning** | None (static weights) | Continuous (edge weights adjust per act) |
| **Tool surface** | 16 tools | 5-6 tools |
| **Output** | JSON rows | Embedding vectors + graph |

---

## 2. Core Concepts

### 2.1 State (Replaces "Node")

A state is a position in the project's semantic space. Every state has:

```
{
  "id": "S-00001",
  "label": "Implement JWT verify",
  "activation": 0.82,                       // scalar 0-1: how much this needs action
  "frontier": true,                         // activation > threshold AND has outgoing edges
  "props": {
    "description": "JWT token verification middleware",
    "acceptance": ["token verify", "expiry handling"],
    "cost_actual": 32000,                   // tokens spent to resolve this state
    "cost_estimated": 35000,
    "resolved_by": "ses_176c908a4ffe..."
  },
  "outgoing": [                              // cached materialized view of outgoing edges
    {"target": "S-00002", "cost": 15000, "prob": 0.85, "action": "implement"},
    {"target": "S-00003", "cost": 50000, "prob": 0.60, "action": "research"}
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

**Key differences from v2 nodes:**

- **No `type` field.** No "task" vs "milestone" vs "goal" — everything is a state. The semantic distinction comes from the embedding vector, not a label.
- **No `status` field.** Instead, `activation` is computed from a lightweight heuristic that scales to 5k+ nodes.
- **No `embedding` on the state.** Embeddings live in a separate index table (see 4.1). The state itself is embedding-agnostic.
- **`outgoing` is a cached view**, not the source of truth. Source of truth is the Edges table (see 4.1). The cache is invalidated on any edge mutation touching this state.

### 2.2 Action (Replaces "Edge Type" + "Transition")

An action is a typed transition between states:

```
{
  "source": "S-00001",
  "target": "S-00002",
  "action": "implement",                    // the verb of the transition
  "cost": {"tokens": 15000, "risk": 0.15, "uncertainty": 0.2},
  "prob": 0.85,                             // P(reaching target | taking action)
  "conditions": ["external_dep == resolved"],  // optional prerequisites
  "weight_history": [                        // how weight evolved over time
    {"at": "...", "cost": 20000, "prob": 0.7, "reason": "initial estimate"},
    {"at": "...", "cost": 15000, "prob": 0.85, "reason": "learn: actual lower"}
  ]
}
```

**Key differences from v2 edges:**

- **No `depends_on` / `parent_of` / `blocked_by` types.** Every edge is an `action` with a verb. The verb is the affordance — what can you do from this state? The type hierarchy disappears.
- **Cost is a vector, not a scalar.** Tokens, risk, uncertainty. The planner optimizes for a Pareto frontier, not a single metric.
- **Probability replaces binary dependency.** Instead of "A depends on B," it's "action A has P(0.85) of reaching state B." Low-probability edges model high-risk transitions.
- **Weight history enables learning.** Each `act` call appends to `weight_history`. The planner uses recent weights (last N) for its estimates.
- **Edges table is the sole source of truth.** The `outgoing` field on a state is a cached materialized view recomputed on edge mutations.

### 2.3 Activation (Replaces "Status")

Instead of a discrete status field (backlog / active / blocked / done), every state has an **activation scalar** `[0, 1]` computed from a **lightweight heuristic** — no PageRank or global graph iteration needed:

```
activation(s) = w₁ · in_degree_ratio(s)
              + w₂ · frontier_ratio(s)
              + w₃ · recency(s)
              + w₄ · agent_boost(s)
```

| Component | Weight | Formula | O() |
|-----------|--------|---------|-----|
| **in_degree_ratio(s)** | 0.40 | `min(in_degree(s) / max_in_degree, 1.0)` | O(1) per state |
| **frontier_ratio(s)** | 0.30 | `unresolved_outgoing(s) / total_outgoing(s)` or `0` if none | O(k) where k = outgoing edges |
| **recency(s)** | 0.20 | `1 - min(days_since_update / stale_days, 1.0)` | O(1) |
| **agent_boost(s)** | 0.10 | `1.0` if boosted, `0.5` otherwise | O(1) |

**Why not PageRank?**

PageRank adds global iteration cost, requires damping factor tuning, and doesn't meaningfully improve activation quality for a task graph. The heuristic above is O(1) per node and gives the same practical result: "what should I work on next?" PageRank is available as an opt-in `observe(scope="rank")` mode.

**No tool changes activation directly.** Activation emerges from graph structure and time. The agent doesn't "start a task" — it **observes** which states have high activation and acts on those.

**Frontier** is a cached boolean: `activation > 0.5 AND outgoing_edges > 0`. Recalculated on any `act` or `branch` touching that state.

**Circular dependency resolution:** Activation uses `frontier_ratio` which depends on `activation(target) > 0.5`. To avoid feedback loops, the cache invalidator computes activation in **sinks-first order** — states with no outgoing edges are computed first (frontier_ratio = 0), so their predecessors read stable values. This is a heuristic approximation of topological sort; a true topological sort would fail on graphs with cycles (e.g., blocked ↔ unblock), but the heuristic works because frontier_ratio only depends on direct successors, not the transitive closure.

### 2.4 Plan (Replaces "Suggest")

A plan is not a ranked list of backlog items. It's an **optimal path** through the state graph:

```
plan(target: "S-0010", from: "S-0001", constraints: {max_cost: 50000, min_prob: 0.8})
→ [
  {
    "path": ["S-0001", "S-0003", "S-0005", "S-0010"],
    "expected_cost": {"tokens": 45000, "risk": 0.12, "steps": 3},
    "traversal": [
      {"from": "S-0001", "action": "implement", "to": "S-0003", "prob": 0.85},
      {"from": "S-0003", "action": "research", "to": "S-0005", "prob": 0.90},
      {"from": "S-0005", "action": "implement", "to": "S-0010", "prob": 0.95}
    ]
  },
  {
    "path": ["S-0001", "S-0004", "S-0010"],
    "expected_cost": {"tokens": 52000, "risk": 0.08, "steps": 2},
    "traversal": [
      {"from": "S-0001", "action": "implement", "to": "S-0004", "prob": 0.70},
      {"from": "S-0004", "action": "implement", "to": "S-0010", "prob": 0.95}
    ]
  }
]
```

**Requires a `from` parameter** — the cursor representing the agent's current position (returned by the last `act` call). A* needs a start node. The agent loop (5.3) shows how `act` returns the next cursor.

The planner uses weighted A* search. Returns multiple candidates with cost/risk tradeoffs.

---

## 3. Tool Surface (5 Core + 1 Utility)

### 3.1 `observe`

Return the current state space. This is how the agent orients itself at session start.

```
observe(project: str, query: str | None, scope: "frontier" | "cluster" | "all" | "rank")
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `project` | Yes | Project slug |
| `query` | No | Natural language — find states similar to this description |
| `scope` | No | Default `frontier`. `rank` enables PageRank-based ranking (opt-in). |

**Returns:**

When `query` is provided (requires embedding provider configured):
```json
{
  "mode": "similarity",
  "results": [
    {"id": "S-0007", "label": "...", "similarity": 0.89, "activation": 0.82, "frontier": true},
    {"id": "S-0012", "label": "...", "similarity": 0.74, "activation": 0.31, "frontier": false}
  ]
}
```

When `scope: "frontier"`:
```json
{
  "mode": "frontier",
  "states": [/* states with activation > 0.5 AND outgoing edges > 0 */],
  "graph": {
    "density": 0.12,
    "avg_path_length": 4.3,
    "entropy": 0.67,
    "health": {
      "issues": [
        {"code": "HIGH_ORPHAN_COUNT", "severity": "high", "message": "...", "fix": "..."},
        {"code": "NO_CALIBRATION", "severity": "high", "message": "...", "fix": "..."}
      ],
      "orphan_count": 20,
      "calibration_count": 3,
      "action_types": 1
    }
  },
  "recommended": "S-0001"
}
```

The `health` field appears when structural issues are detected (orphans, no calibration, low action diversity, shallow graph). This is OpenPlan's self-improvement feedback — the agent sees it every time it observes, without needing a separate tool call. Each issue includes a `fix` describing how to resolve it.

The `recommended` field is the single highest-value next state — computed as `activation × (1 - uncertainty)`. This is what the planner would `act` on if the agent just says "go."

When query is provided but no embedding provider is configured, falls back to FTS5 (SQLite full-text search on labels).

### 3.2 `plan`

Find optimal paths through the state graph.

```
plan(target: str, from: str, constraints: dict | None)
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `target` | Yes | State ID or natural language target description |
| `from` | Yes | Current position cursor (returned by `act` or `observe.recommended`) |
| `constraints` | No | `{max_cost, min_prob, avoid_states[], expansion_limit}` |

- `expansion_limit` caps A* node expansions (default 500) to prevent unbounded search with weak heuristics.
- If `target` is a natural language string (not a state ID), `plan` first finds the nearest state via embedding similarity, then plans to it.
- **Fallback:** Without embeddings, `target` must be a state ID (error if not found). No natural language resolution.

### 3.3 `act`

Execute a transition from one state to another. This is the **only** tool that changes graph state.

```
act(state: str, action: str, target: str | None, evidence: str | None, thought: str | None, expected_cost: dict | None)
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `state` | Yes | Current state ID |
| `action` | Yes | The action verb |
| `target` | Yes* | Target state ID. Required if multiple outgoing edges have the same `action` verb. Optional if the action uniquely identifies the target. |
| `evidence` | No | URL, commit, or description of what was produced |
| `thought` | No | Agent reasoning (preferred over evidence) |
| `expected_cost` | No | Agent's cost estimate (compared to actual) |

**Returns:**
```json
{
  "next_state": "S-0003",
  "cursor": "S-0003",                       // pass to next plan() call
  "activation_delta": {"S-0003": +0.15, "S-0004": -0.05},
  "cost_actual": {"tokens": 32000, "risk": 0.12},
  "cost_delta": {"tokens": -3000, "risk": -0.03},
  "new_frontier": ["S-0005", "S-0007"]
}
```

**No explicit validation gates.** Evidence and thought are optional but recorded when provided.

**Disambiguation rule:** When `action` matches multiple outgoing edges and `target` is not provided, pick the edge with the highest `prob`. If there's a tie, pick the one with the lowest `cost_tokens`.

### 3.4 `branch`

Declare a decision point with multiple possible futures. This replaces v2's `approach` gate.

```
branch(state: str, options: list[option])
```

Where each option (label and action required):
```json
{
  "label": "Use native JWT library",
  "action": "implement",
  "prob": 0.8,
  "expected_cost": {"tokens": 30000, "risk": 0.1},
  "condition": "openapi spec exists"
}
```

**Returns:**
```json
{
  "branch_id": "B-0001",
  "options": 2,
  "states_created": ["S-0008", "S-0009"]
}
```

**Auto-boost:** New branched states get `agent_boost = 1.0` for their first 24 hours (or until an `act` touches them, whichever comes first). This prevents orphan states with zero in-degree from being invisible. Without auto-boost, a branched state starts with `in_degree_ratio = 0` → activation ~0.3-0.4 → below the 0.5 frontier threshold. The agent just declared these as important and they'd be hidden immediately.

`branch` creates new states for each option and links them as outgoing edges from the current state. The planner now sees these as traversal alternatives.

**This is the approach gate, but better.** v2 required `approach` text for p1/large tasks. v3 says: don't write prose — declare your decision space. The graph captures the structure of your reasoning, not the prose.

### 3.5 `learn`

Feed back actual outcomes to adjust edge weights. This is how OpenPlan improves over time.

```
learn(from_state: str, to_state: str, outcome: "success" | "partial" | "failure", actual_cost: float, insight: str | None)
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `from_state` | Yes | Source state of the traversed edge |
| `to_state` | Yes | Target state of the traversed edge |
| `outcome` | Yes | How it went |
| `actual_cost` | No | Actual token cost incurred (float) |
| `insight` | No | Free-form learning record |

The system resolves which specific edge was traversed by matching `(from, to, action)` against the Edges table and the most recent event from `state_id = from` with `event_type = "acted"`.

**Returns:**
```json
{
  "adjustments": [
    {"edge": "S-0001→S-0003", "action": "implement", "cost_old": 15000, "cost_new": 12000, "prob_old": 0.85, "prob_new": 0.90}
  ],
  "activation_shifts": [{"state": "S-0003", "delta": -0.12}],
  "embedding_shift": 0.03
}
```

The learning algorithm:

- If actual cost < expected cost → increase probability, decrease cost estimate
- If actual cost > expected cost → decrease probability, increase cost estimate
- If outcome = failure → significantly decrease probability, log insight
- Smoothing: weighted moving average with recent N acts having 3× weight

This makes OpenPlan a **self-calibrating system**. The first plan for a project is based on initial estimates. After 10 acts, the estimates reflect reality.

### 3.6 `export` (Utility)

Same as v2 — dump all data for audit/analysis.

```
export(format: "json" | "matrix" | "graphml")
```

- `json`: Full state space with edge history
- `matrix`: Adjacency matrix as 2D array (for NumPy analysis)
- `graphml`: For visualization tools

### 3.7 `diagnostics` (Utility)

Return graph health metrics for a project. Read-only — never mutates state.

```
diagnostics(project: str)
```

**Returns:**
```json
{
  "project": "quillby",
  "overview": {
    "states": 89, "edges": 92, "events": 45,
    "max_depth": 3, "root_states": 7,
    "leaf_states": 89, "orphan_count": 20
  },
  "health": {
    "calibrated_edges": 3, "calibration_rate": 0.033,
    "action_types": 1
  },
  "actions_used": [{"action": "branch", "cnt": 92}],
  "orphans": [
    {"id": "S-000074", "label": "PT-BR i18n plan", "activation": 0.7}
  ],
  "issues": [
    {
      "code": "HIGH_ORPHAN_COUNT",
      "severity": "high",
      "message": "20 states have no outgoing edges and are never acted upon",
      "fix": "Branch from each orphan state with actionable options, then act and learn"
    },
    {
      "code": "LOW_ACTION_DIVERSITY",
      "severity": "medium",
      "message": "Only 1 action type used (branch)",
      "fix": "Use domain verbs in branch() options"
    },
    {
      "code": "NO_CALIBRATION",
      "severity": "high",
      "message": "No edges have been calibrated",
      "fix": "Call learn() after each act()"
    }
  ]
}
```

Called by the agent loop when the frontier empties. Issues guide the next iteration's focus.

---

## 4. Data Model

### 4.1 Tables

The v2 schema is **extended**, not replaced. New fields are additive.

#### Nodes (States)

```
id              TEXT PK        # S-000001 prefix (unified across all states)
label           TEXT           # Display name (was "name")
activation      REAL DEFAULT 0 # Computed via heuristic (see 4.3)
frontier        INT DEFAULT 0  # Cached boolean flag (activation > 0.5 AND outgoing > 0)
project         TEXT           # Project slug
props           TEXT (JSON{})  # Everything else (actor_props, branching metadata, etc.)
created_at      TEXT (ISO 8601)
updated_at      TEXT (ISO 8601)
```

**No `embedding` here.** Embeddings live in the separate State Embedding Index table (below). The node is embedding-agnostic. This avoids dual-storage drift.

**Changes from v2:**
- `id` prefix changes — from `T-`/`M-`/`G-` to unified `S-`
- `name` → `label`
- Drops `type` — semantic type emerges from embedding space
- Drops `status` — replaced by `activation` + `frontier`
- No `embedding` field — lives in index table only

#### Edges (Source of Truth — NOT cached on state)

```
source_id       TEXT FK       # From state
target_id       TEXT FK       # To state
action          TEXT          # Verb: implement | research | review | deploy | etc
cost_tokens     REAL          # Token cost estimate
cost_risk       REAL          # Risk estimate 0-1
prob            REAL          # P(reaching target | action)
weight_history  TEXT (JSON[]) # [{cost, prob, timestamp, reason}]
created_at      TEXT
updated_at      TEXT
```

**Source of truth for all edge data.** The `outgoing` array on a State (section 2.1) is a cached materialized view, recomputed on mutations.

**Index:** `(source_id, action)` — used by `act` for disambiguation. `(target_id)` — used by activation's `in_degree_ratio`.

#### Events (Unchanged)

Same schema as v2. Every `act`, `branch`, and `learn` call produces events. Indexed on `(node_id, version)`. WAL mode for performance.

#### State Embedding Index (Separate — for vector operations only)

```
id          TEXT PK       # State ID (FK to Nodes)
label       TEXT          # Source text that was embedded
embedding   BLOB          # 384-dim float32 from fastembed ONNX
model       TEXT          # Embedding model used (e.g., "sentence-transformers/all-MiniLM-L6-v2")
props_hash  TEXT          # Hash of props at embedding time (for cache invalidation)
created_at  TEXT
```

**Purpose:** Populated by the embedding provider (fastembed). Queried by `observe(query)` for cosine similarity search. Brute-force at 5k nodes (~5ms in NumPy). At 10k+, ANN via optional sqlite-vec extension.

**Cache:** On `observe` or server startup, the full embedding matrix is loaded into memory as a NumPy `(N, 384)` float32 array. 5k nodes = ~7.5MB. 50k nodes = ~75MB — acceptable for a long-lived MCP process. Invalidated and reloaded on new state creation.

### 4.2 Adjacency Matrix (Mental Model)

The adjacency matrix `A` is an `n × n` sparse matrix where:

```
A[i][j] = estimated cost of transitioning from state i to state j
       = 0 if no edge exists
```

Never materialized as a 2D array. Constructed on demand by `plan` using adjacency list from Edges table.

### 4.3 Activation Computation

```
activation(s) = w₁ · in_degree_ratio(s)
              + w₂ · frontier_ratio(s)
              + w₃ · recency(s)
              + w₄ · agent_boost(s)
```

Default weights: `w = [0.4, 0.3, 0.2, 0.1]`

#### Component Details

**in_degree_ratio(s):** `min(in_degree(s) / max_in_degree_in_project, 1.0)`

How many edges point to this state, relative to the most-linked state. High in-degree = many paths lead here = important.

- `max_in_degree` is maintained as an **incremental counter** (updated on edge insert/delete, O(1)), not recomputed from scratch. Periodically validates with a full scan during quiet periods.
- Read: `SELECT COUNT(*) FROM edges WHERE target_id = s`

**frontier_ratio(s):** `unresolved_count / total_outgoing`, or `0` if `total_outgoing = 0`

What fraction of outgoing edges lead to unresolved targets (target activation > 0.5). High = this state is a gateway to undone work.

- **Circular dependency resolved:** Activation is computed in sinks-first order. States with fewer outgoing edges are computed first (sinks → sources), so predecessors read stable activation values for frontier_ratio.

**recency(s):** `1 - min(days_since_updated / stale_days, 1.0)`

- `stale_days` from config (default: 2)
- Boosts new states (auto-boost from `branch` sets `recency = 1.0` for 24h or until first `act`)

**agent_boost(s):** `1.0` if explicitly boosted, `0.5` otherwise

- Decays over time (same curve as recency)
- Auto-boost from `branch` uses this mechanism

#### Caching Strategy (Lazy, Sinks-First)

```python
activation_cache: dict[str, float] = {}
cache_order: list[str] = []  # computed on mark_dirty

def mark_dirty(state_id: str):
    """Call after act, branch, or edge change."""
    dirty.add(state_id)
    for neighbor in get_neighbors(state_id):
        dirty.add(neighbor)
    cache_order = sort(dirty, key=lambda s: len(outgoing_edges(s)))  # sinks first

def get_activation(state_id: str) -> float:
    if state_id in dirty:
        _recompute_all_dirty(cache_order)
    return activation_cache.get(state_id, 0.5)

def _recompute_all_dirty(order: list[str]):
    """Recompute in sinks-first order. Sinks computed first so
    predecessors read stable frontier_ratio values."""
    for sid in order:
        activation_cache[sid] = _compute_activation(sid)
    dirty.clear()
```

#### Scale Behavior

| Nodes | Activation compute (all dirty) | Cache memory |
|-------|--------------------------------|--------------|
| 100 | ~1ms | ~8KB |
| 1,000 | ~5ms | ~80KB |
| 5,000 | ~25ms | ~400KB |
| 50,000 | ~250ms | ~4MB |

---

## 5. Planner Architecture

### 5.1 Pathfinding

`plan` uses **A* with a bimodal heuristic**:

```
f(state) = g(state) + h(state)
g(state) = cumulative cost from start (tokens + risk penalty)
h(state) = heuristic remaining cost to target
```

**Bimodal heuristic:**

1. **Cross-cluster estimate:** If `cosine_sim(state_embedding, target_embedding) < 0.8`, use `embedding_distance × avg_edge_cost` — works well for long-range planning across different areas (auth → billing).

2. **Within-cluster fallback:** If `cosine_sim >= 0.8`, embeddings are too similar to be informative. Fall back to `min(cost_tokens)` of all outgoing edges — equivalent to Dijkstra within the cluster.

3. **Learned heuristic (Phase 4):** After N acts, edge weight adjustments from `learn` improve cost estimates. The heuristic learns from actual traversal data.

**Configurable constraints:**
- `expansion_limit: 500` — hard cap on A* node expansions. If hit, returns best path found so far with a `truncated: true` flag.
- `max_cost`, `min_prob` — filters paths by cumulative metrics.
- `top_k: 3` — returns top K distinct paths (at most 50% shared edges).

**Uncertainty-aware:** If a state has `prob < 0.5` on all outgoing edges, flags as "high uncertainty."

### 5.2 Target Resolution

If `target` is not a state ID:

1. Compute embedding of target description via fastembed (in thread pool executor)
2. Cosine similarity search against the in-memory embedding cache
3. If `similarity > 0.7`: plan to nearest existing state
4. If `similarity <= 0.7`: create a new state at the target embedding and plan to it (with auto-boost)

Without embedding provider: `target` must be a state ID. Natural language resolution returns an error.

### 5.3 The Agent Loop

```
cursor = observe(project).recommended   → "where should I start?"

loop:
  1. observe(project, scope="frontier")  → "where am I now?"
  2. if frontier is empty:
       if observe(scope="frontier").graph.health.issues:
         resolve highest-severity issue  → "graph has problems, fix them"
       else:
         done                             → "we're done here"
  3. plan(target=frontier[0], from=cursor) → "how do I get there?"
  4. if plan has high uncertainty: branch → "clarify approach"
  5. result = act(state=cursor, action=..., thought=...)
     → {next_state: s, cursor: s}        → "do the thing"
  6. learn(from_state=cursor, to_state=result.next_state, ...)
                                         → "what did we learn?"
  7. cursor = result.cursor               → "update position for next iteration"
```

The cursor is the agent's current position in the state graph. Every `act` returns `cursor` — the next state. Every `plan` requires `from` — the current cursor. This gives A* a concrete start node.

---

## 6. OpenCode Integration

### 6.1 Session Detection

v2 probed `GET /session` on port 4096. v3 expects the session ID as an **environment variable**:

```
OPENCODE_SESSION_ID=ses_176c908a4ffe8deuinuLBtwOqm
```

Passed by OpenCode when spawning the OpenPlan MCP server. No API probe, no race condition, no SQLite fallback.

### 6.2 Embedding Provider

**Decision: fastembed (Qdrant), not sentence-transformers.**

```python
# openplan/embedding/provider.py

import numpy as np
from numpy.typing import NDArray

class EmbeddingProvider:
    """Thin wrapper around fastembed ONNX model. Zero torch dependency."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name=model_name)
        self.dimensions: int = 384
        # Warmup: encode a single dummy string to load model + tokenizer
        self.model.embed(["warmup"])
        self._cache: dict[str, NDArray[np.float32]] = {}

    def encode(self, texts: list[str]) -> NDArray[np.float32]:
        """Returns (N, 384) float32 array. Must run in thread pool executor."""
        return np.array(list(self.model.embed(texts)), dtype=np.float32)

    def similarity(self, a: NDArray[np.float32], b: NDArray[np.float32]) -> float:
        return float(np.dot(a, b))  # cosine = dot since normalized
```

**Why fastembed over sentence-transformers ONNX?**

| Factor | fastembed | sentence-transformers[onnx] |
|--------|-----------|---------------------------|
| Package size | ~10-20MB | ~1GB+ (torch transitive dep) |
| Memory (runtime) | ~80MB | ~200MB (torch loaded anyway) |
| CPU speed | ~15ms/doc | ~15ms/doc |
| Async-safe | No (same issue) | No (same issue) |
| Warmup | ~2s (pure ONNX) | ~4-5s (torch init overhead) |

**Both block the event loop.** `model.encode()` is synchronous and must run in a thread pool executor:

```python
import asyncio
import concurrent.futures

executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

async def encode_async(texts: list[str]) -> NDArray[np.float32]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, provider.encode, texts)
```

**Config:**
```json
{
  "embedding": {
    "provider": "builtin",
    "model": "sentence-transformers/all-MiniLM-L6-v2",
    "dimensions": 384,
    "batch_size": 32
  }
}
```

Without embedding provider: `observe(query)` falls back to FTS5. `plan` with natural language target returns an error — requires state ID.

#### Embedding Cache

The full embedding matrix is loaded into memory on first `observe(query)` call:

```python
class EmbeddingCache:
    """In-memory (N, 384) float32 array for fast similarity search."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.matrix: NDArray[np.float32] | None = None
        self.state_ids: list[str] = []

    def load(self):
        rows = self.conn.execute(
            "SELECT id, embedding FROM state_embeddings ORDER BY id"
        ).fetchall()
        self.state_ids = [r[0] for r in rows]
        self.matrix = np.frombuffer(
            b"".join(r[1] for r in rows), dtype=np.float32
        ).reshape(len(rows), 384)

    def nearest(self, query_emb: NDArray[np.float32], k: int = 5):
        sims = self.matrix @ query_emb  # (N,) dot product
        idx = np.argsort(-sims)[:k]
        return [(self.state_ids[i], float(sims[i])) for i in idx]
```

At 50k nodes: ~75MB matrix. Load time ~500ms (50k BLOB reads + deserialize). Acceptable for `observe` (not on hot path). Incremental refresh on new states.

---

## 7. Comparison: v2 vs v3

### 7.1 What v3 Keeps From v2

| Feature | v2 | v3 |
|---------|----|----|
| SQLite backend | ✅ | ✅ (extended) |
| Events table | ✅ | ✅ (unchanged) |
| Graph model | ✅ | ✅ (simplified) |
| MCP transport | ✅ | ✅ |
| Project isolation | ✅ | ✅ |
| Export tool | ✅ | ✅ (enhanced formats) |
| Error hierarchy | ✅ | ✅ |
| Config | ✅ | ✅ (extended) |

### 7.2 What v3 Drops From v2

| Feature | v2 | v3 Reason |
|---------|----|-----------|
| type field on nodes | ✅ | Semantic type emerges from embedding |
| status field on nodes | ✅ | Replaced by activation scalar |
| depends_on / parent_of / blocked_by | ✅ | All edges are "action" with a verb |
| p1-p4 priority | ✅ | Replaced by activation + plan constraints |
| Start/complete/block/unblock/close_milestone | ✅ | Condensed into `act` |
| Approach gate | ✅ | Replaced by `branch` tool |
| Evidence gate | ✅ | Optional input to `act` |
| Milestone gate | ✅ | Emerges from graph (cluster of resolved states) |
| sentence-transformers | ✅ | Replaced by fastembed (zero torch) |

### 7.3 Tool Count

| v2 (16 tools) | v3 (5 core + 1 utility) |
|---------------|-------------------------|
| create | — (*) |
| get | observe |
| update | — (*) |
| search | observe (with query) |
| link | — (*) |
| unlink | — (*) |
| start | act |
| complete | act |
| block | act |
| unblock | act |
| close_milestone | — (**) |
| suggest | plan |
| status | observe (scope: frontier) |
| log | — (***) |
| export | export |
| backup | — (****) |

(*) CRUD is implicit — `act` and `branch` create/update states automatically.
(**) Milestones emerge from the graph.
(***) Every tool call records events automatically.
(****) Backup is an implementation detail.

---

## 8. Implementation Phases

### Phase 1 — MVP (3 tools, no embeddings, no pathfinding)

Core schema + activation heuristic + basic traversal. Delivers value over v2 immediately.

**What ships:**
- Nodes + Edges + Events tables (v3 schema)
- `act` tool (with target disambiguation, cursor return)
- `observe(scope="frontier")` — activation heuristic without embeddings
- `export` (JSON only)
- Activation: heuristic with sinks-first ordering, incremental max_in_degree
- FTS5 search on labels for `observe(query)` fallback

**Milestone:** Create states, observe frontier, traverse via `act`.

### Phase 2 — Branch & Plan

Decision trees and graph pathfinding.

**What ships:**
- `branch` tool (with auto-boost for new states)
- `plan` tool (state ID target only, bimodal A* fallback to Dijkstra)
- Cursor-based planning (from cursor → to target)
- Auto-boost: new branched states get boost for 24h or until first act

**Milestone:** Explore decision trees, find optimal paths.

### Phase 3 — Embeddings

Semantic observe and natural language planning.

**What ships:**
- fastembed integration (not sentence-transformers)
- Thread pool executor for blocking encode calls
- Embedding Cache (in-memory NumPy matrix, lazy load on first query)
- `observe(query)` with embedding similarity
- `plan` with natural language target resolution
- FTS5 fallback when embeddings unconfigured

**Milestone:** Semantic observe, natural language planning.

### Phase 4 — Learning

Self-calibrating system.

**What ships:**
- `learn` tool (from_state, to_state resolution)
- Weight history tracking on edges
- Calibration (cost delta calculation)
- A* with learned edge costs

**Milestone:** Path costs improve with use.

### Phase 5 — Scale

Production hardening at 5k-50k nodes.

**What ships:**
- PageRank opt-in (`observe(scope="rank")`)
- sqlite-vec for ANN at >10k nodes
- Events archival/rotation (summarized vs detailed)
- `export` with matrix/graphml formats
- v2 → v3 migration script
- Benchmarks at 5k/50k nodes

**Milestone:** Performant at scale.

### Nice-to-Have (v3.1+)

- Learning decay (older weight adjustments fade)
- Plan confidence scores
- Multi-session coordination
- Graphml visualization hook

---

## 9. Config

```json
{
  "version": 3,
  "db_path": "/Users/me/.local/share/openplan/planner_v3.db",
  "stale_days": 2,
  "plan_limit": 3,
  "expansion_limit": 500,
  "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
  "embedding": {
    "provider": "builtin",
    "model": "sentence-transformers/all-MiniLM-L6-v2",
    "dimensions": 384,
    "batch_size": 32
  },
  "learning": {
    "smoothing_factor": 0.3,
    "recent_weight": 3.0,
    "min_acts_for_calibration": 3
  },
  "page_rank": {
    "enabled": false,
    "iterations": 20,
    "damping": 0.85
  }
}
```

---

## 10. What's Not in Scope (Intentionally)

| Feature | Reason |
|---------|--------|
| UI / Dashboard | MCP server. Tools return JSON. |
| Multi-agent collaboration | Single-agent planner. Future work. |
| Cloud sync | Local-first. Backup via SQLite file copy. |
| Visualization | Export produces graphml. External tools render it. |
| Task assignment | Single-agent system. No "assignee" concept. |
| Time tracking | Activation captures recency, not hours. |

---

## 11. Known Design Risks (From OpenCode Review)

| Risk | Impact | Mitigation |
|------|--------|------------|
| **A* embedding heuristic weak within clusters** | Degrades to Dijkstra inside clusters | Bimodal heuristic (embedding distance for cross-cluster, min(cost) for within-cluster). Learned costs in Phase 4. |
| **Embedding load at 50k nodes is ~500ms-2s** | Slow first `observe(query)` | In-memory NumPy cache, lazy load, incremental refresh. |
| **fastembed blocks async event loop** | Latency spikes | Thread pool executor. Documented in provider code. |
| **Activation circular dependency** | Inconsistent cache | Topological order (sinks first). Documented in 4.3. |
| **Edges dual storage** | Cache divergence | Edges table is sole source of truth. `outgoing` on state is materialized view. |
| **`learn` without path IDs** | Agent can't reference edges | Simplified to `(from_state, to_state)` — system resolves the correct edge via events table. |

---

## 12. Testing Requirements

### Phase 1

| Test | What it validates |
|------|-------------------|
| `test_observe_frontier` | Returns states with activation > threshold + outgoing edges |
| `test_observe_query_fallback_fts5` | FTS5 fallback when no embedding configured |
| `test_act_transition` | Creates new state, records event, returns cursor |
| `test_act_disambiguation` | Picks correct edge when actions are duplicated |
| `test_act_invalid_no_action` | Rejected — no matching outgoing edge |
| `test_activation_heuristic` | Non-zero after graph mutation |
| `test_activation_decay` | Stale states have lower activation |
| `test_activation_consistency` | Consistent reads with circular deps |
| `test_export_json` | Returns valid full state dump |

### Phase 2

| Test | What it validates |
|------|-------------------|
| `test_plan_pathfinding` | Returns valid path from cursor to target |
| `test_plan_respects_constraints` | Filters paths exceeding max_cost or min_prob |
| `test_plan_expansion_limit` | Returns truncated flag when limit hit |
| `test_plan_high_uncertainty_flag` | Flags paths where edges have prob < 0.5 |
| `test_branch_creates_states` | Creates N new states for N options, with auto-boost |
| `test_branch_links_probabilities` | Edges have correct action, prob, and cost |

### Phase 3

| Test | What it validates |
|------|-------------------|
| `test_observe_query_embedding` | Finds nearest state by embedding similarity |
| `test_plan_natural_language_target` | Resolves non-ID target to nearest state |
| `test_embedding_provider_load` | Model loads, warmup succeeds, returns 384-dim |
| `test_embedding_provider_thread_pool` | Runs in executor without blocking |
| `test_embedding_cache_lazy_load` | Matrix loads on first query, not on startup |
| `test_embedding_cache_incremental_refresh` | New states are queryable without full reload |

### Phase 4

| Test | What it validates |
|------|-------------------|
| `test_learn_adjusts_weights` | Edge weight_history appended |
| `test_learn_calibrates_estimates` | Overestimates → cost increases |
| `test_learn_resolves_edge` | Correct edge matched from events table |

### Phase 5

| Test | What it validates |
|------|-------------------|
| `test_plan_5k_scale` | Returns in < 100ms with 5000 synthetic nodes |
| `test_activation_5k_scale` | Full recompute in < 50ms with 5000 nodes |
| `test_observe_query_50k_scale` | Embedding search in < 1s with 50k cache |
| `test_export_matrix_format` | Returns valid 2D array |
| `test_export_graphml_format` | Returns valid XML |
