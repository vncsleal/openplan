# OpenPlan v0.1.0

**Waze for AI agents** — an MCP server that helps AI agents plan, track, and learn from software projects. 3 tools, one job each, no modes, no sub-actions.

---

## Principles

1. **3 tools. One job each.** No modes. No sub-actions. Every tool call does exactly one thing.
2. **The MCP server is MIT.** Free forever. Zero gating.
3. **The Mesh is populated by everyone.** Free users contribute and benefit equally.
4. **Local-first.** Server works fully offline. The Mesh is additive, not required.
5. **The agent is smart. OpenPlan is a data source.** The server remembers well, it doesn't think.
6. **SQL over ML.** Cost learning and path learning are SQL aggregates, not algorithms.
7. **No errors for the agent.** Everything degrades gracefully. The agent never sees sync failures. Internal validation errors produce structured responses, not crashes.
8. **Every line is exercised.** If it's not used by an agent or tested, cut it.
9. **Agent capability is not tracked.** Personal bias per identity doesn't distinguish agent type.

---

## Agent Loop

```
plan  →  checkpoint  →  checkpoint  →  ...  →  review
         checkpoint()  ← status check (any time)
```

**Plan phase:** Agent calls `plan(goal, context?)` → receives decomposed route with cost estimates, evidence, alternatives, hazards. Agent reviews and starts working.

**Execute + Checkpoint phase:** Agent completes a phase, calls `checkpoint(phase, actual_cost)` → receives deviation, hazards, next phase. Cost probe runs automatically if configured; agent-reported cost is fallback.

**Status check (any time):** `checkpoint()` with no args returns full route state and position.

**Correction:** `checkpoint(phase, correct=<value>)` replaces the last actual_cost for that phase. Both the original and corrected values are logged for Mesh aggregate accuracy.

**Review phase:** `review()` → summary, deviations, cost learning, path learning, self-diagnostics.

---

## Tool Surface

### `plan(goal, context?, replan?)`

Decompose a goal into a costed route. Returns route with phases (label, action, expected_cost, CI), route_evidence (alternatives, clusters), personal bias, archived routes.

- `replan=True` archives current route and creates fresh decomposition
- Same goal + same project = return existing route (idempotent by default)

### `checkpoint(phase?, actual_cost?, correct?, route_id?, project?)`

One tool, three behaviors:

| Pattern | Behavior |
|---------|----------|
| `checkpoint("Auth", 1800)` | Record phase completion with deviation, hazards, next phase |
| `checkpoint("Auth", correct=2000)` | Correct last actual_cost for a phase without adding a new record |
| `checkpoint()` | Return full route state (status mode, no mutations) |

Phase subsumption matches by label substring. Cumulative actual_cost across sessions. Route auto-completes on last phase.

### `review(route_id?, project?)`

Session retrospective: summary, deviations per phase, accuracy by action, cost learning, path learning, self_diagnostics (skip/merge/reorder rates, hazard precision/recall), mesh sync status.

Zero-division protected: no actual_costs → null accuracy, empty deviations.

---

## MCP Surface

### Tools (3)

| Tool | Signature | Annotation |
|------|-----------|------------|
| `plan` | `(goal, context?, replan?)` | `readOnlyHint=True` |
| `checkpoint` | `(phase?, actual_cost?, correct?, route_id?, project?)` | `destructiveHint=True` |
| `review` | `(route_id?, project?)` | `readOnlyHint=True` |

### Resources (3)

| URI | Purpose |
|-----|---------|
| `openplan://{project}/route` | Read current route state (no mutations) |
| `openplan://profiles` | Personal bias, accuracy by action, sample counts |
| `openplan://sync-status` | Health check: mesh reachable, pending checkpoints, buffer, version |

---

## Error Model

All tools return structured JSON. Errors are never thrown as MCP exceptions. Response shape:

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<human-readable>",
    "param": "<offending-parameter>",
    "retry_after": <ms>
  }
}
```

| Code | When |
|------|------|
| `INVALID_ARGUMENT` | Bad input |
| `NOT_FOUND` | Route or project doesn't exist |
| `NOT_INITIALIZED` | Config missing |
| `CONFLICT` | Route already exists with different goal |
| `INTERNAL` | Unexpected failure |

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                MCP Host (Agent)                    │
│  plan ── checkpoint ── review → resources          │
└───────────────────────┬──────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────┐
│          OpenPlan MCP Server (local — TS)          │
│                                                    │
│  SQLite via better-sqlite3                         │
│  - routes, route_phases, calibration_events        │
│  - correction_events, cost_baselines               │
│  - completed_sequences, schema_version             │
│                                                    │
│  Background sync (5 min interval):                 │
│    - push unsynced checkpoints to Mesh             │
│    - pull latest baselines on start                │
│    - dual eviction: count-based + TTL-based        │
│                                                    │
│  Cost probe (optional, host-specific):             │
│    - OpenCode, Claude Code, Cursor, Codex          │
│    - start()/stop() — last stop() before           │
│      checkpoint wins. Handles backtracking.        │
│                                                    │
│  Degraded mode: all tools work, cached baselines,  │
│  no agent-visible errors                           │
└───────────────────────┬──────────────────────────┘
                        │ HTTPS (async, fetch)
┌───────────────────────▼──────────────────────────┐
│            The Mesh (api.openplan.cc)              │
│                                                    │
│  All checkpoints from all agents                   │
│  Aggregates per action (token-matched)             │
│  Completed route sequences                         │
│  Personal baselines (per identity)                 │
│                                                    │
│  Auth: GitHub OAuth (device code flow)             │
│  Billing: Stripe (Checkout + Tax)                  │
│  Stack: Python (FastAPI, Turso, Fly.io)            │
└────────────────────────────────────────────────────┘
```

### Architecture Boundary

```
core/ ─── domain types, pure logic, typed ports
handlers/ ── MCP handler layer — validation, wiring
adapters/ ── Mesh sync, config loader, cost probes
```

**Rule:** Core never imports adapters or handlers. Handlers wire adapters into core. The `DataStore` port insulates core from Drizzle — handlers create the store and inject it.

### Cost Probe

Interface: `start()` (snapshot before phase) → `stop()` (delta, returns null if unavailable). Configurable per host with shell command. Multiple start/stop calls per phase — last stop() before checkpoint wins. No probe configured → agent-reported cost. No probe, no error, no noise.

---

## Data Model

7 tables in SQLite (6 domain + `schema_version`), defined as Drizzle schema (single source of truth, self-installs via `CREATE TABLE IF NOT EXISTS`):

| Table | Key columns | Purpose |
|-------|-------------|---------|
| `routes` | id, project, goal, status, identity_id, total_expected, total_actual | Active and archived routes |
| `route_phases` | id, route_id, label, action, expected_cost, actual_cost, status, sequence | Phases within a route |
| `calibration_events` | id, action, phase_label_tokens, expected_cost, actual_cost, outcome, identity_id | Every checkpoint → learning data |
| `correction_events` | id, calibration_event_id, previous_actual, corrected_actual | Checkpoint corrections → Mesh accuracy |
| `cost_baselines` | id, match_level, action, avg_cost, ci_lo, ci_hi, sample_count | Cached Mesh aggregates |
| `completed_sequences` | id, action_sequence, total_expected, total_actual, efficiency | Path learning from completed reviews |

`schema_version` table tracks applied migrations for future schema changes.

**Anchor file (`.openplan`):** Created by `plan()` at project root — maps project name to route_id. Enables multi-session discovery without the agent knowing a route_id.

---

## Tokenization

Phase labels and goals are tokenized before storage and Mesh sync. This enables SQL-based matching without vector search or ML.

**Algorithm:** Lowercase → strip punctuation → collapse whitespace → remove stop words → trim to 50 tokens.

Three match levels consumed by the Mesh:
1. **Exact** — goal keywords + phase label keywords overlap (min 5 samples)
2. **Label keyword** — phase label keyword overlap only (min 20 samples)
3. **Action fallback** — action type only

Mesh receives token strings only, never raw labels. No ML, no vector search, no embedding infrastructure.

---

## Learning

**Cost learning:** Every `checkpoint()` creates a `calibration_event`. Mesh aggregates at three token match levels. Personal bias per identity: `AVG(actual / expected)`.

**Hazard learning:** Variance-based (CI ratio > 3.0 flagged as high-variance). Archive-based (abandon_reason patterns across 3+ projects generate structural hazards).

**Path learning:** Completed sequences stored on `review()`, queried by token match at next `plan()`.

All learning is SQL (`LIKE` + `GROUP BY`). No ML, no vector search.

---

## Multi-Session

1. **`.openplan` anchor file** — any agent, any session, discovers it in the working directory.
2. **`checkpoint()` with no args** — full state, instant position awareness.
3. **`plan()` idempotent per goal** — same goal returns existing route. `replan=True` archives.
4. **Cumulative phase costs** — agent A does 2000, agent B finishes with 2200, checkpoint is 4200.

---

## Identity

Dual model:
- **`identity_id`** — stable UUID, generated once on first run, stored locally. Never changes. Used for bias tracking and Mesh attribution.
- **`api_key`** — Mesh auth token. Can be rotated without losing identity.

Anonymous by default. GitHub OAuth links identity to a Mesh account (enables personal baselines, higher tiers).

---

## Harness

**First-run:** Server detects no config → auto-creates config with sensible defaults. No prompts, no CLI step.

**Config location:** XDG Base Directory — macOS uses `~/Library/Application Support/openplan/`, Linux uses `~/.config/openplan/`. Respects `$XDG_CONFIG_HOME` and `$XDG_DATA_HOME`.

**Config file:** TOML with env var override. `smol-toml` (zero-dependency TOML parser).

**Client registration** (`opencode.json`, `claude_desktop_config.json`) is handled by `openplan install` — writes only with user consent via interactive prompts.

**Host detection:** Reads `OPENCODE_SESSION_ID` etc. for host-specific behavior (cost probe defaults).

---

## Human CLI

No args starts the MCP server (stdio). Subcommands:

| Command | Purpose |
|---------|---------|
| `openplan install` | Detect MCP clients, ask to add OpenPlan |
| `openplan auth` | GitHub OAuth device code flow |
| `openplan subscribe` | Stripe Checkout Session |
| `openplan account` | Account info and subscription status |
| `openplan export` | Export calibration data (JSON/CSV/Markdown, Mesh-backed for cross-machine) |
| `openplan config show` | Display effective config |
| `openplan status [project]` | Route table, archived routes |
| `openplan log [route\|project]` | Checkpoint trail |
| `openplan mesh [on\|off]` | Show or toggle Mesh sync |

CLI conventions: stdout for data, stderr for messaging. `--json` on `account`, `config`, `status`, `log`. `NO_COLOR` support. picocolors for status coloring.

---

## Business Model

| | **Free** | **Pro** |
|---|---|---|
| **Price** | $0 | $9/mo |
| **MCP server** | MIT | MIT |
| **Mesh pull** | 100/day | Unlimited |
| **Baselines** | Pool only | Pool + personal |
| **Personal bias** | None | Bayesian shrinkage |
| **Data export** | — | CSV / JSON / Markdown |
| **Billing** | — | Stripe |

The MCP server is MIT — no locked features. Value is in the Mesh: the cloud calibration pool that sharpens estimates across sessions. Free users contribute to and benefit from the pool (rate limited). Pro users unlock personal baselines, unlimited pull, and export.

---

## Privacy

**Collected:** Action, tokenized phase label (never raw), expected/actual cost, outcome, anonymous identity ID.

**Not collected:** File contents, source code, agent prompts/responses, project names (raw).

**User control:** Data export via `openplan export` (JSON/CSV/Markdown, cross-machine). Data deletion via `openplan account delete`. Mesh deletes identity data within 30 days of request. Local data is never shared by the MCP server.

---

## Stack Decisions

### TypeScript

70.4% of reference MCP servers use TypeScript (Playwright, Cloudflare, Notion, Supabase). Senior TypeScript engineer maintainer — zero context-switch tax. TypeScript + Zod enforces types at build and runtime. Distribution via `npx @openplan/mcp` scoped package.

### FastMCP

Mature MCP framework, full protocol compliance, built-in Zod validation, dev tooling (`npx fastmcp dev` + `inspect`).

### Drizzle ORM + better-sqlite3

Typed query builder over SQLite — 1:1 with SQL, fully typed. Schema file is single source of truth (TypeScript compiler catches stale references at build time). Schema version table for future migrations. `:memory:` for fast, isolated tests.

### Commander + picocolors

Commander for CLI (zero deps, built-in styling). `picocolors` for coloring (zero deps, 2.5KB).

### smol-toml

Zero dependencies, tree-shakeable. TOML is the standard config format for CLI tools.

### tsc only (no bundler)

Official MCP servers use pure `tsc`. `npx` handles dependency installation. Simpler builds, stack traces point to real source lines.

### Python stays for the Mesh

The Mesh API (FastAPI, Turso, Stripe, GitHub OAuth) is a web service, not an MCP server. Rewriting would be 2x effort for no benefit.

---

## Observability

- `openplan://sync-status` resource: mesh reachable, pending checkpoints, buffer usage, version
- `review()` self_diagnostics: route create/archive ratio, phase-abandon rate, hazard precision/recall
- Liveness via MCP protocol ping (this is a stdio server, not a web service)

---

## Pool Poisoning Guard

**When needed:** When OpenPlan has 50+ active agent identities, not before. Currently 1 user (vinicius), ~5 projects. Document now, implement later.

### Defense layers (in order)

1. **MAD filter** — Median Absolute Deviation with scaling factor 1.4826.
   \[
   z_i^{robust} = \frac{x_i - median(x)}{1.4826 \cdot MAD}
   \]
   Reject calibrations where \(|z| > 3\). MAD has 50% breakdown point: an attacker needs to control half the pool to shift the estimate.

   Implementation requirement: need at least 20 samples to compute MAD reliably. Below that, skip filter and fall through to minimum sample threshold.

2. **Minimum sample threshold** — Don't build baselines with fewer than 20 calibration events per (action, project_type) bucket. Below 20, use Bayesian shrinkage toward the project_type prior:
   \[
   estimate = \frac{n \cdot \bar{x} + \kappa \cdot prior}{n + \kappa}
   \]
   where \(\kappa = 10\) (strength of prior) and \(prior\) is the global median for that action across all project_types.

3. **Per-key rate limiting** — Track calibration volume per `identityId`. If one identity produces >30% of calibrations in a sliding 24h window, quarantine that key: its calibrations still count but with 0.5 weight.

4. **Consistency check** — Phase calibrations must satisfy:
   - `actual_cost > 0` (obvious, but enforce)
   - `|expected_cost - actual_cost| / expected_cost < 10` (10x deviation is suspicious; flag for review rather than reject outright)
   - Outcome must be one of the known enum values

### References

- **Huber & Ronchetti, *Robust Statistics*** — MAD is the gold standard since the 1980s.
- **Gelman et al., *Bayesian Data Analysis*** — Chapter on hierarchical modeling (Bayesian shrinkage).
- **GitHub's abuse detection** — Rate limiting + behavioral scoring; they use MAD on latencies to detect API abuse.
- **Wikipedia, *Median absolute deviation*** — \(\hat{\sigma} = 1.4826 \cdot MAD\) for normal-like data.

### Non-goals

- No ML model for poisoning detection (overkill for current scale).
- No cryptographic commitments or on-chain verification.
- No per-identity reputation scores (introduces gameability without enough users to validate).

---

## Infinity Types (Why Keywords)

The current approach of `goal_tokens` and `labelTokens` as space-joined token strings is the correct design. Rationale:

- An enum of allowed labels would grow unbounded — agents invent new phase descriptions on every project.
- A union type in TypeScript would require schema changes and redeploys for every new token.
- Free-form token strings with `LIKE` matching avoid schema drift entirely.

The matching pipeline is already correct:
1. `tokenize()` — lowercases, strips punctuation and stop words, limits to 50 tokens
2. `matchLevel()` — token overlap counting with thresholds (>=2 exact, >=1 label_keyword, fallback to action)

### Why not TF-IDF

TF-IDF is designed for document retrieval where you have long texts and need to rank by relevance. Our tokens are short (10-50 tokens, not thousands) and the matching needs to be fast and deterministic for an MCP server.

Scenarios where TF-IDF would help:
- Synonym resolution: "stripe" vs "stripe-integration" vs "payment-gateway" — these don't overlap but mean the same thing. TF-IDF wouldn't solve this either (different surface tokens). This is a fuzzy matching problem, not a weighting problem.
- Rare token boost: a term like "webhook" appearing in only 1/100 phases gets higher IDF weight. But since our match is threshold-based (>=2 tokens), not ranking-based, IDF doesn't change the outcome.

If synonym resolution becomes a real problem, the solution is token normalization, not TF-IDF:
- Stemming: "deploying" → "deploy", "deployed" → "deploy"
- Alias map: add aliases at tokenize time, e.g. `tokenAliases: { "stripe-integration": "stripe", "payment-gateway": "stripe" }`
- This is a future concern. Current match levels are already generous enough to handle common variants.

---

## Estimation Algorithm Decision

### Current approach

Mediana por (matchLevel, action) — tiered lookup:
1. `exact` (>=2 token overlap, n >= 5) → use that bucket
2. `label_keyword` (>=1 token overlap) → next best
3. `action` (any calibration for that action) → fallback

### Why not agent-estimate (PyPI)

`agent-estimate` does M-estimation with Huber loss — robust regression for multivariate estimation. It's the right tool when:
- You have continuous features (not discrete buckets)
- You want a parametric model (not a lookup table)
- You have hundreds of features and thousands of samples

OpenPlan's estimation problem is univariate per segment: find the expected cost for (exact tokens, action) bucket. A continuous model would add complexity without benefit because:
- Our features are categorical (action, match level), not continuous
- Sample counts per bucket are small (tens, not thousands)
- A linear model with categorical features would produce the same per-group means

### When to revisit

If OpenPlan grows to:
- >1000 calibration events per identity
- Need to estimate cost from multiple continuous dimensions (e.g., code complexity score, commit count, file count)
- Want to predict cost before the first calibration event for a new project type

At that point, agent-estimate or a small Bayesian regression model becomes worth it. Not now.

---

## Pricing Model

### Core principle

- **MCP server is MIT.** Free forever, zero gating. No code is locked.
- **Value is in the Mesh, not the server.** The Mesh is a hosted service — that's what people pay for.
- **Push is unlimited for everyone.** Every calibration enriches the pool. Gating push weakens the product for everyone, including paying users.
- **Pull is what costs money.** Receiving baselines consumes infra (compute, storage, bandwidth).
- **Mesh is opt-out, not opt-in.** Sync is ON by default. Users can disable it via `openplan mesh off`, which turns off both push and pull.
- **No personalization on Free.** Free users get pool-only baselines. No personal bias adjustment, no custom estimates.
- **Personal baselines are the Pro upsell.** Bayesian shrinkage that blends your calibration history with the pool prior.

### Free

- MCP server (MIT) — 3 tools, 3 resources, local SQLite, full CLI
- Mesh sync ON by default. Push: unlimited. Pull: 100/day (24h sliding window)
- If pull limit is hit, server uses cached baselines and retries next window
- Agent never sees an error: degraded sync, not degraded tools
- Pool-only baselines: global median aggregated from all identities
- No personal bias — estimates are the same for everyone on Free
- No data export
- `openplan mesh off` to opt out entirely

### Pro

- Everything in Free
- Pull from Mesh: unlimited (no rate limit)
- Personal baselines via Bayesian shrinkage:

  ```
  personal_estimate = (n · personal_median + κ · pool_median) / (n + κ)
  ```

  - `n` = calibrations from your identity in the (action, matchLevel) bucket
  - `pool_median` = global median from the Mesh
  - `κ` = prior strength (~10, adjustable)
  - With 0 calibrations: estimate = pool (same as Free)
  - With 10 calibrations: 50% personal / 50% pool
  - With 100 calibrations: ~90% personal

- **Guarantee**: personal baseline is never worse than pool baseline. With few data points it converges to pool (safe). With enough data it converges to your personal signal.
- Data export: CSV / JSON / Markdown of complete history (routes, phases, deviations, accuracy by action)
- Priority queue: Pro pulls process before Free pulls
- GitHub OAuth for identity + Stripe for billing

### Rate limit details

- Window: sliding 24h, counted server-side per api_key
- Push: no limit
- Pull: 100/day Free, unlimited Pro
- Cache: baselines cached locally in SQLite, refreshed in background (5min interval). Rate limit affects cache refresh, not MCP tool responses.

### Identity

- **Free**: anonymous UUID + optional api_key. Mesh sync with rate-limited pull. Pool-only baselines.
- **Pro**: GitHub OAuth linked to api_key. Mesh sync with unlimited pull. Personal baselines.
- No auth = no Mesh sync. Server works fully offline. This is the "offline mode" — functional but isolated.

### Future considerations

- Self-hosted Mesh enterprise (private pool, no data leaves the org)
- Custom probes, deviation alerts, dashboards (requires GUI — not in scope for v0.x)

## Compliance (LGPD / GDPR)

### Classification

OpenPlan is classified as **very low risk**. The data collected (action, tokenized phase label, expected/actual cost, anonymous UUID) is irreversibly anonymized before processing. Per LGPD Art. 12 and GDPR Recital 26, irreversibly anonymized data is not considered personal data. OpenPlan is designed to operate outside the scope of both regulations.

### Legal basis

- **Legitimate interest** (Art. 6(1)(f) GDPR / Art. 10 LGPD) — product analytics and cost estimation improvement. No consent required because data is anonymized.
- **Contract** (Art. 6(1)(b) GDPR) — Stripe subscription processing for Pro users. Limited to billing email and Stripe customer ID.

### Data collected

All fields are anonymized or non-identifying:

| Field | Type | Purpose |
|-------|------|---------|
| `action` | string | Type of work performed (implement, design, test, etc.) |
| `phase_label_tokens` | tokenized string | Lowercased, stop words removed, max 50 tokens. Raw label never transmitted. |
| `expected_cost` | float | Estimated cost in seconds |
| `actual_cost` | float | Actual cost reported by checkpoint() |
| `outcome` | string | success / partial / failure |
| `project_type` | string | High-level category ("software") |
| `session_id` | random UUID | Deduplication — not linkable to identity |
| `timestamp` | unix time | Event creation time |

### Data NOT collected

Source code, file paths, project names (raw), agent prompts/responses, raw phase labels, API keys, credentials, email (except Stripe billing), IP addresses (server-side), browser fingerprints, cookies.

### Data subject rights (LGPD / GDPR)

All rights are exercisable via CLI:

| Right | Implementation |
|-------|---------------|
| **Access** | `openplan export` — JSON/CSV of all calibration data. Response within 15 days. |
| **Correction** | `checkpoint(phase, correct=value)` — inaccurate costs can be corrected. |
| **Erasure** | `openplan account delete` — deletes all calibration events and revokes API key. Deletion completes within 30 days. |
| **Portability** | `openplan export --format json` — machine-readable JSON output. |
| **Withdraw consent** | `openplan mesh off` — disables Mesh sync. No further data sent. |

### Data Protection Officer

- **DPO:** Vinicius Leal
- **Email:** oi@iamvini.co
- **Response time:** 15 days (LGPD) / 30 days (GDPR)

### International transfers

- **Mesh API:** Fly.io (US) — SCCs in place with Fly.io.
- **Database:** Turso (US) — calibration events, API keys, subscriptions.
- **MCP Server:** Local machine — no data leaves your environment unless Mesh sync is enabled.

### Data Processing Agreement (DPA)

Required for enterprise accounts where OpenPlan processes data on behalf of a third party. MVP stage: OpenPlan is the data controller for its own anonymized calibration data. DPA available on request once enterprise tier is implemented.

### Record of Processing Activities (ROPA)

Maintained internally. Covers: calibration event storage (Turso, US), API key storage (Turso, US), subscription data (Turso, US + Stripe). Updated quarterly.

### Non-goals

- No gating of MCP server features. The server is MIT.
- No billing per checkpoint or per agent session. Flat tiers only.
- No per-API-key metering. Identity-level limits only.
- No ads, no data selling.
