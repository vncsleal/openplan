---
description: Checkpoint phase completion or get current status
---
Record a completed phase's cost, or check current project status.

1. If you just completed a phase: `checkpoint(phase="Phase name", actual_cost=<token count>)`
2. If you need to resume after context loss: `checkpoint()` with no args — returns full state
3. Check deviation to see if you're on track
