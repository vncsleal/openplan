from __future__ import annotations

from mcp.types import Tool as MCPTool


def t(
    name: str,
    title: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    outputSchema: dict | None = None,
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
    )


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
            "postconditions": {"type": "object", "maxProperties": 20, "description": "Optional key-value pairs describing what becomes true after this action. Stored in the target state's props."},
        },
        ["project", "action"],
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
        "Analyze the graph to find the highest-value target and plan an optimal A* path to it. When a goal is set (via init or passed directly), uses goal-oriented planning: finds the cheapest path from cursor to states matching the goal. Without a goal, uses the activation+orphan scoring system to find the best next state. When project is omitted, searches across all projects.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug (optional; omit for cross-project)"},
            "goal": {"type": "string", "maxLength": 500, "description": "Optional natural language description of what to work on. Overrides the project's stored goal."},
            "max_cost": {"type": "number", "description": "Optional max cost constraint"},
            "cursor": {"type": "string", "maxLength": 20, "description": "Optional cursor override"},
        },
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
                "goal": {"anyOf": [{"type": "string"}, {"type": "null"}]},
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
                "description": "Optional constraints: max_cost, min_prob, expansion_limit, avoid_states",
                "properties": {
                    "max_cost": {"type": "number"},
                    "min_prob": {"type": "number"},
                    "expansion_limit": {"type": "integer"},
                    "avoid_states": {"type": "array", "items": {"type": "string"}},
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
]


def get_tools() -> list[MCPTool]:
    return _TOOLS
