---
description: Simulate a sequence of actions
---
Simulate the following sequence of actions: $ARGUMENTS

Use the openplan simulate() tool. Break the arguments into individual steps:
- Each argument is a target state description
- The action for each step defaults to "implement"
- Call simulate() with the sequence
- Report: expected total cost, cumulative probability, per-step breakdown, and any high-uncertainty steps
