# Changelog

## 0.1.21 — 2026-06-21

- **Feat:** Coverage enforcement in CI (`vitest run --coverage`), 6 test files (55 tests), 55% min thresholds
- **Feat:** Python Mesh API test skeleton with pytest (health, checkpoints, baselines, auth tests)
- **Feat:** CI security scanning — `npm audit --audit-level=high`, gitleaks action, Python API tests
- **Feat:** Adapter quality pipeline — `scripts/validate-adapter.sh` CI gate with contract validation
- **Feat:** Drizzle Kit migration system — `drizzle-kit generate`, `db:generate`/`db:push`/`db:drop` scripts
- **Feat:** Prune old completed routes (auto-prune on startup, 90-day default window)
- **Feat:** Rate limiting on MCP server (configurable via `OPENPLAN_RATE_LIMIT`, default 60 req/min)
- **Feat:** `openplan uninstall` command — removes config from OpenCode/Claude Desktop
- **Feat:** Structured file logging (per-module log files in `~/.local/share/openplan/logs/`)
- **Feat:** Server metrics resource (`openplan://metrics`) with request counts, uptime, error rate
- **Feat:** Server version resource (`openplan://version`)
- **Feat:** Dynamic shell completion generation (bash/zsh/fish) from Commander definitions
- **Feat:** Enhanced CLI `--help` with categorized commands and examples
- **Feat:** Dockerfile for MCP server (multi-stage Node 22 Alpine build)
- **Feat:** GitHub issue/PR templates, SECURITY.md, CODE_OF_CONDUCT.md
- **Fix:** `console.warn` in checkpoint-handler.ts replaced with structured logger
- **Fix:** Stale path in `tests/mcp-e2e.sh` (referenced old `src/cli.ts`)
- **Fix:** 7 unsafe `as Record<string, unknown>` casts in config.ts, mesh.ts, cli.ts — replaced with typed helpers with runtime validation
- **Fix:** ShellCostProbe handles empty/whitespace output correctly (returns null instead of 0)

## 0.1.20 — 2026-06-21

- **Feat:** Cost probe adapter for OpenCode — reads real token consumption from OpenCode's SQLite database (`tokens_input + tokens_output + tokens_reasoning`), replaces the useless wall-clock timer
- **Feat:** `createOpenCodeCostProbe()` — in-process adapter using `better-sqlite3`, no scripts, no subprocesses
- **Feat:** `createClaudeCostProbe()` / `createCursorCostProbe()` — placeholder adapters for future host implementations
- **Feat:** `createNullCostProbe()` — graceful degradation fallback, always returns `null`
- **Feat:** `createShellCostProbe()` — community adapter via external command (user configures in TOML)
- **Feat:** `isOpenCodeAvailable()` — auto-detects OpenCode installation by checking for the local DB
- **Feat:** `CONTRIBUTING.md` — full guide for AI agents to build cost probe adapters for any MCP host
- **Fix:** Removed `createTimerCostProbe()` — wall-clock seconds are meaningless for AI estimation
- **Fix:** Token count normalization — all adapters return tokens as the primary cost unit

## 0.1.19 — 2026-06-21

- **Security:** `is_active = 1` check added to `/v1/account` and `/v1/manage` — revoked keys can no longer access subscription/billing
- **Security:** `hmac.compare_digest()` for admin key comparison (constant-time, prevents timing side-channel)
- **Security:** Turso HTTP adapter now sends `None` params as `{"type": "null"}` instead of the literal string `"None"`
- **Security:** `.dockerignore` excludes `.env`, `.git/`, `node_modules/`, `.venv/` from build context
- **Fix:** Penetration tested — SQL injection, auth bypass, rate limiting, CORS, input validation all verified

## 0.1.18 — 2026-06-20

- **Feat:** CI/CD pipeline (GitHub Actions — lint, test, build, npm publish on tag)
- **Feat:** Vitest config with coverage thresholds (60% branch, 65% lines)
- **Feat:** Structured logger (`createLogger(module)`) with `[openplan:module]` prefix
- **Fix:** All API responses validated with Zod schemas (was raw `as Record<string, unknown>`) — 40+ unsafe casts eliminated
- **Fix:** DB connection guard prevents double-init and handle leaks; `resetDatabaseForTesting()` for clean test teardown
- **Fix:** All empty catch blocks now log via structured logger instead of silent suppression
- **Fix:** `checkpoint()` CI field computed from baselines via `ciFromBaseline()` (was hardcoded `null`)
- **Fix:** Dead `lastSnapshot` / `process.cpuUsage()` code removed from timer cost probe
- **Fix:** `DeviceAuthResponse.expires_in` made optional with default 900 (API omits the field)
- **Fix:** `ExportCalibration.created_at` accepts `number | string` (Python API returns Unix timestamps)
- **Fix:** `ExportCalibration.expected_cost`/`actual_cost` accept `null` (DB allows NULL)
- **Fix:** `openplan account` subscription display restored after schema migration regression
- **Fix:** `DEFAULT_MESH_URL` exported from config.ts — replaces 4 hardcoded URL strings
- **Chore:** Added `author`, `bugs` fields to package.json
- **Chore:** Added `.env.example` for telemetry service (documents 5 required env vars)
- **Chore:** Updated `.gitignore` with `coverage/`, `*.log`, `.npmrc`

## 0.1.17 — 2026-06-20

- **Feat:** `completion` command — generates bash/zsh/fish shell completion scripts
- **Feat:** `doctor` command — system health diagnostics (Node version, config, SQLite, identity, Mesh, API key, subscription, disk space)
- **Feat:** `gitleaks` pre-commit hook — prevents accidental secret leakage
- **Fix:** Tool descriptions now clarify `personalBias: null` (Free tier) and cumulative cost semantics
- **Fix:** `account delete` now confirms via `[y/N]` prompt and cancels Stripe subscription
- **Fix:** `log` argument now actually filters by route ID (was previously ignored)
- **Fix:** Standardized exit codes — all error paths use `process.exit(1)`
- **Fix:** Added `--json` support to `mesh` command

## 0.1.16 — 2026-06-19

- **Docs:** Update tool descriptions to mention personal bias, archived routes, mesh sync status
- **Docs:** Consolidate global AGENTS.md — remove duplicates, correct tool names, remove version numbers
- **Docs:** Update SKILL.md personal bias description (Bayesian shrinkage)

## 0.1.15 — 2026-06-19

- **Feat:** FastMCP framework replaces raw MCP SDK — Zod schemas, automatic validation, dev tooling
- **Feat:** Pool poisoning guard on Mesh API — MAD filter, Bayesian shrinkage, per-key rate limiting
- **Feat:** Consistency check in checkpoint handler — rejects negative costs, flags 10x+ deviations
- **Feat:** Personal bias uses Bayesian shrinkage (κ=10) instead of simple AVG
- **Feat:** Data Retention section in plan.md with industry-standard 30-day rolling window
- **Fix:** Mesh dedup uses random UUID per batch instead of routeId
- **Fix:** Rate-limit handling preserves cached baselines instead of clearing on 429
- **Fix:** `project_type` column on routes table
- **Fix:** Turso string-to-type conversion for all numeric reads
- **Fix:** Removed stale `mergeRate`/`reorderRate` from self-diagnostics
- **Fix:** Removed stale `alternatives`/`clusters` from plan evidence
- **Fix:** Zod version mismatch — updated to ^4.4.0 (FastMCP requirement)
- **Chore:** Biome organizeImports enabled — auto-fixed 17 files
- **Chore:** Removed dead `shx` dependency
- **Chore:** Consolidated PLAN.md into plan.md (single source of truth)

- **Chore:** Version reads from package.json dynamically — no more hardcoded versions
- **Fix:** WAL pragma removed from in-memory databases
- **Fix:** Removed dead RATE_LIMITS dict and unused `_get_tier` function
- **Fix:** `project_type` added to routes table, sent from plan input
- **Feat:** `openplan install` now asks for consent before writing configs
- **Feat:** Archive-based hazard detection in `review()`

## 0.1.12 — 2026-06-19

- **Feat:** Path learning — `plan()` reads completed sequences for action-level efficiency
- **Feat:** Baseline fetch on server start (no more 5min delay)
- **Feat:** Conflict detection — `CONFLICT` error when same project gets different goal
- **Feat:** Cost probe starts before first phase
- **Feat:** Cross-machine export via Mesh API (`openplan export`)
- **Feat:** Account delete (`openplan account delete`)
- **Feat:** `openplan mesh on/off` CLI toggle
- **Feat:** Match-level baselines from Mesh API
- **Fix:** Personal bias gated behind Pro tier
- **Fix:** Export gated behind Pro subscription
- **Fix:** Profiles resource respects Pro tier
- **Fix:** Rate limiting — push unlimited, pull 100/day free, 24h window
- **Fix:** Mesh API sends/receives `phase_label_tokens` for match-level aggregation
- **Fix:** Turso adapter compatibility with `SELECT changes()`
- **Fix:** Removed dead `MESH_UNREACHABLE` error code
- **Fix:** PLAN.md synchronized with Free/Pro model and implementation

## 0.1.10 — 2026-06-18

- **Feat:** `openplan subscribe manage` — Stripe Customer Portal for cancellations and billing management
- **Chore:** Add `/privacy`, `/status`, `/success` pages to website
- **Fix:** Stripe cancel URL scrolls to pricing section

## 0.1.9 — 2026-06-18

- **Chore:** Fix all docs — README, PLAN.md, CHANGELOG, agent instructions
- **Fix:** Version references updated throughout codebase
- **Fix:** Unused import and doubled function name cleaned up
- **Fix:** `createCostProbeProbe` renamed to `createTimerCostProbe`

## 0.1.8 — 2026-06-18

- **Fix:** Mesh API `_TursoHTTP` adapter now correctly parses Turso response (nested `response.result` path)
- **Fix:** Mesh API `init_db` creates `meta` table for Stripe webhook storage
- **Feat:** `openplan auth --with-token <key>` for CI/headless authentication
- **Feat:** Mesh API uses persistent SQLite path by default
- **Fix:** Timer line no longer leaves trailing `)` characters in auth output

## 0.1.7 — 2026-06-18

- **Fix:** Mesh API missing `meta` table in `init_db`
- **Feat:** Mesh API uses `~/.local/share/openplan/telemetry.db` as default path
- **Feat:** `openplan auth --with-token` fallback for CI

## 0.1.6 — 2026-06-18

- **Fix:** Stable polling animation (removed flickering `@clack/prompts` spinner)
- **Feat:** `openplan auth --debug` shows raw API responses for troubleshooting
- **Feat:** Timer display shows remaining time during auth polling

## 0.1.5 — 2026-06-18

- **Feat:** Auth UX with `→` style, auto-open browser, spinner, clipboard, `--no-browser`
- **Fix:** SIGINT handler for clean Ctrl+C during auth

## 0.1.4 — 2026-06-18

- **Feat:** Enhanced auth UX with auto-open browser, spinner, `slow_down` handling
- **Fix:** CLI version reads from `package.json` dynamically

## 0.1.3 — 2026-06-18

- **Fix:** CLI `--help`, `-h`, `help [command]` work in piped environments
- **Feat:** `openplan auth` wired to live Mesh API (GitHub OAuth Device Flow)
- **Feat:** `openplan subscribe` creates Stripe Checkout Session
- **Feat:** `openplan account` shows subscription tier from Mesh API

## 0.1.2 — 2026-06-18

- **Fix:** Mesh adapter uses default URL (`api.openplan.cc`) when no env var set
- **Fix:** Install command detects OpenCode at `~/.config/opencode/opencode.json`

## 0.1.1 — 2026-06-18

- **Fix:** Mesh adapter aligns with Python API contract (outcome mapping, timestamps)
- **Fix:** Install command OpenCode path detection

## 0.1.0 — 2026-06-18

- **Feat:** Initial TypeScript release — 3 MCP tools (plan, checkpoint, review)
- **Feat:** 3 MCP resources (route state, profiles, sync-status)
- **Feat:** Drizzle ORM + better-sqlite3 with 7 tables
- **Feat:** Background Mesh sync (5-minute interval)
- **Feat:** Cost probe (timer-based + shell command)
- **Feat:** `.openplan` anchor file for multi-session discovery
- **Feat:** Cross-platform XDG paths
- **Feat:** Structured error model (no MCP exceptions)
- **Feat:** Tool annotations (readOnlyHint, destructiveHint)
- **Feat:** CLI with 8 subcommands
- **Feat:** Personal bias tracking and accuracy metrics
- **Feat:** 47 unit tests + dist E2E tests
