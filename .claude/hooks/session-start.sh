#!/bin/bash
# SessionStart hook for Claude Code cloud sessions.
#
# Two jobs. First, install the project so tests and scripts run. Second, say at
# the top of every session what this environment can and cannot reach -- which
# keys are set, which hosts answer, which CLIs exist -- so a failing probe is
# explained before anyone reads a traceback. It never prints a secret's value.
#
# Cloud only: local sessions have the owner's own .env.local and network.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

echo "== session-start: dependencies =="
if command -v poetry >/dev/null 2>&1; then
  # Idempotent: a satisfied lockfile is a no-op in a few seconds.
  poetry install --no-interaction --no-ansi 2>&1 | tail -3 || echo "poetry install failed (see above)"
else
  echo "poetry not found; skipping install"
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export PYTHONPATH="."' >> "$CLAUDE_ENV_FILE"
fi

echo
echo "== session-start: readiness (names only, never values) =="

echo "-- environment variables --"
for k in FMP_API_KEY GEMINI_API_KEY RAILWAY_TOKEN RAILWAY_API_TOKEN DB_UPLOAD_SECRET VERCEL_TOKEN; do
  if [ -n "${!k:-}" ]; then printf "  %-20s set\n" "$k"; else printf "  %-20s unset\n" "$k"; fi
done
# A session-wide DATABASE_URL points every local engine run at production.
# The plan sets it per command, inside Railway, never here.
if [ -n "${DATABASE_URL:-}" ]; then
  echo "  DATABASE_URL         SET -- unexpected in a cloud session; local runs would hit production"
fi

echo "-- network (CONNECT through the egress proxy) --"
for h in financialmodelingprep.com data.sec.gov www.sec.gov stockanalysis.com \
         ai-hedge-fund-production-7131.up.railway.app backboard.railway.app \
         generativelanguage.googleapis.com vertexaisearch.cloud.google.com \
         www1.hkexnews.hk api.vercel.com; do
  out="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "https://$h/" 2>&1)"
  case "$out" in
    *"response 403"*) status="blocked (proxy 403)";;
    *"000")           status="unreachable";;
    *)                status="reachable (${out##*$'\n'})";;
  esac
  printf "  %-46s %s\n" "$h" "$status"
done

echo "-- CLIs --"
for t in railway vercel; do
  if command -v "$t" >/dev/null 2>&1; then printf "  %-8s %s\n" "$t" "$(command -v "$t")"; else printf "  %-8s -\n" "$t"; fi
done

exit 0
