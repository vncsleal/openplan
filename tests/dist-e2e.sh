#!/usr/bin/env bash
# E2E test against the compiled dist/ output — this catches build regressions.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== dist/ E2E Test ==="
echo ""

# 1. Build
echo "--- Building dist/ ---"
npm run build 2>&1

# 2. Verify dist/cli.js exists
if [ ! -f dist/cli.js ]; then
  echo "FAIL: dist/cli.js not found"
  exit 1
fi

# 3. Test CLI command (config show)
echo "--- Test: CLI config show ---"
CONFIG_OUTPUT=$(node dist/cli.js --json account 2>/dev/null)
echo "$CONFIG_OUTPUT" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
assert 'identityId' in data, 'Missing identityId'
assert 'dataDir' in data, 'Missing dataDir'
print('PASS: CLI account works')
"

# 4. Test MCP initialize + plan via stdin
echo "--- Test: MCP init + plan ---"
MCP_RESULT=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"plan","arguments":{"goal":"Test dist build","project":"dist-e2e"}}}\n' | node dist/cli.js 2>/dev/null | tail -1)

echo "$MCP_RESULT" | python3 -c "
import sys, json
resp = json.loads(sys.stdin.read())
content = json.loads(resp['result']['content'][0]['text'])
assert 'error' not in content, f'Plan failed: {content.get(\"error\", {})}'
assert len(content['phases']) > 0, 'No phases returned'
assert content['project'] == 'dist-e2e', f'Wrong project: {content[\"project\"]}'
assert content['status'] == 'active', f'Wrong status: {content[\"status\"]}'
print(f'PASS: plan returned {len(content[\"phases\"])} phases, route={content[\"id\"][:12]}...')
"

# 5. Test MCP checkpoint
echo "--- Test: MCP checkpoint ---"
ROUTE_ID=$(echo "$MCP_RESULT" | python3 -c "
import sys, json
resp = json.loads(sys.stdin.read())
content = json.loads(resp['result']['content'][0]['text'])
print(content['id'])
")

CK_RESULT=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"checkpoint","arguments":{"route_id":"'$ROUTE_ID'"}}}\n' | node dist/cli.js 2>/dev/null | tail -1)

echo "$CK_RESULT" | python3 -c "
import sys, json
resp = json.loads(sys.stdin.read())
content = resp['result']['content'][0]['text']
parsed = json.loads(content)
assert 'route' in parsed or 'phase' in parsed, 'Unexpected checkpoint response'
if 'route' in parsed:
    print(f\"PASS: checkpoint status -> {parsed['route']['status']}, {parsed['route']['goal'][:40]}...\")
else:
    print('PASS: checkpoint result received')
"

echo ""
echo "=== ALL DIST E2E TESTS PASSED ==="
