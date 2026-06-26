#!/bin/bash
# Reads session_id from Claude Code's stdin JSON and forwards to the light server.
# Usage: hook.sh <endpoint>
#   endpoint: session-start | session-end | working | idle | confirm
ENDPOINT="$1"
INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id','default'))" 2>/dev/null || echo "default")
curl.exe -s -m 2 "http://127.0.0.1:8777/${ENDPOINT}?sid=${SID}"
