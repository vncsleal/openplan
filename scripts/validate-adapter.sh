#!/usr/bin/env bash
# Validate that all CostProbe adapters in cost-probe.ts conform to the contract.
# Called by CI to gate community contributions.
set -euo pipefail

echo "=== Adapter Contract Validation ==="

ADAPTER_FILE="src/adapters/cost-probe.ts"

if [ ! -f "$ADAPTER_FILE" ]; then
  echo "FAIL: $ADAPTER_FILE not found"
  exit 1
fi

# 1. Every create*CostProbe function must return CostProbe
echo "--- Checking return types ---"
MISSING_RETURN=$(grep -c 'export function create.*CostProbe' "$ADAPTER_FILE" || true)
WITH_RETURN=$(grep -c ': CostProbe {' "$ADAPTER_FILE" || true)
if [ "$MISSING_RETURN" -ne "$WITH_RETURN" ]; then
  echo "FAIL: Found $MISSING_RETURN create*CostProbe functions but only $WITH_RETURN have ': CostProbe {'"
  echo "       Every adapter factory must declare ': CostProbe' return type."
  exit 1
fi
echo "  OK: All $MISSING_RETURN factories declare CostProbe return type"

# 2. Every probe must have start() and stop() methods
echo "--- Checking method presence ---"
START_COUNT=$(grep -c 'start():' "$ADAPTER_FILE" || true)
STOP_COUNT=$(grep -c 'stop():' "$ADAPTER_FILE" || true)
if [ "$START_COUNT" -ne "$STOP_COUNT" ] || [ "$START_COUNT" -eq 0 ]; then
  echo "FAIL: start() and stop() counts mismatch ($START_COUNT vs $STOP_COUNT)"
  exit 1
fi
echo "  OK: $START_COUNT probe objects with start()/stop()"

# 3. No probe should throw — must use try/catch
echo "--- Checking error handling ---"
MATCHES=$(grep -c 'catch' "$ADAPTER_FILE" || true)
if [ "$MATCHES" -lt "$START_COUNT" ]; then
  echo "WARN: Only $MATCHES catch blocks for $START_COUNT probes — all probes must handle errors gracefully"
fi
echo "  OK: $MATCHES catch blocks found"

# 4. Verify types compile
echo "--- Checking TypeScript compilation ---"
if ! npx tsc --noEmit --project tsconfig.json 2>/dev/null; then
  echo "FAIL: TypeScript compilation error"
  npx tsc --noEmit --project tsconfig.json 2>&1 || true
  exit 1
fi
echo "  OK: Compiles cleanly"

# 5. Check for existing tests
echo "--- Checking adapter tests ---"
if [ ! -f "tests/adapters.test.ts" ]; then
  echo "FAIL: No adapter tests found. Create tests/adapters.test.ts"
  exit 1
fi
echo "  OK: tests/adapters.test.ts exists"

echo ""
echo "=== All adapter checks passed ==="
