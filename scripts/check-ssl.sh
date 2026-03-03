#!/usr/bin/env bash
# SSL certificate expiry monitor for tripsignal.ca
# Checks cert expiry and sends an alert via Resend if expiring within 14 days.
# Intended to run weekly via cron.

set -euo pipefail

DOMAIN="tripsignal.ca"
WARN_DAYS=14
LOG_FILE="/opt/tripsignal/scripts/ssl-check.log"
ENV_FILE="/opt/tripsignal/frontend/.env.production"
FROM_EMAIL="hello@tripsignal.ca"
TO_EMAIL="hello@tripsignal.ca"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') — $1" >> "$LOG_FILE"
}

send_alert() {
  local subject="$1"
  local body="$2"
  local api_key
  api_key=$(grep '^RESEND_API_KEY=' "$ENV_FILE" | cut -d'=' -f2-)

  if [[ -z "$api_key" ]]; then
    log "ERROR: RESEND_API_KEY not found in $ENV_FILE — alert NOT sent"
    return 1
  fi

  curl -s -X POST "https://api.resend.com/emails" \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"from":"%s","to":["%s"],"subject":"%s","text":"%s"}' \
      "$FROM_EMAIL" "$TO_EMAIL" "$subject" "$body")"

  log "ALERT SENT: $subject"
}

# Try to fetch the certificate
cert_output=$(echo | openssl s_client -servername "$DOMAIN" -connect "${DOMAIN}:443" 2>/dev/null)

if ! echo "$cert_output" | openssl x509 -noout -enddate > /dev/null 2>&1; then
  send_alert \
    "⚠️ Trip Signal SSL check FAILED — cannot connect" \
    "The SSL certificate check for ${DOMAIN} failed.\\n\\nopenssl could not connect or parse the certificate.\\n\\nCheck Caddy logs:\\n  docker logs tripsignal-caddy --tail 50"
  log "FAIL: Could not connect to ${DOMAIN}:443 or parse certificate"
  exit 1
fi

# Parse expiry date
expiry_str=$(echo "$cert_output" | openssl x509 -noout -enddate | cut -d'=' -f2-)
expiry_epoch=$(date -d "$expiry_str" +%s)
now_epoch=$(date +%s)
days_left=$(( (expiry_epoch - now_epoch) / 86400 ))

if [[ $days_left -le $WARN_DAYS ]]; then
  send_alert \
    "⚠️ Trip Signal SSL certificate expiring in ${days_left} days" \
    "The SSL certificate for ${DOMAIN} expires on ${expiry_str} (${days_left} days from now).\\n\\nCaddy should auto-renew Let's Encrypt certs. If this alert fires, renewal may have failed.\\n\\nCheck Caddy logs:\\n  docker logs tripsignal-caddy --tail 100\\n\\nManual renewal:\\n  docker exec tripsignal-caddy caddy reload --config /etc/caddy/Caddyfile"
  log "WARNING: Certificate expires in ${days_left} days (${expiry_str})"
else
  log "SSL OK — expires ${expiry_str} (${days_left} days left)"
fi
