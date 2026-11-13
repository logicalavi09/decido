#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

post_run() {
  local file="$1"
  local key="$2"
  echo
  echo "=== POST /runs ($key) ==="
  curl -sS -X POST "$BASE_URL/runs" \
    -H 'Content-Type: application/json' \
    -H "Idempotency-Key: $key" \
    --data-binary "@$SCRIPT_DIR/requests/$file" \
    | python3 -m json.tool
}

echo "=== GET /health ==="
curl -sS "$BASE_URL/health" | python3 -m json.tool

post_run application_approved.json demo-approved
post_run application_manual_review.json demo-manual
post_run application_dependency_failure.json demo-dependency-failure
post_run claim_processing.json demo-claim
post_run employee_onboarding.json demo-onboarding
post_run vendor_approval.json demo-vendor
post_run document_verification.json demo-document

echo
echo "=== Idempotent replay (same key + payload) ==="
curl -sS -o /dev/null -D - -X POST "$BASE_URL/runs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-approved' \
  --data-binary "@$SCRIPT_DIR/requests/application_approved.json" \
  | grep -iE 'HTTP/|Idempotency-Replayed'

echo
echo "=== GET /workflows/application_approval/lint ==="
curl -sS "$BASE_URL/workflows/application_approval/lint" | python3 -m json.tool

echo
echo "=== POST /workflows/reload?dry_run=true (preview only) ==="
curl -sS -X POST "$BASE_URL/workflows/reload?dry_run=true" | python3 -m json.tool

echo
echo "Tip: inspect any run with GET /runs/<id>, /audit, /audit/verify and /explanation."
