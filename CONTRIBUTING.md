# Build an OpenPlan Cost Probe Adapter for Your MCP Host

## Mission

Create a `create<YourHost>CostProbe()` function in `src/adapters/cost-probe.ts` that returns real token/cost data from your MCP host, following the same architecture as the existing `createOpenCodeCostProbe()`.

## The Contract

```typescript
// src/core/ports.ts — do not modify
export interface CostProbe {
  start(): void;          // called after plan() — capture baseline
  stop(): number | null;  // called before checkpoint() — return delta tokens, or null if unavailable
}
```

`stop()` MUST return the number of **tokens consumed** between `start()` and `stop()`. Return `null` if unavailable (graceful degradation).

## Reference Implementation

Read `src/adapters/cost-probe.ts` and study `createOpenCodeCostProbe()`. The pattern is:

1. **Find the host's data source** — local SQLite database, CLI command, log file, or API
2. **Extract the current session's cumulative tokens** — `tokens_input + tokens_output + tokens_reasoning`
3. **Implement start/stop** — `start()` captures a baseline, `stop()` reads the new value and returns the delta
4. **Graceful degradation** — if unavailable, return `null`. Never throw.

## How to Discover Your Host's Cost Data

Your host (Claude Code, Cursor, Windsurf, Continue, etc.) tracks token usage internally. Find it by exploring in this order:

**1. Check environment variables:**
```bash
env | grep -iE "claude|cursor|windsurf|continue|session|token|cost"
```

**2. Check local databases:**
```bash
# Linux / macOS CLI
find ~/.local/share -name "*.db" -o -name "*.sqlite" 2>/dev/null
# macOS Desktop
find ~/Library/Application\ Support -name "*.db" -o -name "*.sqlite" 2>/dev/null
# Windows
find %APPDATA% -name "*.db" -o -name "*.sqlite" 2>/dev/null
```

**3. Check CLI commands:**
```bash
<host> export <session-id> 2>/dev/null | grep -c info
<host> stats --json 2>/dev/null
<host> session list --json 2>/dev/null
```

Once you find the data source, determine:
- Where is the current session ID stored?
- How do you get the cumulative token count for that session?
- Is the data updated synchronously (before each tool response)?

## Implementation Steps

**1.** Create the factory function in `src/adapters/cost-probe.ts`:

```typescript
export function createYourHostCostProbe(): CostProbe {
  let baselineTokens = 0;

  return {
    start(): void {
      // 1. Find the current session
      // 2. Read cumulative tokens (input + output + reasoning)
      // 3. Store as baselineTokens
    },

    stop(): number | null {
      if (baselineTokens === 0) return null;
      // 1. Read cumulative tokens again
      // 2. Return delta if positive, null otherwise
    },
  };
}
```

**2.** Register in `src/server.ts`:

```typescript
const costProbe = config.costProbeCommand
  ? createShellCostProbe(config.costProbeCommand)
  : hostId === "opencode" || isOpenCodeAvailable()
    ? createOpenCodeCostProbe()
    : hostId === "claude"
      ? createClaudeCostProbe()
      : hostId === "cursor"
        ? createCursorCostProbe()
        : hostId === "your-host"
          ? createYourHostCostProbe()
          : createNullCostProbe();
```

**3.** Update hostId detection in `src/server.ts`:

```typescript
const hostId = process.env.OPENCODE_SESSION_ID
  ? ("opencode" as const)
  : process.env.CLAUDE_SESSION_ID
    ? ("claude" as const)
    : process.env.CURSOR_SESSION_ID
      ? ("cursor" as const)
      : process.env.YOUR_HOST_SESSION_ID
        ? ("your-host" as const)
        : ("unknown" as const);
```

## Unit

The probe MUST return **token count** (input + output + reasoning). Dollar cost is secondary — tokens are the universal estimation signal across all models and hosts. If your host only exposes dollar cost, return `Math.round(cost * 1_000_000)` (microdollars).

## Verification

```bash
npx tsc --noEmit    # no type errors
npm test             # all 47 tests pass
npm run build        # builds clean
```

Then create a route, call `plan()`, do tool calls, call `checkpoint()` without `actual_cost` — the probe's value should appear as `cumulativeActual`.

## Rules

- Never read files from other applications without explicit user permission
- Never modify data outside OpenPlan — open databases in `readonly: true` mode
- Never depend on undocumented/internal APIs — prefer public CLIs, env vars, or documented DB schemas
- Graceful degradation always — if unavailable, return `null`. The system must work identically with or without your adapter
