# OpenPlan

**Waze for AI agents planning** — an MCP server that helps AI agents plan software projects efficiently by learning from every agent's cost data.

[![PyPI version](https://img.shields.io/pypi/v/openplan-mcp?color=blue)](https://pypi.org/project/openplan-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Smithery](https://img.shields.io/badge/Smithery-available-blue?logo=data:image/svg+xml;base64,...)](https://smithery.ai/server/@vncsleal/openplan)

## How it works

AI agents use OpenPlan's tools to track project phases, costs, and outcomes. Every `start()` and `complete()` call generates calibration data that improves estimates for every other agent — like Waze uses every driver's trip data to give better ETAs.

```
start → complete × N → verify → recommend
```

## Quick Start

### Via PyPI

```bash
pip install openplan-mcp
```

Then add to your MCP host config:

```json
{
  "mcp": {
    "openplan": {
      "type": "local",
      "command": ["uvx", "openplan-mcp"]
    }
  }
}
```

Or use `uvx` directly (no install needed):

```json
{
  "command": ["uvx", "openplan-mcp"]
}
```

## CLI

```bash
openplan                  # Start MCP server
openplan auth login       # Authenticate with GitHub for Pro tier
openplan auth logout      # Remove credentials
openplan auth status      # Show authentication state
openplan subscribe        # Start Pro subscription ($10/mo)
openplan status           # Show OpenPlan status
```

## Tools

| Tool | Description |
|------|-------------|
| `start` | One-call project kickoff: parses goal into phases, estimates costs from global baselines |
| `complete` | Mark a phase done, attaches evidence, auto-traverses to next phase |
| `act` | Traverse, branch, verify, set status, abandon, prune, revert |
| `recommend` | Best next step with A* path, project health, cost estimates |
| `export` | Export full graph as JSON / GraphML / matrix |

## Architecture

The MCP server runs locally. Calibration data syncs to `api.openplan.cc` (optional, anonymous by default). The cloud aggregates anonymized cost data across all users — every project improves estimates for everyone.

```
  MCP host (OpenCode / Claude Desktop / Cursor)
       │
  openplan MCP server (stdlib, uvx openplan-mcp)
       │
       ├── local SQLite (your projects, always works offline)
       │
       └── api.openplan.cc (global calibration pool, optional)
```

## Data Privacy

Only `{project_type, action, expected_cost, actual_cost, outcome}` is shared — no source code, no project names, no file paths. Anonymous by default. GitHub OAuth for Pro features.

## License

MIT
