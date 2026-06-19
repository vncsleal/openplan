# Changelog

## 0.1.13 — 2026-06-19

- **Chore:** Version reads from package.json dynamically — no more hardcoded versions
- **Fix:** WAL pragma removed from in-memory databases
- **Fix:** Removed dead RATE_LIMITS dict and unused `_get_tier` function
- **Fix:** Self-diagnostics now reports `replanTiming`, `mergeRate`, `reorderRate`
- **Fix:** `project_type` no longer hardcoded to "software" — sent dynamically from route project name
- **Feat:** `openplan install` now asks for consent before writing configs
- **Feat:** Archive-based hazard detection in `review()`

## 0.1.12 — 2026-06-19

- **Feat:** Path learning — `plan()` reads completed sequences for action-level efficiency
- **Feat:** Baseline fetch on server start (no more 5min delay)
- **Feat:** Conflict detection — `CONFLICT` error when same project gets different goal
- **Feat:** Cost probe starts before first phase
- **Feat:** Cross-machine export via Mesh API (`openplan export`)
- **Feat:** Account delete (`openplan account delete`)
- **Feat:** Archive-based hazard detection in `review()`
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
