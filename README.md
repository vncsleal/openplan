# OpenPlan MCP Server

**Waze for AI agents** — plan, track, and learn from software projects.

An [MCP](https://modelcontextprotocol.io) server that helps AI agents decompose goals into costed execution plans, checkpoint progress with deviation tracking, and learn from past project data.

## Quick Start

```bash
npx @openplan/mcp
```

The server auto-configures on first run — no setup needed. Add it to your MCP client:

**opencode.json:**
```json
{
  "mcp": {
    "openplan": {
      "type": "local",
      "command": ["npx", "@openplan/mcp"]
    }
  }
}
```

**claude_desktop_config.json:**
```json
{
  "mcpServers": {
    "openplan": {
      "command": "npx",
      "args": ["@openplan/mcp"]
    }
  }
}
```

## Tools (3)

| Tool | Description |
|------|-------------|
| `plan(goal, context?, replan?, project?)` | Decompose a goal into costed phases with estimates |
| `checkpoint(phase?, actual_cost?, correct?, route_id?, project?)` | Record phase cost, correct data, or check status |
| `review(route_id?, project?)` | Session retrospective with deviations, accuracy, learning |

## Resources (3)

| URI | Description |
|-----|-------------|
| `openplan://{project}/route` | Current route state and phase progress |
| `openplan://profiles` | Personal bias and accuracy by action |
| `openplan://sync-status` | Mesh sync health and pending checkpoints |

## CLI

```bash
openplan                 # Start MCP server (stdio)
openplan install         # Detect MCP clients
openplan auth            # GitHub OAuth (placeholder)
openplan config show     # View configuration
openplan status          # Route table
openplan log             # Checkpoint trail
```

## Architecture

```
core/      Domain types, pure logic, typed ports
handlers/  MCP tool handlers — validation, wiring
adapters/  Mesh sync, cost probes, config loaders
db/        Drizzle schema, SQLite connection, DataStore implementation
```

**One rule:** Core never imports adapters or handlers. The `DataStore` port insulates core from Drizzle.

## Data

- **SQLite** via `better-sqlite3` — local-first, fully offline
- 6 tables: routes, route_phases, calibration_events, correction_events, cost_baselines, completed_sequences
- Anchor file (`.openplan`) at project root for multi-session discovery

## Development

```bash
npm install
npm run dev        # tsx watch
npm test           # vitest
npm run build      # tsc
npm run lint       # biome
```

License: MIT
