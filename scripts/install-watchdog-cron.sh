#!/usr/bin/env bash
# Installs the scraper watchdog cron job.
# Run once on the production server:
#   sudo bash /opt/tripsignal/scripts/install-watchdog-cron.sh

set -euo pipefail

CRON_LINE="*/15 * * * * /opt/tripsignal/scripts/scraper-watchdog.sh >> /var/log/scraper-watchdog.log 2>&1"
CRON_USER="${1:-trent}"

# Check if already installed
if crontab -u "$CRON_USER" -l 2>/dev/null | grep -qF "scraper-watchdog.sh"; then
    echo "Watchdog cron already installed for user $CRON_USER"
    crontab -u "$CRON_USER" -l | grep scraper-watchdog
    exit 0
fi

# Add to existing crontab
(crontab -u "$CRON_USER" -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -u "$CRON_USER" -

echo "Installed watchdog cron for user $CRON_USER:"
echo "  $CRON_LINE"
echo ""
echo "Logs will be at /var/log/scraper-watchdog.log"
echo "To verify: crontab -u $CRON_USER -l"
