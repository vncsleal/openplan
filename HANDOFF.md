# OpenPlan v0.6.0 — Session Handoff

**Date:** 2026-06-14
**Next session should read this first.**

## What Changed (v0.6.0)

### Fix: Inverted Goal-Evidence Matching
`verify` action now checks `? LIKE '%' || criterion || '%'` (evidence description contains criterion) instead of `criterion LIKE '%description%'`. Goal markers can actually be achieved by verify now.

### Fix: Version Triplication
Single source of truth at `openplan.__init__.VERSION`, read via `importlib.metadata`. Server, export, and package all report the same version.

### Fix: Dead `detail` Param Removed from `act`
The `detail: boolean` parameter on the `act` tool was never wired in the handler. Removed.

### Feature: Sequential Options
`options` items on `act()` now support a `sequence: integer` field. When present, `branch()` creates sequential edges from option[n] to option[n+1], producing a chained DAG instead of flat sibling states:

```python
# Before: flat star (depth=1)
act(options=[{label: A}, {label: B}, {label: C}])
# root -> A, root -> B, root -> C

# After: sequential chain (depth=3)
act(options=[{label: A, sequence: 1}, {label: B, sequence: 2}, {label: C, sequence: 3}])
# root -> A -> B -> C
```

### Feature: Auto-Check Goal Markers on State Completion
When `status="done"` is set (via status update), the server handler scans the state label against unachieved goal markers. Any match auto-achieves the marker. No separate `verify` call needed for simple label-based goals.

### Feature: `project_complete` Flag
`recommend()` returns `project_complete: true` when all goal markers are achieved. Agents can check this to know when a project is finished.

## Files Changed

| File | What |
|------|------|
| `server.py:351-355` | Fix inverted LIKE direction in evidence matching |
| `server.py:320-335` | Auto-check goal markers on state completion |
| `server.py:545-550` | `project_complete` flag in recommend output |
| `__init__.py` | New: single-source version via importlib.metadata |
| `server.py:913` | Now uses `VERSION` import |
| `export.py:68` | Now uses `VERSION` import |
| `tools/definitions.py:77` | Removed dead `detail` param from act |
| `tools/definitions.py:68` | Added `sequence` to options item schema |
| `core/state.py:568-586` | Sequence chaining logic in `branch()` |
| `tests/test_act.py` | +2 tests: goal marker label matching, evidence matching direction |
| `tests/test_branch.py` | +1 test: sequenced options produce chained graph |
| `tests/test_export.py` | +1 test: version consistency |
| `pyproject.toml` | Version 0.6.0 |
| `CHANGELOG.md` | 0.5.0 + 0.6.0 entries |

## Quick Commands

```bash
.venv/bin/pip install -e ".[dev]"         # reinstall after changes
.venv/bin/python -m pytest tests/ -v      # verify tests pass
```

## Next Priorities

1. **Multi-cursor** — one graph, multiple cursors per project for concurrent agents
2. **Evidence auto-verify** — watch for act(target="Implement...", thought="done") and auto-prompt for evidence
3. **Workflow state machines** — encode the agent loop as a state machine in the graph itself
