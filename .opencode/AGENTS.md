# OpenPlan — Agent Instructions

## Identity

MCP server for AI-native project planning and cost tracking. `@openplan/mcp` on npm.

## MCP Primitives

- **3 tools**: `plan(goal, context?, replan?, project?)`, `checkpoint(phase?, actual_cost?, correct?, route_id?, project?)`, `review(route_id?, project?)`
- **3 resources**: `openplan://{project}/route`, `openplan://profiles`, `openplan://sync-status`

## Agent Loop

1. `plan(goal=..., context=..., project=...)` — decompose goal into costed phases
2. Implement each phase, then `checkpoint(phase=..., actual_cost=..., route_id=...)`
3. `checkpoint(route_id=...)` — status check, no phase needed, returns full state
4. `review(route_id=...)` — retrospective with accuracy and learning

Use `openplan` tools proactively for any non-trivial project. Every call improves future estimates.

## Key Rules

1. Core never imports shell. Handlers wire adapters into core.
2. `.openplan` anchor file at project root for multi-session resumption.
3. Architecture reference: `plan.md` in repo root.
