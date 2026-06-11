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


_OBSERVE_OUTPUT: dict = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["frontier", "similarity", "all", "rank", "cluster"]},
        "states": {"type": "array", "items": {"type": "object"}},
        "recommended": {"type": "string"},
        "graph": {"type": "object"},
    },
}

_ACT_OUTPUT: dict = {
    "type": "object",
    "properties": {
        "next_state": {"type": "string"},
        "cursor": {"type": "string"},
        "activation_delta": {"type": "object"},
        "cost_actual": {"type": "object", "properties": {"tokens": {"type": "number"}, "risk": {"type": "number"}}},
        "cost_delta": {"type": "object", "properties": {"tokens": {"type": "number"}, "risk": {"type": "number"}}},
        "new_frontier": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["next_state", "cursor"],
}

_TOOLS: list[MCPTool] = [
    t(
        "init",
        "Initialize Project",
        "Initialise a project by creating its root state. "
        "Idempotent — returns the existing root state if the project already has states. "
        "Call this once per project to bootstrap before using branch/act/plan.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "label": {"type": "string", "maxLength": 500, "description": "Optional root state label (defaults to project slug)"},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "state_id": {"type": "string"},
                "label": {"type": "string"},
                "created": {"type": "boolean"},
            },
            "required": ["state_id", "created"],
        },
    ),
    t(
        "observe",
        "Observe State Space",
        "Observe the current state space. Returns frontier states (activation > threshold "
        "with outgoing edges), or search results by query.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "query": {"type": "string", "maxLength": 500, "description": "Search query — uses embedding similarity when available, falls back to FTS5"},
            "scope": {
                "type": "string",
                "enum": ["frontier", "all", "rank", "cluster"],
                "default": "frontier",
                "description": "Scope of observation",
            },
        },
        ["project"],
        outputSchema=_OBSERVE_OUTPUT,
    ),
    t(
        "act",
        "Execute State Transition",
        "Execute a transition between states. Validates the edge exists, detects cycles, "
        "records the action, auto-calibrates the edge's weight history, and returns the next cursor. "
        "When multiple edges match the same action, provide 'target' to disambiguate, or the "
        "highest-probability edge is selected.",
        {
            "state": {"type": "string", "maxLength": 20, "description": "Source state ID (S-XXXXXX format)"},
            "action": {"type": "string", "maxLength": 200, "description": "Action to execute"},
            "target": {"type": "string", "maxLength": 20, "description": "Optional target state ID to disambiguate"},
            "evidence": {"type": "string", "maxLength": 2048, "description": "Optional evidence for the transition"},
            "thought": {"type": "string", "maxLength": 10000, "description": "Optional reasoning/thought"},
            "expected_cost": {
                "type": "object",
                "maxProperties": 10,
                "description": "Optional expected cost estimate",
            },
        },
        ["state", "action"],
        outputSchema=_ACT_OUTPUT,
    ),
    t(
        "export",
        "Export Project Data",
        "Export full project data as JSON, adjacency matrix, or GraphML for visualization.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "format": {"type": "string", "default": "json", "enum": ["json", "matrix", "graphml"], "description": "Export format: json (full), matrix (adjacency), or graphml (visualization)"},
        },
        ["project"],
        outputSchema={
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "enum": ["json"]},
                        "project": {"type": "string"},
                        "nodes": {"type": "array"},
                        "edges": {"type": "array"},
                        "events": {"type": "array"},
                    },
                },
                {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "enum": ["matrix"]},
                        "project": {"type": "string"},
                        "sparse": {"type": "array"},
                    },
                },
                {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "enum": ["graphml"]},
                        "project": {"type": "string"},
                        "graphml": {"type": "string"},
                    },
                },
            ],
        },
    ),
    t(
        "branch",
        "Declare Decision Point",
        "Declare a decision point with multiple possible futures. "
        "Creates new states for each option and links them as outgoing edges. "
        "New states get an auto-boost for visibility.",
        {
            "state": {"type": "string", "maxLength": 20, "description": "Source state ID (S-XXXXXX format)"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Option label"},
                        "action": {"type": "string", "description": "Domain verb for the transition (implement, research, review, test, deploy, etc.)"},
                        "prob": {"type": "number", "description": "Probability of success 0-1"},
                        "expected_cost": {
                            "type": "object",
                            "properties": {
                                "tokens": {"type": "number", "description": "Expected token cost"},
                                "risk": {"type": "number", "description": "Expected risk 0-1"},
                            },
                        },
                    },
                    "required": ["label", "action"],
                },
                "description": "Array of option objects to branch into",
            },
        },
        ["state", "options"],
        outputSchema={
            "type": "object",
            "properties": {
                "branch_id": {"type": "string"},
                "options": {"type": "integer"},
                "states_created": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["branch_id", "states_created"],
        },
    ),
    t(
        "plan",
        "Find Optimal Path",
        "Find optimal paths through the state graph using A* with a bimodal heuristic. "
        "Returns the cheapest path from from_id to target_id respecting constraints. "
        "Uses learned costs when calibration data is available.",
        {
            "from_id": {"type": "string", "maxLength": 20, "description": "Current position cursor (S-XXXXXX format)"},
            "target_id": {"type": "string", "maxLength": 500, "description": "Target state ID (S-XXXXXX) or natural language description (resolved via embedding similarity)"},
            "constraints": {
                "type": "object",
                "maxProperties": 10,
                "description": "Optional constraints: max_cost, min_prob, avoid_states, expansion_limit",
            },
        },
        ["from_id", "target_id"],
        outputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "array", "items": {"type": "string"}},
                "expected_cost": {"type": "object"},
                "traversal": {"type": "array"},
                "truncated": {"type": "boolean"},
                "high_uncertainty": {"type": "boolean"},
                "resolved_target": {"type": "object"},
            },
        },
    ),
    t(
        "learn",
        "Calibrate Edge From Outcome",
        "Calibrate edge costs and probability from a past transition. "
        "Given an outcome (success/partial/failure), adjusts the edge's cost_tokens, "
        "probability, and weight_history using the smoothing factor.",
        {
            "from_state": {"type": "string", "maxLength": 20, "description": "Source state ID (S-XXXXXX format)"},
            "to_state": {"type": "string", "maxLength": 20, "description": "Target state ID (S-XXXXXX format)"},
            "outcome": {"type": "string", "enum": ["success", "partial", "failure"], "description": "How well the transition went"},
            "actual_cost": {"type": "number", "description": "The actual cost tokens incurred"},
            "insight": {"type": "string", "description": "Optional free-form notes about what was learned", "default": ""},
        },
        ["from_state", "to_state", "outcome", "actual_cost"],
        outputSchema={
            "type": "object",
            "properties": {
                "edge": {"type": "object"},
                "calibration": {"type": "object"},
                "activation_shifts": {"type": "array"},
            },
            "required": ["edge", "calibration"],
        },
    ),
    t(
        "diagnostics",
        "Graph Health Metrics",
        "Return graph health metrics for a project. Read-only, used by improvement sessions "
        "to assess orphan states, calibration rates, action diversity, and graph depth. "
        "Never modifies data.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "overview": {"type": "object"},
                "health": {"type": "object"},
                "actions_used": {"type": "array"},
                "event_types": {"type": "array"},
                "orphans": {"type": "array"},
                "orphan_count": {"type": "integer"},
                "issues": {"type": "array"},
            },
            "required": ["project"],
        },
    ),
    t(
        "project_list",
        "List All Projects",
        "List all known projects and their root states. "
        "Use this to discover available projects before calling observe/act.",
        {},
        outputSchema={
            "type": "object",
            "properties": {
                "projects": {"type": "array", "items": {"type": "string"}},
                "roots": {"type": "object"},
                "count": {"type": "integer"},
            },
            "required": ["projects", "count"],
        },
    ),
    t(
        "compress",
        "Archive and Compact",
        "Archive old events and optionally merge orphan states with low activation. "
        "Call periodically (every ~100 acts) to keep the graph performant. "
        "Archived events are moved to events_archive table.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "older_than_days": {"type": "number", "default": 30, "description": "Archive events older than this many days"},
            "merge_orphans": {"type": "boolean", "default": True, "description": "Merge orphan states with activation < 0.3 into the highest-activation parent"},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "archived_events": {"type": "integer"},
                "deleted_events": {"type": "integer"},
                "merged_orphans": {"type": "integer"},
            },
            "required": ["archived_events"],
        },
    ),
]


def get_tools() -> list[MCPTool]:
    return _TOOLS
