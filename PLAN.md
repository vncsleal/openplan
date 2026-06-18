# OpenPlan v0.1.0

**Waze for AI agents** — an MCP server that helps AI agents plan, track, and learn from software projects. 3 tools, one job each, no modes, no sub-actions.

---

## Contents

- [Principles](#principles)
- [Agent Loop](#agent-loop)
- [Tool Surface](#tool-surface)
- [MCP Surface](#mcp-surface)
- [Architecture](#architecture)
- [Data Model](#data-model)
- [Learning](#learning)
- [Multi-Session](#multi-session)
- [Human CLI](#human-cli)
- [Business Model](#business-model)
- [File Map](#file-map)
- [Stack Decisions](#stack-decisions)
- [Launch Checklist](#launch-checklist)

---

## Principles

1. **3 tools. One job each.** No modes. No sub-actions. Every tool call does exactly one thing.
2. **The MCP server is MIT.** Free forever. Zero gating.
3. **The Mesh is populated by everyone.** Free users contribute and benefit equally.
4. **Local-first.** Server works fully offline. The Mesh is additive, not required.
5. **The agent is smart. OpenPlan is a data source.** The server remembers well, it doesn't think.
6. **SQL over ML.** Cost learning and path learning are SQL aggregates, not algorithms.
7. **No errors for the agent.** Everything degrades gracefully. The agent never sees sync failures.
8. **Every line is exercised.** If it's not used by an agent or tested, cut it.

---

## Agent Loop

```
plan  →  checkpoint  →  checkpoint  →  ...  →  review
         checkpoint()  ← status check (any time)
```

**Phase 1: Plan**

```
Agent: "I need auth + payments + dashboard."
plan(goal="Auth, Payments, Dashboard", context="Next.js + Stripe + PostgreSQL")
→ 6 phases with cost estimates, route evidence, alternatives, hazards
Agent reviews evidence and starts working on phase 1.
```

**Phase 2: Execute + Checkpoint**

```
Agent codes one phase. Done.
checkpoint("Auth", 1800)
→ {
    next_phase: "Payments (expected: 3000)",
    deviation: {ratio: 0.86, level: "low"},
    hazards: ["Payments has wide CI [500, 8000] (3 samples)"]
  }
Agent codes next phase.
```

**Phase 3: Review**

```
All phases done.
review()
→ {
    summary: {estimated: 7500, actual: 8200, phases: 6},
    learnings: ["your web_app implement avg: 1.08x (14 samples)"],
    path_learning: ["2 of 6 phases had >1.5x variance — consider splitting 'payments' into 'setup' + 'integration'"],
    self_diagnostics: {phase_skip_rate: 0, hazard_precision: 0.8}
  }
```

**Status check (any time, including multi-session resume):**

```
checkpoint()
→ {
    route_id: "R-1",
    project: "newsletter",
    phases: [
      {label: "Scaffold + auth", status: "done", actual: 1500},
      {label: "Subscriber management", status: "done", actual: 3100},
      {label: "Campaign builder", status: "done", actual: 4200},
      {label: "Send engine", status: "pending", expected: 3200},
      {label: "Analytics dashboard", status: "pending", expected: 1800},
      {label: "Deploy", status: "pending", expected: 600}
    ],
    position: "R-1/4",
    hazards: ["send engine CI [1500-5200] (8 samples) — high variance"]
  }
```

---

## Tool Surface

### `plan` — Decompose a goal into a costed route

```
plan(goal, context?)

→ {
    route: {
      id: "R-1",
      phases: [
        {label: "Auth", action: "implement", expected_cost: 2100, ci: [1800, 2500]},
        {label: "Payments", action: "implement", expected_cost: 3000, ci: [2200, 4200]},
        {label: "Dashboard", action: "implement", expected_cost: 2400, ci: [2000, 3000]}
      ],
      total_cost: 7500
    },
    route_evidence: {
      based_on: "implement→implement→implement sequence (42 samples, 1.05x avg)",
      alternatives: [
        {sequence: "design→implement→implement", samples: 12, avg_ratio: 1.18},
        {sequence: "implement→test→implement", samples: 7, avg_ratio: 1.35}
      ],
      clusters: [
        {similar_goal: "payment portal", projects: 89, efficiency: 1.02},
        {similar_goal: "subscription app", projects: 42, efficiency: 1.08}
      ]
    },
    personal_bias: {ratio: 1.03, based_on: 23},
    archived_routes: [
      {id: "R-1", phases_completed: 2, abandoned_at: "phase 3", reason: "coupling detected — watcher and sync engine are inseparable", efficiency: 1.15}
    ]
  }
```

**Frictionless:** `goal` is the only required parameter. `context` is free-form text. `replan=True` archives the current route and creates a fresh decomposition.

**Idempotent by default:** Same goal + same project = returns the active route. No side effects.

**Phase label matching:** Descriptive labels like "Auth (Better Auth, magic link)" carry stack signal. Deviation computed against route's planned expected cost.

**Known limitation — agent capability not tracked:** Personal bias per API key doesn't distinguish agent capability. A v2 improvement could add an optional `agent_capability` param.

### `checkpoint` — Record phase completion or get status

```
checkpoint(phase?, actual_cost?, route_id?, project?)
```

One tool, two behaviors: provide data to record, omit to get status.

**Record mode:**

```
checkpoint(phase="Auth", actual_cost=1800)
→ {
    phase_completed: "Auth",
    actual_cost: 1800,
    expected_cost: 2100,
    deviation: {ratio: 0.86, level: "low", outcome: "success"},
    next_phase: {label: "Payments", expected_cost: 3000, ci: [2200, 4200]},
    hazards: [
      {type: "high_variance", detail: "Payments CI [2200-4200] (8 samples)", suggested_buffer: 1.5},
      {type: "sequence_risk", detail: "Payments depends on Auth schema — not yet finalized"}
    ],
    route_completed: false
  }
```

**Status mode (no args):**

```
checkpoint()
→ {
    route_id: "R-1",
    project: "newsletter",
    status: "in_progress",
    phases: [all phases with their current status, actuals, expecteds],
    position: "phase 3/6",
    hazards: [active hazards for remaining phases]
  }
```

Key behaviors:
- Agent can checkpoint any phase name at any time — phases can be merged, split, reordered
- Deviation computed against planned expected cost. Subsumption matches pending phases by label substring.
- `route_completed` set automatically when last phase is checkpointed
- `actual_cost` is provided by the cost probe when configured, or by the agent as fallback

### `review` — Session retrospective

```
review(route_id?, project?)
→ {
    summary: {
      estimated: 7500,
      actual: 8200,
      phases_completed: 6,
      accuracy: 0.91
    },
    deviations: [
      {phase: "Auth", expected: 2100, actual: 1800, ratio: 0.86},
      {phase: "Payments", expected: 3000, actual: 4200, ratio: 1.4}
    ],
    accuracy_by_action: {
      "implement": {count: 4, avg_deviation: 1.18},
      "deploy": {count: 2, avg_deviation: 0.92}
    },
    cost_learning: [...],
    path_learning: [...],
    self_diagnostics: {...},
    mesh: {shared: 6}
  }
```

Both params optional. `review()` reviews active route if completed, or most recently completed route.

**`self_diagnostics`** reveals routes created vs archived, phase-abandon rate, re-plan timing, skip/merge/reorder rates, hazard precision/recall.

---

## Harness

**The MCP server picks up its own harness.** On first run, it creates `~/.config/openplan/config.toml` with sensible defaults — its own config, not the client's. No prompts, no CLI step, no config searching. The server detects the host environment and adapts.

**What happens on server start:**

1. Server starts, looks for `~/.config/openplan/config.toml`
2. If found — loads it, ready to serve
3. If not found — creates it with defaults:
   ```toml
   [core]
   db_path = "~/.local/share/openplan/data.db"
   
   [mesh]
   api_url = "https://api.openplan.cc"
   api_key = ""
   ```
4. Creates `~/.local/share/openplan/` directory and SQLite database
5. Sets `chmod 600` on config file
6. Ready to serve. First `plan()` call works immediately.

**Host detection:** The server reads `OPENCODE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, or similar env vars to know which host it's running on. This enables host-specific features (cost probe command defaults, etc.). No user action required.

**What about the client config (opencode.json, claude_desktop_config.json)?** That's a separate concern — registering the MCP server with the client so the client knows to launch it. OpenPlan doesn't write to client configs silently. It detects them and asks the user through the `install` command.

## MCP Surface

### Tools (3)

| Tool | Signature | Annotation | Rationale |
|------|-----------|------------|-----------|
| `plan` | `(goal: string, context?: string, replan?: boolean)` | `readOnlyHint=True` | Reads Mesh aggregates, returns route. No side effects. |
| `checkpoint` | `(phase?: string, actual_cost?: number, route_id?: string, project?: string)` | `destructiveHint=True` | Writes to database, updates route state. |
| `review` | `(route_id?: string, project?: string)` | `readOnlyHint=True` | Reads checkpoint history, computes diagnostics. |

### Resources (3)

| URI | Returns | Purpose |
|-----|---------|---------|
| `openplan://{project}/route` | Current route with all phase statuses | Read state without calling checkpoint |
| `openplan://profiles` | Personal bias, accuracy by action, sample counts | Check your own calibration |
| `openplan://sync-status` | Pending checkpoints, last sync time, mesh reachable | Health check for the Mesh |

---

## Architecture

```
┌──────────────────────────────────────────────┐
│              MCP Host (Agent)                  │
│  Tools: plan, checkpoint, review               │
│  Resources: route, profiles, sync              │
└─────────────────────┬────────────────────────┘
                      │
┌─────────────────────▼────────────────────────┐
│       OpenPlan MCP Server (local — TS)         │
│                                                │
│  SQLite via better-sqlite3 + Drizzle           │
│  - routes, route_phases                        │
│  - checkpoints (calibration_events) — buffered │
│  - cost_baselines (cached from Mesh)           │
│                                                │
│  Background sync:                              │
│    - push unsynced checkpoints to Mesh (5 min) │
│    - pull latest baselines on start            │
│    - buffer up to 1000 when Mesh unreachable   │
│                                                │
│  Cost probe (optional):                        │
│    - OpenCode: `opencode stats --json` delta   │
│    - Fallback to agent-reported cost           │
│                                                │
│  Degraded mode:                                │
│    - All tools work normally                   │
│    - plan() uses cached baselines              │
│    - No agent-visible degradation              │
└─────────────────────┬────────────────────────┘
                      │ HTTPS (async, fetch)
┌─────────────────────▼────────────────────────┐
│           The Mesh (api.openplan.cc — Python)  │
│                                                │
│  All checkpoints from all agents               │
│  Aggregates per action (phase label matching): │
│    - avg_cost, ci_lo, ci_hi, sample_count      │
│    - success_rate                              │
│  Completed route sequences:                    │
│    - action_sequence, avg_efficiency, count     │
│  Personal baselines (per API key)              │
│                                                │
│  Auth: GitHub OAuth (device code flow)         │
│  Billing: Stripe (Checkout + Tax)              │
│  Stack: FastAPI, Turso, Fly.io                 │
└────────────────────────────────────────────────┘
```

### Cost Probe

The cost probe is the mechanism for deriving `actual_cost` automatically. Without one, the agent reports cost directly — the system works identically either way.

**Interface (`core/ports.ts`):**

```typescript
interface CostProbe {
  /** Snapshot current state (call before phase begins) */
  start(): Promise<void>;
  /** Compute delta from snapshot to now. Returns null if unavailable. */
  stop(): Promise<number | null>;
}
```

**Flow:** Before the agent starts a phase, the handler calls `probe.start()`. After the phase completes, `checkpoint()` calls `probe.stop()` which returns the token delta. If the probe returns `null` (unavailable, not configured, unsupported host), the agent's reported `actual_cost` is used as fallback. The core domain never knows a probe exists — it receives an `actual_cost: number` either way.

**Built-in probes (`adapters/cost-probe.ts`):**

| Host | Mechanism | Config |
|------|-----------|--------|
| **OpenCode** | `opencode stats --json --session $SESSION_ID`, delta between snapshots | `command = "opencode stats --json"` |
| **Claude Code** | Agent SDK cost API or transcript parse (future) | Not yet implemented |
| **Codex** | Agent-reported (fallback) | No config needed |
| **Cursor** | Agent-reported (fallback) | No config needed |

The probe is configured in `config.toml`:

```toml
[cost_probe]
# Shell command that outputs JSON with a token count.
# Run at start() and stop() — delta is the actual_cost.
# ${SESSION_ID} is injected by the runtime.
command = "opencode stats --json --session ${SESSION_ID}"
```

Without a `[cost_probe]` section, the system uses agent-reported costs. No probe, no error, no noise.

### Architecture Boundary

```
core/ ─── Drizzle schema, pure domain logic, typed ports (interfaces)
handlers/ ── MCP handler layer — validates args, calls core, injects adapters
adapters/ ── Mesh sync (fetch), config loader (smol-toml), cost probes (shell commands)
```

Rule: Core never imports from adapters or handlers. Handlers wire adapters into core.

---

## Data Model

### Core tables (SQLite via Drizzle)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `routes` | Route plans | `id, project, goal, total_expected, total_actual, status, archived, abandon_reason, completed_at` |
| `route_phases` | Route steps | `id, route_id, label, action, expected_cost, actual_cost, outcome, status, sequence` |
| `calibration_events` | Checkpoints | `id, project, action, phase_label_tokens, expected_cost, actual_cost, outcome, synced` |
| `cost_baselines` | Cached Mesh data | `match_level, action, phase_label_tokens, avg_cost, ci_lo, ci_hi, sample_count, success_rate` |
| `completed_sequences` | Path learning | `id, goal_tokens, context_tokens, action_sequence, efficiency, outcome` |

Drizzle schema in `src/db/schema.ts`. Migrations via `drizzle-kit`.

### Project Anchor File (`.openplan`)

Created by `plan()` at the project root. Read by all tools when `route_id` is omitted.

```json
{
  "project": "newsletter",
  "route_id": "R-1",
  "goal": "Email newsletter platform",
  "status": "in_progress"
}
```

---

## Learning

### Cost Learning

Every `checkpoint()` produces a `calibration_event`. The Mesh aggregates at multiple match levels:

```sql
-- Level 1: Exact match (same goal keywords + same phase label keywords)
SELECT AVG(actual_cost), COUNT(*) FROM calibration_events
WHERE goal_tokens LIKE '%' || ? || '%'
  AND phase_label_tokens LIKE '%' || ? || '%'
HAVING COUNT(*) >= 5

-- Level 2: Phase label keyword match
SELECT AVG(actual_cost), COUNT(*) FROM calibration_events
WHERE phase_label_tokens LIKE '%' || ? || '%'
HAVING COUNT(*) >= 20

-- Level 3: Action fallback
SELECT AVG(actual_cost), COUNT(*) FROM calibration_events
WHERE action = ?
```

Personal bias tracked per API key: `AVG(actual_cost / expected_cost)`.

### Hazard Learning

**Variance-based:** Phases with `ci_hi / ci_lo > 3.0` are flagged as high-variance.

**Archive-based:** Archived routes with matching `abandon_reason` and boundary phase pair across 3+ projects generate a structural hazard.

### Path Learning

Every `review()` stores a `completed_sequence`. Queried by goal/context keyword match at next `plan()` time.

No ML. No vector search. Just `LIKE` and `GROUP BY`.

---

## Multi-Session

Four mechanisms, none require the agent to know a route_id:

1. **`.openplan` anchor file** — created by `plan()`, read by all tools. Any agent, any session, discovers it in the working directory.
2. **`checkpoint()` with no args returns full state** — any agent, any session, instantly knows position.
3. **`plan()` is idempotent per goal** — same goal returns existing route. `replan=True` archives and recreates.
4. **Phase costs are cumulative** — agent A does 2000, agent B finishes with 2200, checkpoint is 4200.

---

## Human CLI

Eight commands via Commander. No args starts the MCP server (stdio). Subcommands handle human operations:

```
openplan                   → Start MCP server (stdio)  ← default, no subcommand
openplan install            → Detect MCP clients, ask to add OpenPlan
openplan auth              → GitHub OAuth device code flow
openplan subscribe         → Stripe Checkout Session (upgrade tier)
openplan account           → Account info, plan, checkpoint count
openplan config show       → Display effective config
openplan status [project]  → Current route table + archived routes + goal
openplan log [route|project] → Full checkpoint trail (by route ID or project name)
```

**`install` command:** Scans for MCP client configs (opencode.json, claude_desktop_config.json, Cursor config, VS Code config). Lists found clients. Asks which one(s) to add OpenPlan to:

```
$ openplan install
✔ Detected MCP clients:
  1. OpenCode (~/.config/opencode/opencode.json)
  2. Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json)
  3. VS Code (~/.vscode-oss/extensions/)

? Add OpenPlan to which clients? › (space to select, enter to confirm)
  ◻ OpenCode
  ◉ Claude Desktop
  ◻ VS Code
```

Writes the MCP entry to selected configs with user consent. The user is in control — no silent writes.

`@clack/prompts` for the `install` command's interactive prompts. All other commands use Commander's built-in help, colors, and exit codes.

### CLI Conventions

| Convention | OpenPlan |
|------------|----------|
| Data to stdout, messaging to stderr | Command output (tables, JSON) to stdout. Progress to stderr. |
| Exit codes | 0=ok, 1=auth error, 2=not found, 3=mesh unreachable, 4=usage error |
| `--json` output | All commands accept `--json`. Overrides human table output. |
| `--no-color` / `NO_COLOR` | Standard. picocolors auto-disables on both. |
| Color conventions | `picocolors` for status: green=done, yellow=pending, red=hazard. |
| `-q` / `--quiet` | Suppress non-essential output. |
| `--help` / `-h` | Every command. |

### Config

TOML at `~/.config/openplan/config.toml`:

```toml
[core]
db_path = "~/.local/share/openplan/data.db"

[mesh]
api_url = "https://api.openplan.cc"
api_key = ""

[cost_probe]
# Shell command for automatic cost derivation.
# Runs at start() and stop() of each phase — token delta = actual_cost.
# ${SESSION_ID} injected by the runtime. Comment out for agent-reported.
# command = "opencode stats --json --session ${SESSION_ID}"
```

Loaded via `smol-toml` with env var fallback: `OPENPLAN_MESH__API_KEY`, `OPENPLAN_MESH__API_URL`, `OPENPLAN_CORE__DB_PATH`. Legacy `OPENPLAN_CONFIG`, `OPENPLAN_API_KEY`, `OPENPLAN_API_URL` recognized with deprecation warning. Without `[cost_probe]`, the system uses agent-reported costs — no error, no noise.

---

## Business Model

| | **Free** | **Pro** | **Team** |
|---|---|---|---|
| **Price** | $0 | **$9/mo** | **$49/mo** |
| **MCP server** | Full MIT | Full MIT | Full MIT |
| **Checkpoints** | Unlimited | Unlimited | Unlimited |
| **Baselines** | Global only | Global + personal | Global + personal + team |
| **Seats** | 1 API key | 1 API key | 5 API keys |
| **Billing** | — | Stripe | Stripe |

---

## File Map (~18 files)

```
openplan/
├── package.json              # @openplan/mcp — name, bin, scripts, deps
├── tsconfig.json             # TypeScript config
├── biome.json                # Lint + format
├── drizzle.config.ts         # Drizzle Kit config
├── src/
│   ├── server.ts             # FastMCP lifespan + tool/resource registration
│   ├── cli.ts                # Commander: init, config, auth, subscribe, status
│   ├── config.ts             # smol-toml loader + env fallback
│   ├── core/
│   │   ├── planner.ts        # Goal decomposition, route generation
│   │   ├── tracker.ts        # Phase completion, deviation, hazard detection
│   │   ├── reviewer.ts       # Retrospective, learnings, self-diagnostics
│   │   ├── costs.ts          # Defaults, calibration, personal bias
│   │   └── ports.ts          # Interfaces: MeshPort, CostProbe
│   ├── handlers/
│   │   ├── plan-handler.ts   # plan tool
│   │   ├── checkpoint-handler.ts  # checkpoint tool
│   │   └── review-handler.ts # review tool
│   ├── adapters/
│   │   ├── mesh.ts           # fetch-based Mesh sync, degraded mode
│   │   └── cost-probe.ts     # Shell command cost probe (start/stop/delta)
│   └── db/
│       ├── schema.ts         # Drizzle schema — 5 tables
│       └── connection.ts     # better-sqlite3 init + WAL
├── tests/
│   └── core.test.ts          # Vitest, in-memory SQLite
└── .opencode/                # opencode project config
    ├── AGENTS.md             # Agent instructions for v0.1.0
    ├── skills/openplan/SKILL.md
    └── commands/             # plan, checkpoint, review, status
```

**Architecture rule:** `core/` defines ports (interfaces). `adapters/` implements ports. `handlers/` wires them. `core/` never imports from `adapters/` or `handlers/`.

---

## Stack Decisions

### Why TypeScript

| Factor | Assessment |
|--------|------------|
| **MCP ecosystem** | TypeScript dominates — 70.4% of reference servers, all major vendors (Playwright, Cloudflare, Notion, Supabase) |
| **Maintainer fit** | Senior TypeScript engineer — zero context-switch tax |
| **Type safety** | TypeScript + Zod (via FastMCP) enforces types at build time and runtime. Python type hints are not enforced. |
| **Codebase size** | ~1,600 LOC — cheap rewrite, low risk |
| **Distribution** | `npx @openplan/mcp` — scoped npm package, matches org ownership |

### Why FastMCP (TS)

Mature (v4.3.0, 467k weekly), active maintenance (last release 2 days ago), full MCP protocol compliance, built-in Zod validation, `npx fastmcp dev` + `npx fastmcp inspect` for dev tooling.

### Why Drizzle + better-sqlite3

Type-safe SQL queries, `drizzle-kit` for migrations, adapter pattern supports switching to Turso without code changes, matches existing skill.

### Why Commander + @clack/prompts

Commander is the CLI standard (445M weekly, zero deps, built-in styling). `@clack/prompts` for the `init` command's interactive prompts only (text inputs, selects, confirm, spinner). `picocolors` (67M weekly, 0 deps, 2.5KB) for coloring command output — green for completed phases, yellow for pending, red for hazards.

### Why smol-toml

Zero dependencies, tree-shakeable, tiny. Wraps in ~20 lines. Config format is the standard for Python/TS CLI tools.

### Why tsc only (no bundler)

Official MCP servers (`@modelcontextprotocol/server-filesystem`, `@playwright/mcp`) use pure `tsc`. `npx` handles dependency installation. No bundler needed for `npx` distribution. Faster builds, simpler debugging (stack traces point to real source lines).

### What stays in Python

The **Mesh API** (`services/telemetry/` — FastAPI, Turso, Stripe, GitHub OAuth). It's a web service, not an MCP server. Rewriting it would be 2x the effort for no benefit.

---

## Launch Checklist

### Core tools
- [ ] `plan(goal)` — route decomposition from goal alone. `readOnlyHint=True`.
- [ ] `plan` — cold start handled by bundled defaults (generic phase sequences with label-specific costs)
- [ ] `plan` — `replan=True` archives current route, creates fresh decomposition for same goal
- [ ] `plan` — archived routes preserved with abandon_reason for path learning
- [ ] `plan` — route_evidence with alternatives, clusters, personal bias
- [ ] `plan` — CIs and hazards per phase
- [ ] `checkpoint` — phase completion with deviation and next phase. `destructiveHint=True`.
- [ ] `checkpoint` — outcome derivation (success/partial/failure)
- [ ] `checkpoint` — calibration_event inserted (feeds Mesh cost learning)
- [ ] `checkpoint` — hazards from calibration pool
- [ ] `checkpoint` — cumulative actual_cost across sessions
- [ ] `checkpoint` — terminal phase sets route_completed
- [ ] `checkpoint()` no-arg — returns full route state (status mode)
- [ ] `review` — summary with deviations per phase. `readOnlyHint=True`.
- [ ] `review` — cost_learning (accuracy by action, bias adjustment)
- [ ] `review` — path_learning (sequence patterns from completed routes)
- [ ] `review` — self_diagnostics (skip/merge/reorder rates, hazard precision)
- [ ] `review` — completed_sequence inserted for path learning
- [ ] `review` — mesh (shared, pending)

### MCP surface
- [ ] `openplan://{project}/route` — current route with phase statuses
- [ ] `openplan://profiles` — personal bias, accuracy stats
- [ ] `openplan://sync-status` — mesh health, pending checkpoints count

### Harness
- [ ] First-run detection: no config → auto-create `~/.config/openplan/config.toml` with defaults
- [ ] Auto-create `~/.local/share/openplan/` directory + SQLite database
- [ ] `chmod 600` on newly created config file
- [ ] Idempotent: existing config → load and serve (no file writes)
- [ ] Host detection: read `OPENCODE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID` etc. for host-specific behavior

### Database
- [ ] Drizzle schema — 5 tables (routes, route_phases, calibration_events, cost_baselines, completed_sequences)
- [ ] `drizzle-kit generate` produces SQL migration
- [ ] `drizzle-kit migrate` applies on server start
- [ ] WAL mode + foreign keys enabled

### Learning
- [ ] Cost learning: hierarchical matching (exact → label keyword → action fallback)
- [ ] Personal bias: per API key bias ratio applied at plan time
- [ ] Hazard learning: variance-based (CI ratio filter) + archive-based (abandon_reason patterns, min 3 samples)
- [ ] Path learning: completed sequences stored and queried for route evidence

### Cost probe
- [ ] `CostProbe` interface in `core/ports.ts` — `start()` / `stop()`
- [ ] Shell command probe in `adapters/cost-probe.ts` — runs command, parses JSON, computes delta
- [ ] Probe config in TOML (`[cost_probe]` section) with `${SESSION_ID}` interpolation
- [ ] Handler integration: probe.start() before phase, probe.stop() for actual_cost
- [ ] Graceful fallback: no probe configured → agent reports actual_cost directly
- [ ] Graceful fallback: probe command fails → agent-reported cost used

### Telemetry & Mesh
- [ ] Background sync: push unsynced checkpoints every 5 min via `setInterval`
- [ ] Baseline import: pull Mesh aggregates on server start
- [ ] Degraded mode: buffer up to 1000 checkpoints, cached baselines, no errors

### Anchor file
- [ ] `.openplan` created by `plan()`, read by `checkpoint()` and `review()`

### Human CLI
- [ ] `openplan install` — detect MCP clients, present choices, write with consent
- [ ] `openplan config show` — display effective config with source annotations
- [ ] `openplan auth` — GitHub OAuth device code flow
- [ ] `openplan subscribe` — Stripe Checkout Session link
- [ ] `openplan account` — account info, plan, checkpoint count
- [ ] `openplan status [project]` — route table, archived routes (derives from CWD)
- [ ] `openplan log [route|project]` — checkpoint trail by route ID or project name
- [ ] Exit codes: 0=ok, 1=auth, 2=not found, 3=mesh unreachable, 4=usage error
- [ ] `--json` flag on all commands
- [ ] `--no-color` flag + respect `NO_COLOR` env var
- [ ] Data to stdout, messaging to stderr
- [ ] picocolors for status output (green done, yellow pending, red hazard)

### Config
- [ ] TOML config at `~/.config/openplan/config.toml`
- [ ] smol-toml loader with env var override
- [ ] Validated load — fail loud on malformed config
- [ ] Legacy env var aliases with deprecation warning
- [ ] `chmod 600` on auto-created config file at first run

### Testing
- [ ] Tokenization
- [ ] Cost estimation (all match levels + fallback)
- [ ] Plan creation + replan + archive
- [ ] Checkpoint record + status mode + subsumption
- [ ] Review summary + diagnostics
- [ ] Config loading (valid, malformed, env override)
- [ ] Mesh adapter (sync, degraded, pull baselines)
- [ ] Cost probe (command runs, JSON parsed, delta computed, null fallback)
- [ ] All tests use in-memory SQLite

### Distribution
- [ ] npm: `npx @openplan/mcp`
- [ ] package.json: `bin.openplan = "dist/cli.js"`, `type: "module"`
- [ ] Build: `tsc && shx chmod +x dist/*.js`
- [ ] Release: `npm publish` with conventional commits
- [ ] opencode.json MCP entry: `["npx", "-y", "@openplan/mcp"]` with `OPENPLAN_API_KEY` env

### Bug fixes from Python version (verified in translation)
- [ ] `update_bias_for_checkpoint` wired into checkpoint flow
- [ ] `review` protected against zero-division when no phases have actual_cost
- [ ] Server reports correct version (not mcp library version)
- [ ] `hazard_precision` uses separate fired vs relevant criteria
- [ ] Hazard estimation uses phase's actual action, not hardcoded "implement"
- [ ] `_find_matching_sequences` sorts descending (best efficiency first)
- [ ] Mesh sync uses `fetch` (async) throughout — no event loop blocking
- [ ] Mesh sync sends explicit columns, not `SELECT *`
- [ ] Config file errors fail loud, not silently swallowed
- [ ] `cmd_status` / `cmd_log` query real database state

### Documentation
- [ ] README.md — what it is, 3 tools, 3 examples
- [ ] `.opencode/AGENTS.md` — agent instructions for v0.1.0
- [ ] `.opencode/skills/openplan/SKILL.md` — skill file for opencode
- [ ] `.opencode/commands/*.md` — plan, checkpoint, review, status
