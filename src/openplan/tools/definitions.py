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
        "Create a new project context. Idempotent — returns the existing root state if the project already exists. Call this once to bootstrap before using act/recommend/search.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "label": {"type": "string", "maxLength": 500, "description": "Optional root state label"},
        },
        ["project"],
        outputSchema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "state_id": {"type": "string"},
                "label": {"type": "string"},
                "created": {"type": "boolean"},
            },
            "required": ["ok", "state_id"],
        },
    ),
    t(
        "act",
        "Execute Action",
        "Traverse from your current position to a target. If the target state doesn't exist, it's created automatically. Records evidence, thought, and auto-calibrates the edge cost. This is the only tool that changes the graph. Use parent to create siblings under a specific state.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug"},
            "action": {"type": "string", "maxLength": 200, "description": "Action verb (implement, research, design, etc.)"},
            "target": {"type": "string", "maxLength": 500, "description": "Target label or state ID. If it doesn't exist, it's created."},
            "parent": {"type": "string", "maxLength": 20, "description": "Optional parent state ID. Creates the target as a child of this state instead of the cursor. The cursor still moves to the target."},
            "evidence": {"type": "string", "maxLength": 2048, "description": "Optional evidence URL or description"},
            "thought": {"type": "string", "maxLength": 10000, "description": "Optional reasoning"},
            "expected_cost": {"type": "object", "maxProperties": 10, "description": "Optional expected cost estimate"},
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
        "Analyze the graph to find the highest-value target and plan an optimal A* path to it. Unlike plan, doesn't require a target — the system proactively recommends the best next state based on activation, visit counts, orphan status, and optional goal alignment. When project is omitted, searches across all projects.",
        {
            "project": {"type": "string", "maxLength": 200, "description": "Project slug (optional; omit for cross-project)"},
            "goal": {"type": "string", "maxLength": 500, "description": "Optional natural language description of what to work on"},
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
            },
        },
    ),
    t(
        "search",
        "Search Knowledge",
        "Search across all projects for matching states, projects, and insights. Returns a combined result of projects, states, and stored learnings. Use this to find what you know, what projects exist, and what you've learned. When query is omitted, returns a complete project index.",
        {
            "query": {"type": "string", "maxLength": 500, "description": "Search query (omit to list all projects)"},
        },
        outputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "projects": {"type": "array"},
                "states": {"type": "array"},
                "insights": {"type": "array"},
                "count": {"type": "integer"},
            },
            "required": ["query", "projects", "count"],
        },
    ),
]


def get_tools() -> list[MCPTool]:
    return _TOOLS
