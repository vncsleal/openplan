# Changelog

## 0.6.0 (2026-06-14)

- Sequential options: `options` on `act()` now support `sequence` field to create ordered DAG chains instead of flat siblings.
- Auto-check goal markers on state completion: setting status="done" automatically checks state labels against unachieved goal criteria.
- `project_complete: true` flag in `recommend()` output when all goal markers are achieved.
- Fix: Inverted goal-evidence matching — `verify` now correctly checks `description LIKE '%criterion%'` instead of `criterion LIKE '%description%'`.
- Fix: Version triplication — single source of truth via `openplan.__init__.VERSION`.
- Fix: Removed dead `detail` parameter from `act` tool definition.
- Tests: 6 new tests for goal markers, evidence matching, sequential options, version consistency.

## 0.5.0 (2026-06-14)

- Goal markers: goals parsed into achievement criteria, tracked in `goal_markers` table.
- Evidence layer: structured evidence items (file, commit, test, verification) attachable to states.
- `verify` action: attach evidence, auto-match against goal markers.
- Cursor fix: `abandon()` moves cursor to nearest active ancestor instead of getting stuck.
- Prompt cleanup: all 4 prompts rewritten to reference only existing tools.
- New recommend modes: `plan`, `retro`, `learnings`.

## 0.1.0 (2026-06-11)

- Initial release.
- State space navigation model with activation heuristic.
- 10 MCP tools: init, observe, act, branch, plan, learn, diagnostics, export, project_list, compress.
- A* pathfinding with bimodal heuristic (cross-cluster via embeddings, within-cluster via min cost).
- Self-calibrating edge weights via learn tool.
- Embedding similarity search via fastembed (optional).
- ANN vector search via sqlite-vec (optional).
- RW lock for concurrent read/serialized write.
- Session tracking via OPENCODE_SESSION_ID env var.
- Idempotency keys on all events.
- Cycle detection on every act() transition.
- FTS5 full-text search with AFTER UPDATE trigger.
- Event archival and orphan state merging (compress tool).
- 55 tests across all tools and scales.
