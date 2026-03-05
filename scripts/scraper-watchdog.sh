#!/usr/bin/env bash
# scraper-watchdog.sh — External safety net for the SellOff scraper.
#
# Checks if the scraper container has been running a single cycle too long.
# Alerts via Resend email, and optionally force-kills after a harder limit.
#
# Designed to run via cron every 15 minutes:
#   */15 * * * * /opt/tripsignal/scripts/scraper-watchdog.sh
#
# Requires: RESEND_API_KEY in environment (or sourced from .env)

set -euo pipefail

CONTAINER="tripsignal-selloff-scraper"
ALERT_AFTER_SECONDS="${ALERT_AFTER_SECONDS:-10800}"    # 3 hours
KILL_AFTER_SECONDS="${KILL_AFTER_SECONDS:-14400}"      # 4 hours
ALERT_EMAIL="${ALERT_EMAIL:-hello@tripsignal.ca}"
FROM_EMAIL="${FROM_EMAIL:-TripSignal <hello@tripsignal.ca>}"
LOCKFILE="/tmp/scraper-watchdog-alerted.lock"

# Source .env for RESEND_API_KEY if not already set
if [ -z "${RESEND_API_KEY:-}" ]; then
    ENV_FILE="${ENV_FILE:-/home/trent/Projects/tripsignal/.env}"
    if [ -f "$ENV_FILE" ]; then
        RESEND_API_KEY=$(grep '^RESEND_API_KEY=' "$ENV_FILE" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
        export RESEND_API_KEY
    fi
fi

# Check if container is running
if ! docker inspect --format='{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    # Container not running — clear any stale alert lock and exit
    rm -f "$LOCKFILE"
    exit 0
fi

# Get container start time and calculate uptime
STARTED_AT=$(docker inspect --format='{{.State.StartedAt}}' "$CONTAINER")
STARTED_EPOCH=$(date -d "$STARTED_AT" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "${STARTED_AT%%.*}" +%s 2>/dev/null)
NOW_EPOCH=$(date +%s)
UPTIME_SECONDS=$((NOW_EPOCH - STARTED_EPOCH))
UPTIME_HOURS=$((UPTIME_SECONDS / 3600))
UPTIME_MINS=$(( (UPTIME_SECONDS % 3600) / 60 ))

# Not over threshold — clear lock and exit
if [ "$UPTIME_SECONDS" -lt "$ALERT_AFTER_SECONDS" ]; then
    rm -f "$LOCKFILE"
    exit 0
fi

send_alert() {
    local subject="$1"
    local body="$2"

    if [ -z "${RESEND_API_KEY:-}" ]; then
        echo "WARNING: RESEND_API_KEY not set, cannot send alert email"
        logger -t scraper-watchdog "$subject — $body"
        return 1
    fi

    curl -s -X POST "https://api.resend.com/emails" \
        -H "Authorization: Bearer $RESEND_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$(cat <<EOF
{
    "from": "$FROM_EMAIL",
    "to": "$ALERT_EMAIL",
    "subject": "$subject",
    "text": "$body"
}
EOF
)" > /dev/null 2>&1

    logger -t scraper-watchdog "$subject"
}

# Force kill if over hard limit
if [ "$UPTIME_SECONDS" -ge "$KILL_AFTER_SECONDS" ]; then
    send_alert \
        "[CRITICAL] Scraper force-killed after ${UPTIME_HOURS}h ${UPTIME_MINS}m" \
        "The scraper container ($CONTAINER) has been running for ${UPTIME_HOURS}h ${UPTIME_MINS}m (limit: $((KILL_AFTER_SECONDS/3600))h). It has been forcefully restarted to prevent excessive resource usage and potential IP blocking."

    docker restart "$CONTAINER"
    rm -f "$LOCKFILE"
    echo "KILLED: Scraper restarted after ${UPTIME_HOURS}h ${UPTIME_MINS}m"
    exit 0
fi

# Alert (but don't kill) if over soft limit — only alert once per incident
if [ ! -f "$LOCKFILE" ]; then
    send_alert \
        "[WARNING] Scraper running for ${UPTIME_HOURS}h ${UPTIME_MINS}m" \
        "The scraper container ($CONTAINER) has been running for ${UPTIME_HOURS}h ${UPTIME_MINS}m, which exceeds the alert threshold of $((ALERT_AFTER_SECONDS/3600))h. It will be force-killed at $((KILL_AFTER_SECONDS/3600))h if it doesn't stop on its own. Check the scraper logs: docker logs --tail 50 $CONTAINER"

    touch "$LOCKFILE"
    echo "ALERT: Scraper has been running for ${UPTIME_HOURS}h ${UPTIME_MINS}m"
else
    echo "Already alerted for this incident (lock exists)"
fi
