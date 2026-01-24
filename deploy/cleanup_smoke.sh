#!/usr/bin/env bash
set -euo pipefail

# TripSignal smoke test cleanup
# Reads IDs from /tmp/tripsignal_smoke_last.env (or $OUT_IDS) and deletes rows.

OUT_IDS="${OUT_IDS:-/tmp/tripsignal_smoke_last.env}"

cd /opt/tripsignal

if [[ ! -f "$OUT_IDS" ]]; then
  echo "No ID file found at: $OUT_IDS"
  echo "Run the smoke test first: /opt/tripsignal/deploy/smoke_test.sh"
  exit 1
fi

# shellcheck disable=SC1090
source "$OUT_IDS"

echo "Cleaning up using:"
echo "  SIGNAL_ID=$SIGNAL_ID"
echo "  DEAL_ID=$DEAL_ID"
echo "  MATCH_ID=$MATCH_ID"
echo

docker compose exec -T postgres psql -U postgres -d tripsignal -c "
delete from deal_matches where id = '$MATCH_ID';
delete from deals where id = '$DEAL_ID';
delete from signals where id = '$SIGNAL_ID';
"

echo
echo "Cleanup complete ✅"
echo "Removing ID file: $OUT_IDS"
rm -f "$OUT_IDS"
