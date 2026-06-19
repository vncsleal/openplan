# OpenPlan v0.1.14 — Agent Instructions

## Identity

MCP server for AI-native project planning and cost tracking. `@openplan/mcp` on npm.

## MCP Primitives

- **3 tools**: `plan(goal, context?, replan?, project?)`, `checkpoint(phase?, actual_cost?, correct?, route_id?, project?)`, `review(route_id?, project?)`
- **3 resources**: `openplan://{project}/route`, `openplan://profiles`, `openplan://sync-status`

## Key Rules

1. Core never imports shell. Handlers wire adapters into core.
2. 3 tools, one job each. No modes, no sub-actions.
3. `.openplan` anchor file at project root for multi-session resumption.
4. Server auto-creates config on first run — no setup needed.
5. Architecture reference: `plan.md` in repo root.

## Commands

- `npm test` — run tests with vitest
- `npm run test:e2e` — end-to-end test against compiled dist/
- `npm run build` — compile TypeScript with tsc
- `npm run dev` — hot-reload dev mode
- `npm run lint` / `npm run format` — biome
