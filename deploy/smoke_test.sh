#!/usr/bin/env bash
set -euo pipefail

# TripSignal operator smoke test
# Creates: Signal (API) -> Deal (DB) -> Match (API) -> Verifies Match (DB)
# Saves IDs to: /tmp/tripsignal_smoke_last.env

HOST="${HOST:-https://tripsignal.ca}"
OUT_IDS="${OUT_IDS:-/tmp/tripsignal_smoke_last.env}"

cd /opt/tripsignal

say() { printf "\n%s\n" "$*"; }

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1"; exit 1; }
}

require curl
require python3
require docker

say "TripSignal smoke test"
say "HOST=$HOST"
say "Saving IDs to: $OUT_IDS"

say "0) Quick health checks..."
curl -fsS "$HOST/health" >/dev/null && echo "  - Public health: OK"
docker compose exec -T caddy sh -lc "wget -qO- http://api:8000/health" >/dev/null && echo "  - Caddy -> API internal health: OK"
docker compose exec -T postgres psql -U postgres -d tripsignal -c "select 1;" >/dev/null && echo "  - Postgres: OK"

say "1) Create Signal (API)..."
curl -fsS -X POST "$HOST/api/signals" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"Smoke Test Signal",
    "departure":{"mode":"single","airports":["YQR"]},
    "destination":{"mode":"single","regions":["mexico"],"airports":[]},
    "travel_window":{"start_month":"2026-03","end_month":"2026-04","min_nights":7,"max_nights":10},
    "travellers":{"adults":2,"children_ages":[],"rooms":1},
    "budget":{"currency":"CAD","target_pp":1500,"strict":false}
  }' > /tmp/ts_signal.json

SIGNAL_ID=$(python3 - <<'PY'
import json
data=json.load(open("/tmp/ts_signal.json"))
print(data["id"])
PY
)
echo "  - SIGNAL_ID=$SIGNAL_ID"

say "2) Read Signal back (API)..."
curl -fsS "$HOST/api/signals/$SIGNAL_ID" >/dev/null && echo "  - Read-back: OK"

say "3) Insert Deal (DB)..."
DEAL_ID=$(
  docker compose exec -T postgres psql -U postgres -d tripsignal -X -q -t -A -c "
insert into deals
(provider, origin, destination, depart_date, return_date, price_cents, currency, deeplink_url, airline, cabin, stops, dedupe_key)
values
('manual_smoke','YQR','CUN','2026-03-10','2026-03-17',49900,'CAD','https://example.com','WS','economy',0,'manual_smoke_YQR_CUN_2026-03-10_2026-03-17_49900')
on conflict (dedupe_key) do update set price_cents = excluded.price_cents
returning id;
" | head -n 1 | tr -d '[:space:]'
)
echo "  - DEAL_ID=$DEAL_ID"

say "4) Create Match (API links Signal <-> Deal)..."
curl -fsS -X POST "$HOST/api/signals/$SIGNAL_ID/matches" \
  -H "Content-Type: application/json" \
  -d "{\"deal_id\":\"$DEAL_ID\"}" > /tmp/ts_match.json

MATCH_ID=$(python3 - <<'PY'
import json
data=json.load(open("/tmp/ts_match.json"))
print(data["id"])
PY
)
echo "  - MATCH_ID=$MATCH_ID"

say "5) Verify Match exists (DB)..."
docker compose exec -T postgres psql -U postgres -d tripsignal -c "
select id, signal_id, deal_id, matched_at
from deal_matches
where id = '$MATCH_ID';
" | sed -n '1,20p'

say "6) Save IDs for cleanup..."
cat > "$OUT_IDS" <<EOF
SIGNAL_ID=$SIGNAL_ID
DEAL_ID=$DEAL_ID
MATCH_ID=$MATCH_ID
HOST=$HOST
EOF

echo "  - Saved: $OUT_IDS"

say "PASS ✅  Signal -> Deal -> Match -> DB verify succeeded."
echo
echo "Next:"
echo "  Cleanup: /opt/tripsignal/deploy/cleanup_smoke.sh"
