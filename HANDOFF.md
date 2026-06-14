# OpenPlan v0.5.0 — Session Handoff

**Date:** 2026-06-14
**Next session should read this first.**

## What Changed (v0.5.0)

### Fix 3: Stale Prompt References
All 4 prompts rewritten to only reference tools that exist (init, act, recommend, export).
Removed dead references to: `diagnose()`, `tune()`, `reconstruct()`, `plan()`, `read_state()`, `tree()`, `search()`.

### Fix 2: Cursor Moves Off Abandoned State
`abandon()` now finds the nearest non-superseded ancestor and returns `cursor_moved: {from, to}`.
Server handler moves the cursor. The agent doesn't get stuck on a dead state.

### Fix 1: Goal Completion Detection
- New `goal_markers` table in schema: `(project, criterion, achieved, achieved_at, achieved_by)`
- `init(goal=...)` parses the goal into individual achievement markers
- `act(action="verify", evidence=[...])` links real artifacts to states, matches descriptions against goal markers
- `recommend()` returns goal progress: `goal.markers: {total, achieved, items}`
- `goal_satisfied: true` when all markers are met

### Improvement 4: Evidence Layer (Plan-to-Code Bridge)
- New `evidence` table: `(id, project, state_id, evidence_type, uri, description, status)`
- `act(evidence=[...])` accepts structured evidence items (file, commit, test, checkpoint, verification)
- `act(action="verify")` attaches evidence to a state and auto-marks matching goal markers as achieved
- `export()` includes evidence and goal_markers in output
- `recommend()` shows `evidence_total` and `evidence_verified` in project_health

## Files Changed

| File | Lines | What |
|------|-------|------|
| `db/schema.py` | +20 | `goal_markers` table, `evidence` table + indexes |
| `core/state.py` | +65 | `_nearest_active_ancestor()`, `_parse_goal_markers()`, `_insert_goal_markers()`, updated `abandon()` + `init_project()` |
| `server.py` | +80 | Verify sub-action, cursor-move handling after abandon, goal markers in recommend, evidence stats in health, all 4 prompts rewritten |
| `tools/definitions.py` | +2 | `evidence` array param on act, `verify` added to action enum |
| `core/read.py` | +12 | Evidence stats in `reconstruct()` output |
| `core/export.py` | +9 | Evidence + goal_markers in JSON export |
| `tests/test_export.py` | +1 | Version bump 0.2.1 → 0.5.0 |

## Tool Surface (still 4 tools)

Same init/act/recommend/export as v0.4.0. Nothing removed, two new sub-ops added:
- `act(action="verify")` — attach evidence, check goal markers
- `act(evidence=[...])` — include evidence payload on any act

## Quick Commands

```bash
.venv/bin/pip install -e ".[dev]"         # reinstall after changes
.venv/bin/python -m pytest tests/ -v      # 154 pass
vi HANDOFF.md && git add -A && git commit -m "feat: v0.5.0 — goal markers, evidence layer, cursor fix, prompt cleanup"
```

## Next Priorities

1. **New tests** for: goal marker parsing, evidence verify, cursor move on abandon
2. **Evidence auto-verify** — watch for act(target="Implement...", thought="done") and auto-prompt for evidence
3. **Multi-cursor** — one graph, multiple cursors per project for concurrent agents
4. **Workflow state machines** — encode the agent loop as a state machine in the graph itself
