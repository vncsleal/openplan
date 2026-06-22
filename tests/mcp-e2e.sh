#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== OpenPlan MCP E2E Test ==="

# Use a fresh in-memory DB by setting data dir to a temp location
export XDG_DATA_HOME=$(mktemp -d /tmp/openplan-e2e-XXXXXX)
export XDG_CONFIG_HOME=$XDG_DATA_HOME

# Helper: send a JSON-RPC message and read one response
send_rpc() {
  local request="$1"
  echo "$request" | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null
}

echo ""
echo "--- Test 1: initialize ---"
INIT_RESPONSE=$(send_rpc '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}')
echo "$INIT_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$INIT_RESPONSE"

echo ""
echo "--- Test 2: plan ---"
PLAN_RESPONSE=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"plan","arguments":{"goal":"Implement user auth with JWT","context":"API backend with token auth","project":"test-project"}}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$PLAN_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$PLAN_RESPONSE"

# Extract route ID
ROUTE_ID=$(echo "$PLAN_RESPONSE" | python3 -c "import sys,json; print(json.loads(json.loads(sys.stdin.read())['result']['content'][0]['text'])['id'])" 2>/dev/null)
echo "Route ID: $ROUTE_ID"

echo ""
echo "--- Test 3: checkpoint (record phase 1) ---"
CK1=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"checkpoint","arguments":{"phase":"Research and Planning","actual_cost":350,"route_id":"'$ROUTE_ID'"}}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$CK1" | python3 -m json.tool 2>/dev/null || echo "$CK1"

echo ""
echo "--- Test 4: checkpoint (record phase 2) ---"
CK2=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"checkpoint","arguments":{"phase":"Implementation","actual_cost":800,"route_id":"'$ROUTE_ID'"}}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$CK2" | python3 -m json.tool 2>/dev/null || echo "$CK2"

echo ""
echo "--- Test 5: checkpoint (status check - no args) ---"
STATUS=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"checkpoint","arguments":{"route_id":"'$ROUTE_ID'"}}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$STATUS" | python3 -m json.tool 2>/dev/null || echo "$STATUS"

echo ""
echo "--- Test 6: review ---"
REVIEW=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"review","arguments":{"route_id":"'$ROUTE_ID'"}}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$REVIEW" | python3 -m json.tool 2>/dev/null || echo "$REVIEW"

echo ""
echo "--- Test 7: resources/route ---"
RES_ROUTE=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"openplan://test-project/route"}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$RES_ROUTE" | python3 -m json.tool 2>/dev/null || echo "$RES_ROUTE"

echo ""
echo "--- Test 8: resources/profiles ---"
RES_PROFILES=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"openplan://profiles"}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$RES_PROFILES" | python3 -m json.tool 2>/dev/null || echo "$RES_PROFILES"

echo ""
echo "--- Test 9: resources/sync-status ---"
RES_SYNC=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"openplan://sync-status"}}\n' | npx tsx "$PROJECT_DIR/src/cli.ts" 2>/dev/null | tail -1)
echo "$RES_SYNC" | python3 -m json.tool 2>/dev/null || echo "$RES_SYNC"

echo ""
echo "=== ALL E2E TESTS COMPLETE ==="

# Cleanup
rm -rf "$XDG_DATA_HOME" 2>/dev/null
