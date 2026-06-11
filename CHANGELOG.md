# Changelog

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
