from __future__ import annotations

from mcp.types import (
    Annotations as MCPAnnotations,
    Tool as MCPTool,
    ToolAnnotations as MCPToolAnnotations,
    ToolExecution as MCPToolExecution,
)


def t(
    name: str,
    title: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    outputSchema: dict | None = None,
    annotations: MCPToolAnnotations | None = None,
    execution: MCPToolExecution | None = None,
) -> MCPTool:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return MCPTool(
        name=name,
        title=title,
        description=description,
        inputSchema=schema,
        outputSchema=outputSchema,
        annotations=annotations,
        execution=execution,
    )


_READ_ONLY = MCPToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_READ_ONLY_NO_IDEMP = MCPToolAnnotations(readOnlyHint=True, destructiveHint=False)
_DESTRUCTIVE = MCPToolAnnotations(readOnlyHint=False, destructiveHint=True)
_DESTRUCTIVE_IDEMP = MCPToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)

_TOOLS: list[MCPTool] = [
        t(
            "init",
            "Initialize Project",
            "Create a new project context. Idempotent — returns the existing root state if the project already exists. Call this once to bootstrap before using act/recommend/search. Optionally set a project_type for cost baselines, and a goal describing the desired end state.",
            {
                "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
                "label": {"type": "string", "maxLength": 500, "description": "Optional root state label"},
                "project_type": {"type": "string", "maxLength": 100, "description": "Optional project type for cost baselines (e.g. 'python_cli', 'rust_library', 'web_app')"},
                "goal": {"type": "string", "maxLength": 500, "description": "Optional natural language description of the desired end state"},
            },
            ["project"],
            outputSchema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "state_id": {"type": "string"},
                    "label": {"type": "string"},
                    "project_type": {"type": "string"},
                    "goal": {"type": "string"},
                    "created": {"type": "boolean"},
                    "cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["ok", "state_id"],
            },
        ),
    t(
        "act",
        "Execute Action",
        "Traverse from your current position to a target. If the target state doesn't exist, it's created automatically. Records evidence, thought, and auto-calibrates the edge cost. This is the only tool that changes the graph. Use parent to create siblings under a specific state. Automatically marks the source state as 'done' and the target as 'in_progress'. Edges with preconditions (conditions JSON field) are validated before acting. Postconditions are stored on the target state's props.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "action": {"type": "string", "maxLength": 200, "description": "Action verb (implement, research, design, etc.)"},
            "target": {"type": "string", "maxLength": 500, "description": "Target label or state ID. If it doesn't exist, it's created."},
            "parent": {"type": "string", "maxLength": 20, "description": "Optional parent state ID. Creates the target as a child of this state instead of the cursor. The cursor still moves to the target."},
            "evidence": {"type": "string", "maxLength": 2048, "description": "Optional evidence URL or description"},
            "thought": {"type": "string", "maxLength": 10000, "description": "Optional reasoning"},
            "expected_cost": {"type": "object", "maxProperties": 10, "description": "Optional expected cost estimate"},
            "actual_cost": {"type": "object", "maxProperties": 10, "description": "Optional actual cost spent on this action. When provided, used for edge calibration. Keys: tokens (number), cost (number, optional)."},
            "postconditions": {"type": "object", "maxProperties": 20, "description": "Optional key-value pairs describing what becomes true after this action. Stored in the target state's props."},
        },
        ["project", "action"],
        annotations=_DESTRUCTIVE,
        outputSchema={
            "type": "object",
            "properties": {
                "next_state": {"type": "string"},
                "cursor": {"type": "string"},
                "cost_actual": {"type": "object"},
                "cost_delta": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            "required": ["next_state", "cursor"],
        },
    ),
        t(
            "recommend",
            "Recommend Best Target",
            "Analyze the graph to find the highest-value target and plan an optimal A* path to it. When a goal is set (via init or passed directly), uses goal-oriented planning. Without a goal, uses the activation+orphan scoring system.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug (optional; omit for cross-project)"},
            "goal": {"type": "string", "maxLength": 500, "description": "Optional natural language description of what to work on. Overrides the project's stored goal."},
            "max_cost": {"type": "number", "description": "Optional max cost constraint"},
            "cursor": {"type": "string", "maxLength": 20, "description": "Optional cursor override"},
        },
        annotations=_READ_ONLY,
        outputSchema={
            "type": "object",
            "properties": {
                "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "target_label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "explanation": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "path": {"type": "array"},
                "cost": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "plan": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "state_of_project": {"type": "object"},
                "cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
        },
    ),
    t(
        "search",
        "Search Knowledge",
        "Search across all projects for matching states, projects, and insights. Returns a combined result of projects, states, and stored learnings. Use this to find what you know, what projects exist, and what you've learned. When query is omitted, returns a complete project index. Optionally scope to a single project with the project parameter.",
        {
            "query": {"type": "string", "maxLength": 500, "description": "Search query (omit to list all projects)"},
            "project": {"type": "string", "maxLength": 200, "description": "Optional project slug to scope search"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20, "description": "Max states to return (default 20, max 200)"},
        },
        outputSchema={
            "type": "object",
            "properties": {
                "query": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "projects": {"type": "array"},
                "states": {"type": "array"},
                "insights": {"type": "array"},
                "count": {"type": "integer"},
            },
            "required": ["projects", "count"],
        },
    ),
    t(
        "read_state",
        "Read State",
        "Returns full state including parsed reasoning payload, edges, events, and metadata. Use this to deeply understand a single state's context.",
        {
            "state_id": {"type": "string", "maxLength": 20, "description": "State ID to read"},
        },
        ["state_id"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "state": {"type": "object"},
                "edges_out": {"type": "array"},
                "edges_in": {"type": "array"},
                "events": {"type": "array"},
            },
            "required": ["ok", "state"],
        },
    ),
    t(
        "update_state",
        "Update State",
        "Update reasoning, status, or arbitrary props on an existing state. This is the AI's mechanism for self-correction — updating a state when new information arrives. Reasoning fields merge into the existing props, preserving non-reasoning data (like visit_count).",
        {
            "state_id": {"type": "string", "maxLength": 20, "description": "State ID to update"},
            "status": {"type": "string", "enum": sorted(["pending", "in_progress", "done", "blocked", "superseded"]), "description": "New status (optional)"},
            "props_patch": {"type": "object", "maxProperties": 20, "description": "Partial reasoning payload or arbitrary props to merge"},
        },
        ["state_id"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "state_id": {"type": "string"},
                "updated_fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ok", "state_id"],
        },
    ),
    t(
        "reconstruct",
        "Reconstruct Project Context",
        "Returns a synthesized view of the project: where you are (cursor), how you got here (recent_path), what needs attention (frontier, blockers), what you learned (open_insights), and what to do next (next_target). Call this to rebuild your full mental model from scratch — no need to call recommend/search/plan separately.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "project": {"type": "string"},
                "cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "root": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "goal": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "tree": {"type": "array"},
                "recent_path": {"type": "array"},
                "frontier": {"type": "array"},
                "blockers": {"type": "array"},
                "open_insights": {"type": "array"},
                "next_target": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "project_health": {"type": "object"},
            },
            "required": ["ok", "project", "cursor", "project_health"],
        },
    ),
    t(
        "plan",
        "Plan Path",
        "Find the optimal path from your current position to a target state. Target can be a state ID (S-000042) or a natural language description resolved via embeddings. Returns path, expected cost, and traversal steps.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "target": {"type": "string", "maxLength": 500, "description": "Target state ID or natural language description"},
                "constraints": {
                    "type": "object",
                    "maxProperties": 10,
                    "description": "Optional constraints: max_cost, min_prob, expansion_limit, avoid_states, top_k, risk_adjustment",
                    "properties": {
                        "max_cost": {"type": "number"},
                        "min_prob": {"type": "number"},
                        "expansion_limit": {"type": "integer"},
                        "avoid_states": {"type": "array", "items": {"type": "string"}},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 5, "description": "Return top K alternative paths (default 1)"},
                        "risk_adjustment": {"type": "string", "enum": ["none", "probability", "variance"], "description": "How to adjust cost for edge probability"},
                    },
                },
        },
        ["project", "target"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "path": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "expected_cost": {"type": "object"},
                "traversal": {"type": "array"},
                "truncated": {"type": "boolean"},
                "high_uncertainty": {"type": "boolean"},
            },
            "required": ["ok", "path"],
        },
    ),
    t(
        "compare_paths",
        "Compare Paths",
        "Compare cost, risk, and steps across multiple target states from your current position. Results sorted cheapest first. Use this to decide between alternative approaches.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "targets": {"type": "array", "items": {"type": "string", "maxLength": 500}, "minItems": 1, "maxItems": 10, "description": "State IDs or natural language targets to compare"},
        },
        ["project", "targets"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "results": {"type": "array"},
                "count": {"type": "integer"},
                "from": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["ok", "results"],
        },
    ),
    t(
        "optimize",
        "Optimize Project Order",
        "Find the optimal traversal order for all remaining (non-done, non-blocked) states that minimizes total cost. Uses greedy nearest-neighbor heuristic. Returns ordered list with cumulative cost.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "optimal_order": {"type": "array"},
                "total_cost": {"type": "number"},
                "count": {"type": "integer"},
                "remaining_unreachable": {"type": "integer"},
                "from": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["ok", "optimal_order"],
        },
    ),
    t(
        "tune",
        "Tune System Parameters",
        "Analyze all edge calibration data and compute per-action statistics (avg cost, avg risk, success rate). Updates tunings in the meta table for the recommendation engine. Call periodically or after completing significant work.",
        {},
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "actions_tuned": {"type": "integer"},
                "recommendations": {"type": "object"},
            },
            "required": ["ok", "actions_tuned"],
        },
    ),
    t(
        "abandon",
        "Abandon Branch",
        "Mark a state and its descendants as 'superseded'. Dead ends and abandoned approaches are preserved for history but excluded from recommendations. Returns count of states affected.",
        {
            "state_id": {"type": "string", "maxLength": 20, "description": "Root state ID of the branch to abandon"},
        },
        ["state_id"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "state_id": {"type": "string"},
                "states_affected": {"type": "integer"},
            },
            "required": ["ok", "state_id"],
        },
    ),
    t(
        "set_goal",
        "Set Project Goal",
        "Set or update the project's goal — a natural language description of the desired end state. Used by plan() and optimize() to find the optimal path to project completion.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "goal": {"type": "string", "maxLength": 1000, "description": "Natural language description of the goal"},
            "target_state_id": {"type": "string", "maxLength": 20, "description": "Optional target state ID for the goal"},
        },
        ["project", "goal"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "project": {"type": "string"},
                "goal": {"type": "string"},
            },
            "required": ["ok", "project"],
        },
    ),
    t(
        "diagnose",
        "Self-Diagnose System",
        "Run system diagnostics and store results in the self_diagnostics table. Checks: calibration accuracy, orphan rates, stale states, cost drift, recommendation conversion. Returns issues found.",
        {},
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "issues_found": {"type": "integer"},
                "issues": {"type": "array"},
            },
            "required": ["ok", "issues_found"],
        },
    ),
    t(
        "tree",
        "Tree Visualization",
        "Return a tree view of the state graph from a given state. Shows parent-child relationships with activation, status, and edge info. Accepts state_id or project for root discovery.",
        {
            "state_id": {"type": "string", "maxLength": 20, "description": "Starting state ID. Defaults to project root."},
            "project": {"type": "string", "maxLength": 200, "description": "Project slug (needed when state_id is omitted)."},
            "depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3, "description": "Maximum depth to traverse (default 3)."},
            "up_depth": {"type": "integer", "minimum": 0, "maximum": 5, "default": 0, "description": "How many parent levels to show above the starting state (default 0)."},
            "format": {"type": "string", "enum": ["json", "ascii"], "default": "json", "description": "Output format: json or ascii tree."},
            "include_activation": {"type": "boolean", "default": True, "description": "Include activation scores."},
            "include_status": {"type": "boolean", "default": True, "description": "Include status values."},
            "include_edges": {"type": "boolean", "default": False, "description": "Include edge info (action, cost)."},
        },
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "format": {"type": "string"},
                "tree": {"anyOf": [{"type": "object"}, {"type": "string"}, {"type": "null"}]},
                "node_count": {"type": "integer"},
                "max_depth": {"type": "integer"},
                "branching_factor": {"type": "number"},
            },
            "required": ["ok", "format", "tree"],
        },
    ),
    t(
        "compress",
        "Compress Project History",
        "Archive old events and merge low-activation orphan states. Keeps the active graph manageable by moving stale data to the events_archive table.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "older_than_days": {"type": "integer", "default": 30, "minimum": 1, "maximum": 365, "description": "Archive events older than this many days (default 30)."},
            "merge_orphans": {"type": "boolean", "default": True, "description": "Merge low-activation orphans into the most active state."},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "archived_events": {"type": "integer"},
                "deleted_events": {"type": "integer"},
                "merged_orphans": {"type": "integer"},
            },
            "required": ["ok"],
        },
    ),
    t(
        "prune",
        "Prune Completed Subtree",
        "Collapse done/leaf states into a single summary state. Reduces graph complexity by archiving completed subgraphs. Only works when all descendants are 'done' or 'superseded'.",
        {
            "state_id": {"type": "string", "maxLength": 20, "description": "Root of the subtree to prune. All descendants must be 'done' or 'superseded'."},
            "summary_label": {"type": "string", "maxLength": 200, "description": "Label for the summary state. Defaults to 'Pruned: <original_label>'."},
            "keep_events": {"type": "boolean", "default": False, "description": "Preserve individual events in the events table."},
        },
        ["state_id"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "state_id": {"type": "string"},
                "summary_label": {"type": "string"},
                "collapsed_nodes": {"type": "integer"},
                "collapsed_edges": {"type": "integer"},
            },
            "required": ["ok", "state_id"],
        },
    ),
    t(
        "simulate",
        "Forward Simulation",
        "Simulate a sequence of actions without mutating state. Returns expected costs, probabilities, and risk metrics for each step. Use this to compare strategies before committing to an action.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "sequence": {"type": "array", "items": {"type": "object", "properties": {"action": {"type": "string"}, "target": {"type": "string"}}, "required": ["target"]}, "minItems": 1, "maxItems": 10, "description": "Sequence of action steps to simulate"},
            "cursor": {"type": "string", "maxLength": 20, "description": "Optional starting cursor. Defaults to current cursor or project root."},
        },
        ["project", "sequence"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "project": {"type": "string"},
                "from": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "trajectory": {"type": "array"},
                "total_cost": {"type": "number"},
                "cumulative_prob": {"type": "number"},
                "steps": {"type": "integer"},
            },
            "required": ["ok", "project", "trajectory"],
        },
    ),
    t(
        "compare_states",
        "Compare Two States",
        "Compare two states side-by-side. Returns a diff of their props, status, activation, edges, events, and embedding similarity (when available).",
        {
            "state_a": {"type": "string", "maxLength": 20, "description": "First state ID"},
            "state_b": {"type": "string", "maxLength": 20, "description": "Second state ID"},
        },
        ["state_a", "state_b"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "state_a": {"type": "object"},
                "state_b": {"type": "object"},
                "props_diff": {"type": "object"},
                "status_diff": {"type": "object"},
                "activation_diff": {"type": "object"},
                "edges_diff": {"type": "object"},
                "embedding_similarity": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            },
            "required": ["ok", "state_a", "state_b"],
        },
    ),
]


def get_tools() -> list[MCPTool]:
    return _TOOLS
