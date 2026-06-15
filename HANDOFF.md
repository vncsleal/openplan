# OpenPlan v0.7.0 — Session Handoff

**Date:** 2026-06-15
**Next session should read this first.**

## What Changed (v0.7.0)

### Evidence Filesystem Verification
`verify` now stats file URIs to confirm they actually exist on disk before marking them verified:
- File found → `status = 'verified'`, metadata recorded (size, mtime)
- File missing → `status = 'unverified'`, error stored in metadata
- Only `type: "file"` evidence is stat'd; other types (commit, test, checkpoint) remain `verified`
- The goal-marker matching loop already filters on `status = 'verified'`, so only real files trigger goal achievement

### Sequential Defaults for Branch Options
Options now auto-sequence by default:
```
act(options=[{label: A}, {label: B}, {label: C}])
# v0.6.0: root → A, root → B, root → C  (flat, depth=1)
# v0.7.0: root → A → B → C               (chain, depth=3)
```
Use `parallel: true` for flat siblings (old default):
```
act(options=[{label: A}, {label: B}, {label: C}], parallel=true)
# root → A, root → B, root → C  (flat)
```
The `sequence` field overrides positioning within the chain.

## Files Changed

| File | What |
|------|------|
| `server.py:345-367` | Stat evidence URIs on verify; conditional status; metadata JSON |
| `db/schema.py:165` | New `metadata TEXT DEFAULT '{}'` column on evidence table |
| `db/schema.py:174-176` | ALTER TABLE migration for existing databases |
| `core/state.py:594-614` | Auto-sequence when no `sequence` fields and not `parallel` |
| `tools/definitions.py:68` | Added `parallel` param; updated options description |
| `server.py:295` | Pass `parallel` from args to `branch()` |
| `tests/test_act.py` | +3 evidence stat tests (existing, missing, metadata) |
| `tests/test_diagnostics.py` | Updated flat tree assertions; new `test_diagnostics_parallel_tree` |

## Quick Commands

```bash
.venv/bin/pip install -e ".[dev]"         # reinstall after changes
.venv/bin/python -m pytest tests/ -v      # 162 pass
```

## Next Priorities

1. **Visible effective costs** — surface bandit-adjusted costs in recommend output instead of raw cost_tokens
2. **Goal marker parser improvements** — handle more natural phrasing in goal text splitting
3. **Multi-cursor** — one graph, multiple cursors per project for concurrent agents
4. **Workflow state machines** — encode the agent loop as a state machine in the graph itself
