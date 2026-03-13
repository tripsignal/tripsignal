# TripSignal — Project Instructions

## Architecture

- **Backend**: FastAPI + SQLAlchemy + PostgreSQL (Python 3.12)
- **Frontend**: Next.js 16 + React 19 + Tailwind CSS 4 + shadcn/ui
- **Auth**: Clerk (`@clerk/nextjs`)
- **Billing**: Stripe (checkout, portal, webhook)
- **Email**: Resend API (`hello@tripsignal.ca`)
- **Reverse Proxy**: Caddy — `/api/*` → `api:8000`, everything else → `tripsignal-frontend:3000`
- **All services run in Docker** on a single VPS

## Deployment

### Backend (API, scraper, notifications worker)

Code is **baked into Docker images** (no volume mounts). Must rebuild to deploy changes:

```bash
cd /opt/tripsignal
docker compose build api selloff_scraper notifications_worker
docker compose up -d api selloff_scraper notifications_worker
```

All three services share the same Dockerfile and codebase. When you change backend code, rebuild ALL of them — otherwise containers run stale code.

### Frontend

The frontend is a **standalone Docker container** (NOT in docker-compose):

```bash
cd /opt/tripsignal/frontend
docker build -f Dockerfile.prod -t tripsignal-frontend:latest .
docker stop tripsignal-frontend && docker rm tripsignal-frontend
docker run -d --name tripsignal-frontend \
  --network tripsignal_tripsignal-network \
  -p 3000:3000 \
  --env-file /opt/tripsignal/frontend/.env.production \
  tripsignal-frontend:latest
```

### Database

PostgreSQL runs in Docker. Access via:
```bash
docker exec tripsignal-postgres psql -U postgres -d tripsignal
```

Apply migrations via Alembic:
```bash
docker exec tripsignal-api alembic upgrade head
```

## Key Files

| File | Purpose |
|------|---------|
| `backend/app/workers/selloff_scraper.py` | Scraper, deal matching, email sending, unsub tokens |
| `backend/app/api/signals.py` | Signal CRUD + background matching on create |
| `backend/app/api/routes/users.py` | User prefs, sync, terms, delete (8 endpoints) |
| `backend/app/api/routes/unsubscribe.py` | Token-based unsubscribe (public, no auth) |
| `backend/app/db/models/user.py` | User model — **must match actual DB schema (25 columns)** |
| `frontend/middleware.ts` | Public routes, beta exemption, terms/activation redirects |
| `frontend/app/signals/new/page.tsx` | Signal creation form |
| `frontend/.env.production` | Frontend env vars for Docker build |
| `deploy/Caddyfile` | Reverse proxy config |

## Frontend Patterns

- **Public routes** must be added to BOTH `isPublicRoute` and `isBetaExempt` in `middleware.ts`
- **Frontend proxy routes** (`/user/prefs`, `/user/sync`, `/user/me`, `/user/delete`) proxy to backend endpoints. Both sides must have matching implementations.
- **`useSearchParams()`** requires a `<Suspense>` boundary or the Next.js static build will fail
- **Clerk `<SignedOut>` components** render client-side only — they won't appear in `curl` tests
- **Design system colors**: `ts-orange`, `ts-charcoal`, `ts-gray`, `ts-muted`, `ts-border`
- **Components**: shadcn/ui — Card, Button, Accordion, Dialog, Calendar, Popover

## Deal Matching Logic

The matching function is in `selloff_scraper.py` → `match_deal_to_signals()`. A copy also exists in `signals.py` → `_match_signal_against_deals()` for instant matching on signal creation. **Keep both in sync.**

Key rules:
- `start_date` = **earliest departure**, `end_date` = **latest return** (not latest departure)
- Must check both: `deal.depart_date >= start_date` AND `deal.return_date <= end_date`
- Only match `Deal.is_active == True` deals (matches website behavior)
- Signal uses `Signal.status == "active"` (not `is_active`)
- New signals run background matching immediately after creation (no email sent, deals just appear)

## Email System

- **One consolidated email per user** (not per signal) via `send_user_digest_email()`
- **Subject lines**: curiosity-driven, no price in subject (4 states: drop single/multi, new single/multi)
- **Hero deal**: biggest price drop, not cheapest
- **No deeplinks** to deal providers — all links go to `tripsignal.ca`
- **No unverified data** (no `discount_pct` or marketing claims)
- **Unsubscribe**: token-based HMAC links, no expiration (CASL/CAN-SPAM compliance)
- **Footer**: `Manage signals · Unsubscribe` (unsubscribe link must be its own visible word)
- Price delta tracking via `DealPriceHistory` table + `_build_price_delta_map()` using SQL `LAG()` window function

## Common Gotchas

1. **SQLAlchemy `None` vs `null()`**: When inserting a row with a `server_default`, Python `None` lets the default kick in. Use `from sqlalchemy import null` and `null()` to explicitly insert NULL.

2. **Always `db.rollback()` in exception handlers** after a failed `db.commit()`. Without rollback, the session stays dirty and poisons all subsequent operations.

3. **Rebuild ALL backend containers** when changing shared code. The scraper, API, and notifications worker all build from the same image but run independently.

4. **User model must match the database**. The DB has 25 columns. If the SQLAlchemy model is missing columns, queries may fail silently or endpoints may 404.

5. **AIRPORT_CITY_MAP** in `selloff_scraper.py` maps IATA codes to readable Canadian city names for emails.

6. **`validate_user_for_email()`** checks: user exists → not opted out → has active plan (pro or active trial). All three must pass before sending.

7. **CRITICAL: Never run more than one deal scraper at a time.** Concurrent scrapers risk getting proxy IPs blocked, which would disable deal ingestion entirely. The SellOff scraper enforces this via a Postgres advisory lock (`pg_try_advisory_lock`). Never remove or bypass this lock. Never manually exec a scraper without confirming no other scraper is running.
