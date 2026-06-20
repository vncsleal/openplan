# OpenPlan MCP Server

**Waze for AI agents** — plan, track, and learn from software projects.

An [MCP](https://modelcontextprotocol.io) server that helps AI agents decompose goals into costed execution plans, checkpoint progress with deviation tracking, and learn from past project data.

## Quick Start

```bash
npx @openplan/mcp
```

The server auto-creates its config and SQLite database on first run — no setup needed.

Add it to your MCP client:

**opencode.json:**
```json
{
  "mcp": {
    "openplan": {
      "type": "local",
      "command": ["npx", "-y", "@openplan/mcp"]
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
      "args": ["-y", "@openplan/mcp"]
    }
  }
}
```

Or run `openplan install` to auto-detect and configure both.

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
openplan                 # Start MCP server (stdio mode)
openplan install         # Auto-detect and install in MCP clients
openplan auth            # Authenticate with Mesh via GitHub OAuth
openplan subscribe       # Subscribe to Pro (Stripe Checkout)
openplan portal          # Manage subscription (Stripe Customer Portal)
openplan account         # Show identity, API key, subscription
openplan config          # View current configuration
openplan mesh [on|off]   # Show or toggle Mesh sync
openplan status          # List routes for a project
openplan log             # Show checkpoint trail
openplan export          # Export calibration data (Pro)
openplan completion      # Generate shell completion script
openplan doctor          # Check system health and diagnose issues
```

Use `--json` on `account`, `config`, `status`, `log`, `mesh` for structured output. Auth supports `--no-browser`, `--clipboard`, `--with-token <key>`, and `--debug`.

## Architecture

```
core/      Domain types, pure logic, typed ports
handlers/  MCP tool handlers — validation, wiring
adapters/  Mesh sync, cost probes, config loaders
db/        Drizzle schema, SQLite, DataStore implementation
```

**One rule:** Core never imports adapters or handlers. The `DataStore` port insulates core from Drizzle.

## Development

```bash
npm install
npm run dev        # tsx watch
npm test           # vitest (config at vitest.config.ts)
npm run test:e2e   # end-to-end against compiled dist/
npm run build      # tsc
npm run lint       # biome check
```

### CI/CD

GitHub Actions runs lint, test, build on every push/PR. Publish to npm happens automatically on version tags (`v*`).

## Stack

- **FastMCP** — MCP framework with Zod schemas for input validation
- **SQLite** via `better-sqlite3` / Drizzle ORM — 7 tables, local-first
- **Mesh API** (Python/FastAPI on Fly.io) — cross-session cost learning with MAD filter, Bayesian shrinkage, per-key rate limiting
- **Structured logging** — `createLogger(module)` with `[openplan:module]` prefix
- **Coverage** — Vitest with 60% branch / 65% line thresholds

License: MIT
