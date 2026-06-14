from __future__ import annotations

from mcp.types import (
    Tool as MCPTool,
    ToolAnnotations as MCPToolAnnotations,
)


def t(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    outputSchema: dict | None = None,
    annotations: MCPToolAnnotations | None = None,
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
        description=description,
        inputSchema=schema,
        outputSchema=outputSchema,
        annotations=annotations,
    )


_READ_ONLY = MCPToolAnnotations(readOnlyHint=True, destructiveHint=False)
_DESTRUCTIVE = MCPToolAnnotations(readOnlyHint=False, destructiveHint=True)

_TOOLS: list[MCPTool] = [
    t(
        "init",
        "Create a new project context. Idempotent — returns the existing root state if the project already exists. Call this once to bootstrap. Optionally set a project_type for cost baselines, and a goal describing the desired end state.",
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
        "The only mutation tool. Traverses to a target, creates branches, changes status, abandons branches, prunes subtrees, sets goals, or inspects states. Records evidence, auto-calibrates edge costs, updates baselines. The cursor moves to the target state. Every project state change goes through this tool.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "action": {"type": "string", "maxLength": 200, "description": "Action verb (implement, research, design, etc.). Set to 'abandon', 'prune', 'revert', 'set_goal', or 'verify' for special operations."},
            "target": {"type": "string", "maxLength": 500, "description": "Target label or state ID. If it doesn't exist, it's created. Omit when using options."},
            "options": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "action": {"type": "string"}, "sequence": {"type": "integer", "description": "Optional order index. When set, creates sequential edges from option[n] to option[n+1] instead of all flat siblings."}, "expected_cost": {"type": "object"}}, "required": ["label", "action"]}, "description": "Branch options — creates multiple child states from cursor in one call (replaces branch tool)."},
            "parent": {"type": "string", "maxLength": 20, "description": "Optional parent state ID. Creates target as child of this state instead of cursor."},
            "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked", "superseded"], "description": "Set cursor's status (replaces update_state for status changes). 'blocked' cascades to descendants."},
            "props_patch": {"type": "object", "maxProperties": 20, "description": "Key-value pairs to merge into the target state's props (replaces update_state for props)."},
            "expected_cost": {"type": "object", "maxProperties": 10, "description": "Optional expected cost estimate {tokens: number, risk: number}."},
            "actual_cost": {"type": "object", "maxProperties": 10, "description": "Optional actual cost spent {tokens: number}. When provided, calibrates the edge with real data."},
            "postconditions": {"type": "object", "maxProperties": 20, "description": "Optional key-value pairs stored on the target state's props."},
            "evidence": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string", "description": "Evidence type (file, commit, test, checkpoint, verification)"}, "uri": {"type": "string", "description": "File path, commit hash, test name, or URI"}, "description": {"type": "string", "description": "Human-readable description of what this evidence proves"}}, "required": ["type", "uri"]}, "description": "Evidence items linking a state to real artifacts. Used with action='verify' to attach proof of completion."},
            "thought": {"type": "string", "maxLength": 10000, "description": "Optional reasoning"},
            "dry_run": {"type": "boolean", "description": "When true, returns state info without mutating (replaces read_state for inspection)."},
        },
        ["project"],
        annotations=_DESTRUCTIVE,
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "next_state": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "cost_actual": {"type": "object"},
                "cost_delta": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "cost_source": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "states_affected": {"type": "integer"},
                "updated_fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ok", "cursor"],
        },
    ),
    t(
        "recommend",
        "The only read tool. Returns the best next target with A* path, project health, self-tuning state, estimation accuracy, and tree visualization. Use target to find a path to a specific state. Use top_k to compare alternatives. Use sequence to simulate forward. Use up_depth to see ancestor context.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "target": {"type": "string", "maxLength": 500, "description": "Target state ID or label. Returns A* path when provided. Omit for best-next-target recommendation."},
            "query": {"type": "string", "maxLength": 500, "description": "Free-text search query across all states. Finds matching states by label, similar to full-text search. Use when you need to find something without knowing its state ID."},
            "format": {"type": "string", "enum": ["json", "ascii"], "description": "Tree output format (default: json)."},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Return up to K alternative paths sorted by cost, or top K search results when used with query."},
            "sequence": {"type": "array", "items": {"type": "object", "properties": {"action": {"type": "string"}, "target": {"type": "string"}}, "required": ["target"]}, "description": "Forward simulation — chain of actions to simulate. Returns trajectory with per-step costs."},
            "up_depth": {"type": "integer", "minimum": 0, "maximum": 5, "description": "Include N levels of ancestor states in the tree output."},
            "cursor": {"type": "string", "maxLength": 20, "description": "Optional cursor override. Defaults to current session cursor."},
            "max_cost": {"type": "number", "description": "Optional max cost constraint for pathfinding."},
            "risk_adjustment": {"type": "string", "enum": ["none", "probability", "variance"], "description": "How to adjust cost for edge probability."},
            "mode": {"type": "string", "enum": ["plan", "retro", "learnings"], "description": "Special modes: 'plan' estimates cost for a new project from historical data; 'retro' compares planned vs actual costs for a completed project; 'learnings' surfaces cross-project patterns."},
            "detail": {"type": "boolean", "description": "When true, include full bandit_arms, self_tuning, estimation_accuracy in response. Default false."},
        },
        annotations=_READ_ONLY,
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "query": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "states": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "insights": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "count": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "projects": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "target_label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "path": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "expected_cost": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "traversal": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "alternatives": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "heuristic_method": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "risk_adjustment": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "project_health": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "self_tuning": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "estimation_accuracy": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "tree": {"anyOf": [{"type": "array"}, {"type": "object"}, {"type": "string"}, {"type": "null"}]},
                "tree_format": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "blockers": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "goal": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                "trajectory": {"anyOf": [{"type": "array"}, {"type": "null"}]},
                "total_cost": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "cumulative_prob": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "steps": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "project": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "from": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["ok"],
        },
    ),
]


def get_tools() -> list[MCPTool]:
    return _TOOLS
