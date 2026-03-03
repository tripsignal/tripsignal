#!/usr/bin/env bash
# Monthly vulnerability audit for TripSignal dependencies.
# Runs pip-audit (backend) and npm audit (frontend), logs results.
# Sends an email alert via Resend if vulnerabilities are found.
# Intended to be called from cron.
set -euo pipefail

LOG="/opt/tripsignal/scripts/vuln-audit.log"
ENV_FILE="/opt/tripsignal/frontend/.env.production"
FROM_EMAIL="hello@tripsignal.ca"
TO_EMAIL="hello@tripsignal.ca"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') — $1" >> "$LOG"
}

send_alert() {
  local subject="$1"
  local body="$2"
  local api_key
  api_key=$(grep '^RESEND_API_KEY=' "$ENV_FILE" | cut -d'=' -f2-)

  if [[ -z "$api_key" ]]; then
    log "ERROR: RESEND_API_KEY not found — alert NOT sent"
    return 1
  fi

  curl -s -X POST "https://api.resend.com/emails" \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"from":"%s","to":["%s"],"subject":"%s","text":"%s"}' \
      "$FROM_EMAIL" "$TO_EMAIL" "$subject" "$body")"

  log "ALERT SENT: $subject"
}

# --- Backend (pip-audit) ---
docker exec tripsignal-api pip install --quiet pip-audit 2>&1 || true
BACKEND=$(docker exec tripsignal-api pip-audit 2>&1 || true)
# Filter out pip's own low-severity CVEs (mitigated by Python 3.12 PEP 706)
BACKEND_FILTERED=$(echo "$BACKEND" | grep -v "^pip " | grep -v "^---" | grep -v "^Name " || true)

# --- Frontend (npm audit) ---
FRONTEND=$(cd /opt/tripsignal/frontend && npm audit 2>&1 || true)

# Count real issues
BACKEND_VULNS=$(echo "$BACKEND_FILTERED" | grep -cE "^[a-z]" || true)
FRONTEND_VULNS=$(echo "$FRONTEND" | grep -oP '\d+ (?=vulnerabilit)' || echo "0")

TOTAL=$((BACKEND_VULNS + FRONTEND_VULNS))

# Log results
{
  echo "=========================================="
  echo "Vulnerability audit — $TIMESTAMP"
  echo "=========================================="
  echo ""
  echo "--- Backend (pip-audit) ---"
  echo "$BACKEND"
  echo ""
  echo "--- Frontend (npm audit) ---"
  echo "$FRONTEND"
  echo ""
  echo "--- Result: $TOTAL issue(s) found ---"
  echo ""
} >> "$LOG"

# Send email only if real vulnerabilities found
if [[ $TOTAL -gt 0 ]]; then
  send_alert \
    "Vulnerability audit: $TOTAL issue(s) found" \
    "The monthly dependency audit for tripsignal.ca found $TOTAL vulnerability issue(s).\\n\\nBackend (pip-audit):\\n${BACKEND}\\n\\nFrontend (npm audit):\\n${FRONTEND}\\n\\nRun the audit manually:\\n  sudo /opt/tripsignal/scripts/vuln-audit.sh\\n  cat /opt/tripsignal/scripts/vuln-audit.log"
  log "VULNERABILITIES FOUND: $TOTAL issue(s) — alert sent"
else
  log "CLEAN: No vulnerabilities found"
fi
