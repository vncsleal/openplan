# OpenPlan v0.1.0 — Agent Instructions

## Identity

OpenPlan is an MCP server for AI-native project planning and cost tracking. Lives at `/Users/vncsleal/Code/openplan`. npm package `@openplan/mcp`, version `0.1.0`.

## MCP Primitives

- **3 tools**: plan, checkpoint, review
- **Resources**: `openplan://profiles`, `openplan://sync-status`

## Commands

- `npm test` — run 15+ tests with vitest
- `npm run build` — compile TypeScript
- `npx @openplan/mcp` — start MCP server (stdio)
- `npx fastmcp dev src/server.ts` — dev mode with hot reload

## Architecture

| Layer | Responsibility |
|-------|---------------|
| `server.ts` | FastMCP setup, tool/resource registration, lifespan |
| `core/planner.ts` | Goal decomposition, route generation |
| `core/tracker.ts` | Phase completion, deviation, hazard detection |
| `core/reviewer.ts` | Retrospective, learnings, self-diagnostics |
| `core/costs.ts` | Defaults, calibration, personal bias, tokenization |
| `core/ports.ts` | Interfaces: MeshPort, CostProbe |
| `handlers/` | MCP handler layer — validates args, calls core |
| `adapters/mesh.ts` | fetch-based Mesh sync, degraded mode |
| `adapters/cost-probe.ts` | Shell command cost probe (start/stop/delta) |
| `db/` | SQLite via better-sqlite3 with raw SQL schema |
| `config.ts` | smol-toml loader + env fallback |

## Key Rules

1. Core never imports shell. Shell imports core.
2. 3 tools, one job each. No modes, no sub-actions.
3. `.openplan` anchor file at project root for multi-session resumption.
4. First `plan()` call works immediately with bundled defaults.
5. Server auto-creates config on first run — no setup needed.
