# TripSignal -- Technical Documentation

Generated: 2026-03-07
Codebase: Backend (`tripsignal/backend/`) + Frontend (`tripsignal-ui/`)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [Backend API](#3-backend-api)
4. [Backend Services](#4-backend-services)
5. [Frontend Pages](#5-frontend-pages)
6. [Frontend Components](#6-frontend-components)
7. [Next.js Proxy Routes](#7-nextjs-proxy-routes)
8. [Data & Utilities](#8-data--utilities)
9. [Infrastructure](#9-infrastructure)
10. [Auth Flow](#10-auth-flow)
11. [Billing Flow](#11-billing-flow)
12. [Scraper -> Alert Pipeline](#12-scraper---alert-pipeline)
13. [Known Gotchas & Tribal Knowledge](#13-known-gotchas--tribal-knowledge)

---

## 1. Architecture Overview

### System Diagram

```
Browser
  |
  v
Caddy (tripsignal-caddy) -- reverse proxy, TLS
  |
  +--  /api/*  -->  FastAPI (tripsignal-api:8000)
  |
  +--  /*      -->  Next.js (tripsignal-frontend:3000)

                    PostgreSQL (tripsignal-postgres:5432)
                       ^
                       |
                    FastAPI / Workers

Workers (all share same Docker image):
  - tripsignal-api           -- uvicorn, serves HTTP
  - scrape_orchestrator      -- runs SellOff + RedTag scrapers in staggered sequence
  - notifications_worker     -- polls notifications_outbox, sends via Resend
  - lifecycle_worker         -- trial expiry emails, welcome emails, engagement
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16.1.6, React 19.2.3, Tailwind CSS 4, shadcn/ui |
| Backend | FastAPI 0.125.0, SQLAlchemy 2.0.36, Python 3.12 |
| Database | PostgreSQL 16 (Alpine) |
| Auth | Clerk (`@clerk/nextjs` 6.37.2) |
| Billing | Stripe (checkout sessions, customer portal, webhooks) |
| Email | Resend API (from `hello@tripsignal.ca`) |
| Reverse Proxy | Caddy 2 (auto TLS, gzip/zstd) |
| Hosting | Single VPS, all Docker containers |
| Migrations | Alembic |
| Rate Limiting | slowapi |
| Rich Text (Admin) | TipTap |
| Analytics | Google Analytics (G-2DNWZ6VJ6X) |

### Request Flow

1. Browser hits `tripsignal.ca`
2. Caddy terminates TLS, compresses response
3. If path starts with `/api/` -> reverse proxy to `api:8000` (FastAPI)
4. Everything else -> reverse proxy to `tripsignal-frontend:3000` (Next.js)
5. Next.js proxy routes (e.g., `/user/sync`) make internal HTTP calls to `http://api:8000` (Docker network)
6. FastAPI authenticates via Clerk JWT (Bearer token) or legacy `x-clerk-user-id` header

---

## 2. Database Schema

### Table: `users`
**File:** `backend/app/db/models/user.py`

Primary user table. ~40 columns. Keyed by UUID, linked to Clerk via `clerk_id`.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | Internal user ID |
| clerk_id | Text (unique, indexed) | Clerk external ID |
| email | Text | Email address (synced via Clerk webhook) |
| role | Text | `'user'` or `'admin'` |
| plan_type | Text | `'free'` or `'pro'` |
| plan_status | Text | `'active'`, `'cancelled'` |
| trial_ends_at | Timestamptz | End of 14-day free trial |
| stripe_customer_id | Text | Stripe customer ID |
| stripe_subscription_id | Text | Stripe subscription ID |
| stripe_subscription_status | Text | Stripe status string |
| subscription_current_period_end | Timestamptz | Current billing period end |
| terms_accepted_at | Timestamptz | When user accepted terms |
| terms_version | Text | Version of terms accepted |
| privacy_accepted_at | Timestamptz | When user accepted privacy policy |
| privacy_version | Text | Version of privacy policy accepted |
| email_enabled | Boolean | Email notifications enabled (default true) |
| sms_enabled | Boolean | SMS notifications enabled (default false) |
| email_opt_out | Boolean | User opted out of all emails |
| pro_activation_completed_at | Timestamptz | When pro onboarding was completed |
| last_login_at | Timestamptz | Last login timestamp |
| login_count | Integer | Total login count |
| last_login_ip | Text | IP from X-Forwarded-For |
| last_login_user_agent | Text | Browser user agent |
| is_test_user | Boolean (indexed) | Admin-flagged test user |
| deleted_at | Timestamptz (indexed) | Soft-delete timestamp |
| deleted_by | Text | `'admin'` or `'user'` |
| deleted_reason | Text | Reason code |
| deleted_reason_other | Text | Free-text reason |
| stripe_canceled_at | Timestamptz | When Stripe sub was canceled |
| trial_auto_extended_at | Timestamptz | If trial was auto-extended |
| trial_expired_email_sent_at | Timestamptz | Idempotency guard for trial expiry email |
| welcome_email_sent_at | Timestamptz | Idempotency guard for welcome email |
| trial_expiring_email_sent_at | Timestamptz | Idempotency guard |
| no_signal_email_sent_at | Timestamptz | Idempotency guard |
| email_mode | Text | `'active'`, etc. (engagement tracking) |
| last_email_opened_at | Timestamptz | Email open tracking |
| last_email_clicked_at | Timestamptz | Email click tracking |
| notification_delivery_frequency | Text | `'all'`, `'morning'`, `'noon'`, `'evening'` (comma-separated) |
| timezone | Text | User timezone (default `'America/Toronto'`) |
| notification_weekly_summary | Boolean | Weekly summary enabled (Pro only) |
| quiet_hours_enabled | Boolean | Quiet hours toggle |
| quiet_hours_start | Text | Default `'21:00'` |
| quiet_hours_end | Text | Default `'08:00'` |
| created_at | Timestamptz | Row creation time |
| updated_at | Timestamptz | Last update time |

Properties: `frequency_windows` (list from comma-separated string), `is_instant_delivery` (checks for `'all'`).

### Table: `signals`
**File:** `backend/app/db/models/signal.py`

User-created travel monitoring signals. Each signal defines departure airports, destination regions, travel window, budget, etc.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | Signal ID |
| name | Text | User-given name (e.g., "Mexico spring break") |
| status | Text | `'active'`, `'paused'`, `'payment_paused'`, `'deleted'` |
| departure_airports | Text[] | IATA codes (e.g., `{YYZ,YOW}`) -- mirrored from config for fast matching |
| destination_regions | Text[] | Region slugs (e.g., `{riviera_maya,cancun}`) -- mirrored from config |
| user_id | UUID (FK -> users, CASCADE) | Owner |
| config | JSONB | Full signal configuration (departure, destination, travel_window, travellers, budget, notifications, preferences) |
| last_check_min_price | Integer | Min price found in last scrape cycle |
| last_check_at | Timestamptz | When signal was last checked |
| all_time_low_price | Integer | Lowest price ever seen (cents) |
| all_time_low_at | Timestamptz | When ATL was recorded |
| no_match_email_sent_at | Timestamptz | Idempotency for no-match notification |
| created_at / updated_at | Timestamptz | Timestamps |

Relationships: `deal_matches` (one-to-many), `runs` (one-to-many SignalRun).

### Table: `deals`
**File:** `backend/app/db/models/deal.py`

All-inclusive travel deals scraped from providers (SellOff, RedTag).

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | Deal ID |
| provider | Text (indexed) | `'selloff'` or `'redtag'` |
| origin | Text (indexed) | IATA code (e.g., `'YYZ'`) |
| destination | Text (indexed) | Region slug (e.g., `'riviera_maya'`) |
| depart_date | Date (indexed) | Departure date |
| return_date | Date (indexed) | Return date |
| price_cents | Integer (indexed) | Per-person price in cents CAD |
| currency | Text | Default `'CAD'` |
| deeplink_url | Text | URL to provider's booking page |
| airline | Text | Airline name |
| cabin | Text | Cabin class |
| stops | Integer | Number of stops |
| found_at | Timestamptz (indexed) | When first scraped |
| dedupe_key | Text (unique, indexed) | Composite key for deduplication |
| is_active | Boolean (indexed) | Whether deal is still live |
| deactivated_at | Timestamptz | When deal was deactivated |
| last_seen_at | Timestamptz | Last scrape cycle that saw this deal |
| missed_cycles | Integer | Consecutive scrape cycles where deal wasn't found |
| hotel_name | Text | Hotel/resort name |
| hotel_id | Text | Provider hotel ID (used for hotel_links lookup) |
| discount_pct | Integer | Provider-reported discount |
| destination_str | Text | Human-readable destination string |
| star_rating | Float | Hotel star rating |

Relationships: `price_history` (DealPriceHistory), `deal_matches` (DealMatch).

### Table: `deal_matches`
**File:** `backend/app/db/models/deal_match.py`

Junction table linking deals to signals. Created during scraper matching or on signal creation.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | Match ID |
| signal_id | UUID (FK -> signals, CASCADE) | Which signal |
| deal_id | UUID (FK -> deals, CASCADE) | Which deal |
| run_id | UUID (FK -> signal_runs, SET NULL) | Which run created this match |
| matched_at | Timestamptz | When matched |
| is_favourite | Boolean | User-favourited flag |
| notified_at | Timestamptz | When user was notified about this match |
| major_drop_alert_sent_at | Timestamptz | Major price drop alert tracking |
| price_per_night_cents | Integer | Calculated price per night |
| deal_seen_at | Timestamptz | First seen |
| deal_last_seen_at | Timestamptz | Last confirmed active |
| value_label | String(30) | Market-based value label (scored at match time) |

Unique constraint: `(signal_id, deal_id)` -- prevents duplicate matches.

### Table: `deal_price_history`
**File:** `backend/app/db/models/deal_price_history.py`

Price snapshots recorded each scrape cycle. Used for trend analysis and price drop detection.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | |
| deal_id | UUID (FK -> deals, CASCADE) | |
| price_cents | Integer | Price at this point in time |
| recorded_at | Timestamptz (indexed) | When recorded |

### Table: `signal_runs`
**File:** `backend/app/db/models/signal_run.py`

Tracks each matching execution per signal (scheduled, manual, or test).

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | Run ID |
| signal_id | UUID (FK -> signals, CASCADE) | |
| run_type | Enum | `morning`, `afternoon`, `manual`, `test` |
| status | Enum | `running`, `success`, `failed` |
| started_at | Timestamptz | |
| completed_at | Timestamptz | |
| matches_created_count | Integer | |
| error_message | Text | |

### Table: `scrape_runs`
**File:** `backend/app/db/models/scrape_run.py`

Tracks each scraper execution cycle (aggregated across all signals).

| Column | Type | Purpose |
|--------|------|---------|
| id | Integer (PK, auto) | |
| started_at | Timestamptz | |
| completed_at | Timestamptz | |
| total_deals | Integer | Deals found this cycle |
| total_matches | Integer | Matches created |
| error_count | Integer | |
| status | Text | `'running'`, `'completed'`, `'error'` |
| error_log | JSONB | Array of error details |
| deals_deactivated | Integer | Deals marked inactive |
| deals_expired | Integer | Deals expired |
| proxy_ip | Text | IP used for scraping |
| proxy_geo | Text | Geo of proxy |
| provider | Text | `'selloff'`, `'redtag'` |

### Table: `notifications_outbox`
**File:** `backend/app/db/models/notification_outbox.py`

Outbox pattern for reliable notification delivery. The `notifications_log_worker` polls this table.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | |
| created_at / updated_at | Timestamptz | |
| sent_at | Timestamptz | When actually sent |
| status | String(20) | `'pending'`, `'sent'`, `'failed'` |
| attempts | Integer | Retry count |
| next_attempt_at | Timestamptz | |
| last_error | Text | |
| signal_id | UUID (FK -> signals, SET NULL) | |
| match_id | UUID | |
| channel | String(20) | `'log'`, `'email'` |
| to_email | Text | |
| subject | Text | |
| body_text | Text | HTML body |
| opened_at | Timestamptz | Tracking pixel open time |
| open_count | Integer | Number of opens |

### Table: `email_log`
**File:** `backend/app/db/models/email_log.py`

Audit log for all emails sent. Provides idempotency via `idempotency_key` (unique).

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | |
| user_id | UUID (FK -> users, SET NULL) | |
| email_type | Text (indexed) | e.g., `'WELCOME_EMAIL'`, `'DEAL_ALERT'` |
| category | Text | `'transactional'`, `'marketing'` |
| idempotency_key | Text (unique) | Prevents duplicate sends |
| to_email | Text | |
| subject | Text | |
| provider_message_id | Text | Resend message ID |
| status | Text | `'sent'`, `'suppressed'` |
| suppressed_reason | Text | Why email was suppressed |
| metadata_json | JSONB | |
| sent_at | Timestamptz | |
| created_at | Timestamptz | |

### Table: `email_queue`
**File:** `backend/app/db/models/email_queue.py`

Rate-limited, prioritized email delivery queue.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | |
| priority | SmallInt | 1=critical, 2=high, 3=low |
| to_email, subject, html_body | Text | Email content |
| email_log_id | UUID | Link back to email_log |
| attempts / max_attempts | Integer | Retry tracking (max 3) |
| last_attempt_at, next_retry_at | Timestamptz | |
| status | Text | `'queued'`, `'sending'`, `'sent'`, `'failed'`, `'dead'` |
| error_message | Text | |
| provider_message_id | Text | |
| email_type | Text | |
| user_id | UUID | |
| metadata_json | JSONB | |
| created_at, sent_at | Timestamptz | |

### Table: `email_template_overrides`
**File:** `backend/app/db/models/email_template_override.py`

Admin-editable email template overrides. When set, override Python-default templates.

| Column | Type | Purpose |
|--------|------|---------|
| email_type | Text (PK) | e.g., `'WELCOME_EMAIL'` |
| subject | Text | Override subject (null = use default) |
| body_html | Text | Override HTML body (null = use default) |
| updated_at | Timestamptz | |
| updated_by | Text | Admin identifier |

### Table: `hotel_links`
**File:** `backend/app/db/models/hotel_link.py`

External URLs (TripAdvisor, etc.) keyed by SellOff hotel_id.

| Column | Type | Purpose |
|--------|------|---------|
| hotel_id | Text (PK) | Provider hotel ID |
| hotel_name | Text | |
| destination | Text | |
| star_rating | Numeric(2,1) | |
| tripadvisor_url | Text | |
| created_at / updated_at | Timestamptz | |

### Table: `market_snapshots`
**File:** `backend/app/db/models/market_snapshot.py`

Daily compressed market summaries. Not yet exposed to UI -- future analytics foundation.

| Column | Type | Purpose |
|--------|------|---------|
| id | Integer (PK, auto) | |
| snapshot_date | Date (indexed) | |
| departure_airport | Text (indexed) | |
| destination_region | Text (indexed) | |
| duration_bucket | Text | e.g., `'7'`, `'10-14'` |
| star_bucket | Text | e.g., `'3'`, `'4+'` |
| package_count | Integer | |
| unique_resort_count | Integer | |
| min_price / median_price / p75_price / max_price | Integer | |
| price_stddev | Float | |
| created_at | Timestamptz | |

### Table: `route_intel_cache`
**File:** `backend/app/db/models/route_intel_cache.py`

Computed intelligence per (origin, destination_region) pair. Refreshed after each scrape cycle.

| Column | Type | Purpose |
|--------|------|---------|
| origin | Text (PK) | IATA code |
| destination_region | Text (PK) | Region slug |
| cheapest_depart_week / cheapest_week_avg_cents | Date / Integer | Best departure window |
| priciest_depart_week / priciest_week_avg_cents | Date / Integer | Worst departure window |
| current_week_avg_cents / prev_week_avg_cents | Integer | Week-over-week comparison |
| week_over_week_pct | Float | Percentage change |
| avg_price_4plus_weeks_cents | Integer | Booking countdown pressure |
| avg_price_2to4_weeks_cents | Integer | |
| avg_price_under_2_weeks_cents | Integer | |
| late_booking_premium_pct | Float | |
| total_deals_analyzed | Integer | |
| cache_refreshed_at | Timestamptz | |

### Table: `signal_intel_cache`
**File:** `backend/app/db/models/signal_intel_cache.py`

Computed intelligence per signal. Refreshed after each scrape cycle.

| Column | Type | Purpose |
|--------|------|---------|
| signal_id | UUID (PK, FK -> signals) | |
| min_price_ever_cents | Integer | All-time low |
| current_deal_percentile | Float | Where current price sits in distribution |
| trend_direction | Text | `'stable'`, `'falling'`, `'rising'` |
| trend_consecutive_weeks | Integer | |
| trend_velocity | Text | `'accelerating'`, `'decelerating'`, `'steady'` |
| trend_last_week_delta_cents / trend_prev_week_delta_cents | Integer | |
| trend_inflection | Boolean | Prices reversing direction |
| inflection_pct_change | Float | |
| best_value_nights | Integer | Optimal night count for value |
| best_value_pct_saving | Float | |
| star_price_anomaly_pct | Float | Price-per-star anomaly detection |
| hero_star_rating | Float | |
| floor_proximity_pct | Float | How close to all-time low |
| value_score | Integer | 0-100 price-to-quality score |
| total_matches | Integer | |
| cache_refreshed_at | Timestamptz | |

### Table: `stripe_events`
**File:** `backend/app/db/models/stripe_event.py`

Stores Stripe webhook events for deduplication and audit.

| Column | Type | Purpose |
|--------|------|---------|
| id | UUID (PK) | |
| stripe_event_id | Text (unique, indexed) | Stripe event ID |
| event_type | Text (indexed) | e.g., `'checkout.session.completed'` |
| payload | JSONB | Full event data object |
| received_at | Timestamptz | |
| processed_at | Timestamptz | |
| processing_error | Text | |

### Table: `system_config`
**File:** `backend/app/db/models/system_config.py`

Key-value store for system configuration.

| Column | Type | Purpose |
|--------|------|---------|
| key | String (PK) | e.g., `'next_scan_at'` |
| value | Text | |
| updated_at | Timestamptz | |

### Database Infrastructure

**File:** `backend/app/db/session.py` -- SQLAlchemy session factory, provides `get_db()` generator for FastAPI dependency injection.

**File:** `backend/app/db/base.py` -- Declares `Base = declarative_base()` for all models.

---

## 3. Backend API

### 3.1 Authentication & User Management

**File:** `backend/app/api/routes/users.py` (prefix: `/users`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/users/by-clerk-id/{clerk_id}` | JWT | Returns user profile (plan, trial, notification prefs). Verifies caller is the same user. |
| POST | `/users/sync` | JWT | Called on every sign-in. Creates user if new, updates login tracking (IP, UA, timezone, count). Rate limited: 30/min. |
| GET | `/users/terms-status?clerk_id=xxx` | None | Returns whether user has accepted terms. Used by middleware. Returns `true` for unknown users (don't block). |
| POST | `/users/accept-terms` | None | Records terms/privacy acceptance. Starts 14-day free trial if user is on free plan. Triggers welcome email. |
| GET | `/users/prefs` | JWT | Returns notification preferences and plan details. |
| PUT | `/users/prefs` | JWT | Updates notification preferences (frequency, email enabled, SMS, timezone, pro activation). Validates frequency windows. |
| DELETE | `/users/me` | JWT | Soft-deletes user account. Cancels Stripe subscription, sends goodbye email. |
| POST | `/users/cancel-subscription` | JWT | Marks plan_status as 'cancelled' locally. |

### 3.2 Signals

**File:** `backend/app/api/signals.py` (prefix: `/api/signals`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/signals` | JWT | Create signal. Enforces per-user limits (Free: 1, Pro: 10). Immediately matches against all active deals. Triggers first-signal email if it's the user's first. Rate limited: 10/min. |
| GET | `/api/signals` | JWT | List all user's signals with match counts, intel cache data, market stats, spectrum data, and empty-state diagnostics. |
| GET | `/api/signals/{signal_id}` | JWT | Get single signal by ID (must be owned by caller). |
| PATCH | `/api/signals/{signal_id}` | JWT | Update signal. Deep-merges config. Re-matches deals if search criteria changed. Updates mirrored columns. |
| DELETE | `/api/signals/{signal_id}` | JWT | Hard-delete signal (cascades to deal_matches). |

**Matching logic** (`_match_signal_against_deals`): Iterates all active deals, checks: airport match -> region match -> travel window (exact dates or month range) -> duration (min/max nights) -> star rating -> budget. Creates DealMatch rows with value labels.

### 3.3 Deal Matches

**File:** `backend/app/api/routes/deal_matches.py` (prefix: `/api/signals`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/signals/{signal_id}/matches` | JWT | Returns active matched deals for a signal. Includes price trends (batch N+1 fix), TripAdvisor URLs from hotel_links. Sorted: favourites first, then by matched_at desc. |
| PATCH | `/api/signals/{signal_id}/matches/{match_id}/favourite` | JWT | Toggle favourite status on a deal match. |
| POST | `/api/signals/{signal_id}/matches` | Internal | Create a match between a signal and a deal (idempotent). Creates a SignalRun, creates NotificationOutbox entry. |

### 3.4 Billing

**File:** `backend/app/api/routes/billing.py` (prefix: `/api/billing`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/billing/checkout` | JWT | Creates Stripe checkout session for Pro upgrade. Rate limited: 5/min. Returns checkout URL. |
| POST | `/api/billing/portal` | JWT | Creates Stripe billing portal session. Returns portal URL. |
| POST | `/api/billing/webhook` | Stripe signature | Handles Stripe webhook events. Deduplicates via `stripe_events` table. |

**Webhook event handlers:**
- `checkout.session.completed`: Upgrades user to Pro, reactivates payment-paused signals, triggers pro_activated email.
- `customer.subscription.updated` / `deleted`: Updates subscription status. On deletion, downgrades to free and triggers canceled email.
- `invoice.payment_failed`: Pauses all active signals (status = `'payment_paused'`), triggers payment_failed email.

### 3.5 Admin

**File:** `backend/app/api/routes/admin.py` (prefix: `/admin`)

All endpoints require `X-Admin-Token` header (HMAC comparison).

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/admin/test-email` | Enqueue test notification email |
| GET | `/admin/debug/outbox` | Debug notification outbox |
| GET | `/admin/health` | System health (DB, Stripe, users, deals, signals counts) |
| GET | `/admin/signals` | List all signals with details |
| GET | `/admin/users/by-clerk-id/{clerk_id}` | Look up user by Clerk ID |
| GET | `/admin/users` | List all users with signals and match counts |
| GET | `/admin/users-unified` | Full user list with deal match details, email stats |
| PATCH | `/admin/users/{user_id}/toggle-test` | Toggle test user flag |
| PATCH | `/admin/users/{user_id}/set-plan` | Change user plan (free/pro) |
| PATCH | `/admin/users/{user_id}/set-status` | Change plan status |
| DELETE | `/admin/users/{user_id}` | Soft-delete user |
| POST | `/admin/users/{user_id}/undelete` | Restore soft-deleted user |
| DELETE | `/admin/users/{user_id}/hard-delete` | Permanent delete (admin only) |
| PATCH | `/admin/users/{user_id}/extend-trial` | Extend trial by N days |
| PATCH | `/admin/users/{user_id}/reset-trial` | Reset trial to 14 days from now |
| GET | `/admin/users/{user_id}/feedback` | Get user's delete feedback |
| POST | `/admin/run-trial-expiry` | Manually trigger trial expiry check |
| GET | `/admin/notifications` | List recent notifications with filters |
| GET | `/admin/scrape-runs` | List scrape runs with stats |
| GET | `/admin/deals` | List deals with filters (destination, active, date) |
| GET | `/admin/hotels` | List all hotels with links |
| PUT | `/admin/hotels/{hotel_id}` | Update hotel links (TripAdvisor URL, star rating) |
| GET | `/admin/email-types` | List all email template types |
| POST | `/admin/send-test-email` | Send a test email (with template rendering) |
| POST | `/admin/preview-email` | Preview rendered email template |
| GET | `/admin/email-templates` | List all template overrides |
| GET | `/admin/email-templates/{email_type}` | Get specific template override |
| PUT | `/admin/email-templates/{email_type}` | Create/update template override |
| DELETE | `/admin/email-templates/{email_type}` | Delete template override (revert to Python default) |
| GET | `/admin/email-queue/stats` | Email queue statistics |
| GET | `/admin/email-queue/items` | List queue items |
| POST | `/admin/email-queue/retry-dead` | Retry dead-lettered emails |
| POST | `/admin/email-queue/pause` | Pause email queue |
| POST | `/admin/email-queue/resume` | Resume email queue |
| POST | `/admin/email-queue/flush` | Flush queued emails |
| POST | `/admin/email-queue/drain` | Drain (send all) queued emails |
| POST | `/admin/backfill-value-labels` | Backfill value labels on existing deal matches |

### 3.6 Scout (Travel Intelligence)

**File:** `backend/app/api/routes/scout.py` (prefix: `/api/scout`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/scout/verdict` | JWT | Overall "should I book now?" assessment. Mood: positive/caution/neutral. |
| GET | `/api/scout/destinations` | JWT | Per-destination price intelligence with 14-day sparkline data. |
| GET | `/api/scout/signal-health` | JWT | Per-signal health: matches, trend, freshness. |
| GET | `/api/scout/price-baseline` | JWT | Price distribution spectrum per signal. |
| GET | `/api/scout/action-queue` | JWT | Prioritized action items (price drops, near-floor, review deals). |
| GET | `/api/scout/market-context` | JWT | Platform-wide market context (total deals, top destinations, route trends). |
| GET | `/api/scout/what-is-a-good-price` | JWT | Educational price ranges for user's routes. |
| GET | `/api/scout/insights` | JWT | **Unified endpoint** for Scout page. Returns briefing, action items, best deals, price context, book windows, next scan. Single request replaces multiple API calls. |

### 3.7 Market Intelligence

**File:** `backend/app/api/routes/market.py` (prefix: `/api/market`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/market/overview` | None | Public market overview (total packages, resorts, departures, destinations, price drops). |
| GET | `/api/market/events` | None | Today's market events and movers. |
| GET | `/api/market/top-destinations/{origin}` | None | Top 3 destinations by deal count for a departure airport. |
| GET | `/api/market/signal/{signal_id}/intelligence` | JWT | Per-signal market stats, spectrum, trigger likelihood, empty-state insights. |
| POST | `/api/market/draft/insights` | JWT | Market intelligence for a draft signal during Create Signal flow. |

### 3.8 Public Deal Page

**File:** `backend/app/api/routes/deal_public.py` (prefix: `/api/deals`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/deals/{deal_id}/public` | None | Public deal page data. Returns deal details, market value score, price delta. No auth required. |

### 3.9 Unsubscribe

**File:** `backend/app/api/routes/unsubscribe.py` (prefix: `/api/unsubscribe`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/unsubscribe?token=xxx` | Token (HMAC) | Returns masked email, opt-out status, preferences. No Clerk auth. |
| POST | `/api/unsubscribe` | Token (HMAC) | Update preferences: opt_out, resubscribe, change_frequency, update_prefs. |

### 3.10 Clerk Webhook

**File:** `backend/app/api/routes/clerk_webhook.py`

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/clerk/webhook` | Svix signature | Handles `user.created` and `user.updated` events. Syncs email from Clerk to DB. Creates user row on `user.created` if needed. |

### 3.11 Scraper Lab (Admin)

**File:** `backend/app/api/routes/scraper_lab.py` (prefix: `/admin/scraper-lab`)

All endpoints require `X-Admin-Token`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/admin/scraper-lab/health-check` | Test URL fetchability and regex patterns |
| POST | `/admin/scraper-lab/test-scrape` | Parse HTML and extract deals (no DB insert) |
| POST | `/admin/scraper-lab/dry-run` | Full scrape simulation with DB action simulation and signal match simulation |

### 3.12 Resend Webhooks

**File:** `backend/app/api/routes/resend_webhooks.py`

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/resend/webhook` | Svix signature | Handles Resend email delivery events (delivered, bounced, complained, opened, clicked). Updates email_log and user engagement tracking. |

### 3.12 System Endpoints (in main.py)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/system/next-scan` | Admin token | Scraper registers next scan time. Upserts to system_config. |
| GET | `/api/system/next-scan` | None | Returns next scheduled scrape time. |
| POST | `/api/system/scrape-started` | Admin token | Scraper registers cycle start. Creates ScrapeRun row. |
| POST | `/api/system/collection-complete` | Admin token | Scraper registers cycle end. Updates ScrapeRun with stats. |
| GET | `/api/notifications/{id}/pixel.png` | None | Tracking pixel. Returns 1x1 transparent PNG and records open. |
| GET | `/health` | None | Health check with DB ping. |
| GET | `/` | None | Root endpoint, returns API version. |

---

## 4. Backend Services

### 4.1 Scraper System

#### Scrape Orchestrator
**File:** `backend/app/workers/scrape_orchestrator.py`

Runs as a standalone Docker container. Coordinates SellOff and RedTag scrapers in staggered sequence. Configurable via `SCRAPE_DELAY_SECONDS` env var. Schedule: 3 daily windows in Eastern Time (7-9am, 12-2pm, 6-8pm) with random offset within each window.

#### SellOff Scraper
**File:** `backend/app/workers/selloff_scraper.py`

Key scraper. Scrapes SellOff Vacations website for all-inclusive deals.

Key functions:
- `match_deal_to_signals()`: The canonical deal matching function. Must stay in sync with `_match_signal_against_deals()` in `signals.py`.
- `send_user_digest_email()`: Consolidated email per user (not per signal).
- `validate_user_for_email()`: Checks user exists, not opted out, has active plan (pro or active trial).
- `_build_price_delta_map()`: Uses SQL `LAG()` window function for price change detection.
- `AIRPORT_CITY_MAP`: Maps IATA codes to readable Canadian city names.

Safety: Uses residential proxy (DataImpulse), configurable via env vars (`PROXY_HOST`, `PROXY_PORT`, `PROXY_USER`, `PROXY_PASS`, `PROXY_COUNTRY`).

#### RedTag Scraper
**File:** `backend/app/workers/redtag_scraper.py`

Scrapes RedTag Vacations. Similar pattern to SellOff scraper.

#### Shared Matching
**File:** `backend/app/workers/shared/matching.py` -- Shared matching logic.
**File:** `backend/app/workers/shared/regions.py` -- `deal_matches_signal_region()` function for region matching.
**File:** `backend/app/workers/shared/upsert.py` -- Deal upsert logic (insert or update, handle dedup).

### 4.2 Email System

#### Email Orchestrator
**File:** `backend/app/services/email_orchestrator.py`

Central entry point for all email sends. Defines `EmailType` enum:
- `WELCOME`, `FIRST_SIGNAL`, `DEAL_ALERT`, `TRIAL_EXPIRING`, `TRIAL_EXPIRED`
- `PRO_ACTIVATED`, `PAYMENT_FAILED`, `SUBSCRIPTION_CANCELED`
- `NO_SIGNAL_REMINDER`, `ACCOUNT_DELETED`

Function `trigger(db, email_type, user_id, context)`:
1. Validates user for email (exists, not opted out, plan active)
2. Checks idempotency via `email_log` table
3. Renders template
4. Enqueues to `email_queue` (or sends directly for critical emails)
5. Logs to `email_log`

#### Email Queue
**File:** `backend/app/services/email_queue.py`

Rate-limited email delivery. Supports priorities (1=critical, 2=high, 3=low), retry logic with exponential backoff, dead-letter after max attempts.

#### Email Templates
**File:** `backend/app/services/email_templates/templates.py`

Python-defined email templates for each EmailType. Generates HTML emails with:
- Brand colors and styling
- Tracking pixel insertion
- Unsubscribe link (HMAC token-based)
- "Manage signals" and "Unsubscribe" footer links

**File:** `backend/app/services/email_templates/base.py` -- Base template wrapper with header, footer, styles.
**File:** `backend/app/services/email_templates/subject_preview.py` -- Subject line and preview text generation.

#### Email Service
**File:** `backend/app/services/email.py`

Low-level email sending via Resend API. Handles actual HTTP calls.

#### Notifications Log Worker
**File:** `backend/app/workers/notifications_log_worker.py`

Polls `notifications_outbox` table, sends pending notifications via Resend.

#### Lifecycle Email Worker
**File:** `backend/app/workers/lifecycle_email_worker.py`

Runs every 5 minutes (configurable via `LIFECYCLE_POLL_SECONDS`). Handles:
- Welcome emails for new users
- Trial expiring soon notifications
- Trial expired notifications
- No-signal reminder emails

### 4.3 Market Intelligence

**File:** `backend/app/services/market_intel.py`

Computes market statistics for deal scoring:
- `MarketBucket`: Defines a market segment (origin, destination, duration bucket, star bucket)
- `MarketStats`: Min, median, P25, P75, max, sample size
- `compute_market_stats()`: Queries active deals matching a bucket
- `score_deal()`: Scores a deal price against market stats, returns value label
- `build_spectrum_data()`: Data for price spectrum visualization
- `compute_empty_state_insights()`: Explains why a signal has no matches
- `compute_trigger_likelihood()`: Estimates when a signal is likely to get matches
- `compute_draft_signal_insights()`: Market data for the signal creation wizard

**File:** `backend/app/services/signal_intel.py`

Computes per-signal intelligence: trend direction, velocity, inflection, floor proximity, value score.

### 4.4 Book Window

**File:** `backend/app/services/book_window.py`

Heuristic-based "should I book now?" recommendation. Analyzes:
- Price trend direction and velocity
- Floor proximity
- Days until departure
- Market competition

Returns recommendation: `'book_now'`, `'wait'`, `'watch'`.

### 4.5 Account Service

**File:** `backend/app/services/account.py`

`delete_account()`: Two-phase soft delete:
1. Phase 1: Soft-delete user (set deleted_at, reason), cancel Stripe subscription
2. Send goodbye email (between phases)
3. Phase 2: Delete signals, clean up related data

`restore_account()`: Reverses soft delete (admin only).

### 4.6 User Mode

**File:** `backend/app/services/user_mode.py`

Email engagement tracking. Classifies users as `'active'`, `'cooling'`, `'dormant'` based on email opens/clicks.

---

## 5. Frontend Pages

### 5.1 Public Pages

#### Landing Page (`/`)
**File:** `tripsignal-ui/app/page.tsx`

Marketing landing page. Shows hero section, feature highlights, pricing preview, testimonials. Accessible to all users.

#### Pricing (`/pricing`)
**File:** `tripsignal-ui/app/pricing/page.tsx`

Pricing comparison page. Shows Free vs Pro tiers with feature lists.

#### Contact (`/contact`)
**File:** `tripsignal-ui/app/contact/page.tsx`

Contact form. Submits via `/contact-submit` route handler which sends email via Resend.

#### Legal Pages
**Files:** `tripsignal-ui/app/(legal)/privacy-policy/page.tsx`, `tripsignal-ui/app/(legal)/terms-and-conditions/page.tsx`

Static legal pages with print button component. Route group `(legal)` has its own layout.

#### Beta Gate (`/beta`)
**File:** `tripsignal-ui/app/beta/page.tsx`

Password gate page. Currently **disabled** in middleware (commented out). The beta-login route sets a cookie.

#### Deal Page (`/deal/[id]`)
**Files:** `tripsignal-ui/app/deal/[id]/page.tsx`, `tripsignal-ui/app/deal/[id]/DealPageClient.tsx`

Public deal detail page. No auth required. Fetches deal data from `/api/deals/{id}/public`. Shows hotel, dates, price, value score, market context.

#### Unsubscribe (`/unsubscribe`)
**File:** `tripsignal-ui/app/unsubscribe/page.tsx`

Token-based unsubscribe page. Reads `?token=xxx` from URL, calls backend `/api/unsubscribe` endpoints. Shows current preferences and allows opt-out or frequency change.

#### 404 Not Found
**File:** `tripsignal-ui/app/not-found.tsx`

Custom 404 page with radar animation and airplane blip. Themed messaging ("Lost signal"). CTAs to return home or view signals.

### 5.2 Auth Pages

#### Sign In (`/sign-in`)
**File:** `tripsignal-ui/app/sign-in/[[...sign-in]]/page.tsx`

Clerk `<SignIn>` component. Catch-all route for Clerk's auth flow.

#### Sign Up (`/sign-up`)
**File:** `tripsignal-ui/app/sign-up/[[...sign-up]]/page.tsx`

Clerk `<SignUp>` component. Catch-all route for Clerk's auth flow.

### 5.3 Onboarding Pages

#### Accept Terms (`/accept-terms`)
**File:** `tripsignal-ui/app/accept-terms/page.tsx`

Terms and privacy acceptance page. Redirected to by middleware if user hasn't accepted terms. Submits via `/accept-terms-submit` route handler.

#### Pro Activate (`/pro/activate`)
**File:** `tripsignal-ui/app/pro/activate/page.tsx`

Pro onboarding wizard. Shown after checkout.session.completed if `pro_activation_completed_at` is null. User configures notification preferences. Sets cookie on completion to prevent redirect loop.

### 5.4 App Pages (auth required)

#### Signals List (`/signals`)
**File:** `tripsignal-ui/app/signals/page.tsx`

Main app page. Lists all user's signals with match counts, market intel, deals panel. Uses components: `SignalListRow`, `DealsPanel`, `MarketHeader`, `MarketInsights`, `PlanCard`, `EmptySignalState`.

#### Create Signal (`/signals/new`)
**File:** `tripsignal-ui/app/signals/new/page.tsx`

Multi-step wizard for creating a signal. Steps:
1. Airport selection (AirportSelectionPanel)
2. Destination selection (DestinationSelectionPanel)
3. Travel window, duration, budget, star rating, notifications

Uses `WizardStepper` for step navigation, `WizardFooter` for actions.

#### Edit Signal (`/signals/[id]/edit`)
**File:** `tripsignal-ui/app/signals/[id]/edit/page.tsx`

Signal editing page. Loads existing signal config and presents edit form.

#### Scout (`/scout`)
**File:** `tripsignal-ui/app/scout/page.tsx`

Personal travel intelligence briefing. Calls `/api/scout/insights` for unified data. Fetched via `/scout-data` proxy route. Shows: verdict, action items, best deals, price context, book windows.

#### Feedback (`/feedback`)
**File:** `tripsignal-ui/app/feedback/page.tsx`

User feedback form.

### 5.5 Account Pages

#### Account Settings (`/account/settings`)
**File:** `tripsignal-ui/app/account/settings/page.tsx`

User settings: email preferences, plan management, account deletion.

#### Cancel Account (`/account/cancel`)
**File:** `tripsignal-ui/app/account/cancel/page.tsx`

Account deletion flow with reason selection.

#### Notification Settings (`/account/notifications`)
**File:** `tripsignal-ui/app/account/notifications/page.tsx`

Notification preference management (delivery frequency, quiet hours, weekly summary).

### 5.6 Admin Pages

#### Admin Dashboard (`/admin`)
**Files:** `tripsignal-ui/app/admin/page.tsx`, `tripsignal-ui/app/admin/layout.tsx`

Admin-only dashboard. Auth via `ADMIN_CLERK_USER_ID` env var check. Tabs:
- **Health** (HealthTab): System health, scrape run stats, DB stats
- **Users** (UsersTab): Full user list with actions (toggle test, set plan, delete, extend trial)
- **Signals** (SignalsTab): All signals across users
- **Deals** (DealsTab): Deal browsing with filters
- **Collection** (CollectionTab): Scrape run history and stats
- **Hotels** (HotelsTab): Hotel link management (TripAdvisor URLs)
- **Notifications** (NotificationsTab): Notification outbox browsing
- **Email Testing** (EmailTestingTab): Send test emails, preview templates
- **Email Queue** (EmailQueueTab): Queue management (stats, retry, pause, drain)
- **Scrape Data** (ScrapeDataTab): Detailed scrape run data
- **Accessibility** (AccessibilityTab): Accessibility audit

Additional admin components:
- `NextScrapeIndicator`: Shows countdown to next scrape
- `TipTapEditor`: Rich text editor for email template editing
- `StatCard`: Metric card component
- `Badge`: Status badge
- `DeleteModal`, `FeedbackModal`: Confirmation dialogs
- `UserActionMenu`: Dropdown actions per user

---

## 6. Frontend Components

### Shared Components

| Component | File | Purpose |
|-----------|------|---------|
| AppHeader | `components/AppHeader.tsx` | Top navigation. Shows logo, nav links, auth state (SignedIn/SignedOut). Mobile hamburger menu. Links differ based on auth state. |
| AppFooter | `components/AppFooter.tsx` | Footer with links to legal pages, contact, social. |
| BottomNav | `components/BottomNav.tsx` | Mobile bottom navigation bar (signals, scout, settings). Only shown when authenticated. |
| TopNav | `components/top-nav.tsx` | Alternative top navigation component. |
| SearchInput | `components/search-input.tsx` | Searchable input with combobox (cmdk). Used for airport/destination search. |
| WizardFooter | `components/signal/WizardFooter.tsx` | Footer for signal creation wizard (back/next/create buttons). |
| TripadvisorIcon | `components/icons/TripadvisorIcon.tsx` | TripAdvisor SVG icon component. |

### Signal Page Components

| Component | File | Purpose |
|-----------|------|---------|
| SignalListRow | `signals/components/SignalListRow.tsx` | Individual signal row in the signals list. Shows name, status, match count, intel badges. Expandable to show deals. |
| DealsPanel | `signals/components/DealsPanel.tsx` | Expanded panel showing matched deals for a signal. Shows deal cards with price, hotel, dates, price trend, favourite toggle. |
| MarketHeader | `signals/components/MarketHeader.tsx` | Market overview header on signals page (total packages, price drops). |
| MarketInsights | `signals/components/MarketInsights.tsx` | Market intelligence display per signal (value score, trend, spectrum). |
| PlanCard | `signals/components/PlanCard.tsx` | Free vs Pro plan comparison card. Shown when user hits signal limit. |
| EmptySignalState | `signals/components/EmptySignalState.tsx` | Empty state when user has no signals. |
| CapacityModal | `signals/components/CapacityModal.tsx` | Modal shown when signal limit is reached. |
| SignalIcon | `signals/components/SignalIcon.tsx` | Icon component for signal status. |
| SignalStatusBadge | `signals/components/SignalStatusBadge.tsx` | Status badge (active, paused, etc.) |
| signal-types.ts | `signals/components/signal-types.ts` | TypeScript type definitions for signal data structures. |

### Signal Wizard Components

| Component | File | Purpose |
|-----------|------|---------|
| AirportSelectionPanel | `signals/new/components/AirportSelectionPanel.tsx` | Departure airport selection with search. Shows Canadian airports. |
| DestinationSelectionPanel | `signals/new/components/DestinationSelectionPanel.tsx` | Destination region selection. Grouped by country/region. |
| WizardStepper | `signals/new/components/WizardStepper.tsx` | Step indicator for signal creation wizard. |
| useRecents | `signals/new/hooks/useRecents.ts` | Hook for tracking recently selected airports/destinations (localStorage). |

### shadcn/ui Components

Installed in `components/ui/`: accordion, alert-dialog, alert, badge, button, calendar, card, checkbox, command, dialog, dropdown-menu, input, label, popover, radio-group, select, separator, sheet, skeleton, sonner, switch, table, textarea.

---

## 7. Next.js Proxy Routes

These route handlers in the frontend proxy requests to the backend API, adding Clerk auth headers.

### User Routes

| Frontend Path | Method | Backend Endpoint | Purpose |
|---------------|--------|-----------------|---------|
| `/user/me` | GET | `/users/by-clerk-id/{clerkId}` | Get current user profile |
| `/user/sync` | POST | `/users/sync` | Sync user on sign-in (forwards IP, UA, timezone) |
| `/user/delete` | DELETE | `/users/me` | Delete user account |
| `/user/cancel-subscription` | POST | `/users/cancel-subscription` | Cancel subscription |
| `/user/prefs` | GET/PUT | `/users/prefs` | Get/update notification preferences |

### Signal Routes

| Frontend Path | Method | Backend Endpoint | Purpose |
|---------------|--------|-----------------|---------|
| `/api/signals` | GET/POST | `/api/signals` | List/create signals |
| `/api/signals/[id]` | PATCH/DELETE | `/api/signals/{id}` | Update/delete signal |

### Billing Routes

| Frontend Path | Method | Backend Endpoint | Purpose |
|---------------|--------|-----------------|---------|
| `/api/billing/checkout` | POST | `/api/billing/checkout` | Create Stripe checkout session |
| `/api/billing/portal` | POST | `/api/billing/portal` | Create Stripe portal session |
| `/api/billing/webhook` | POST | `/api/billing/webhook` | Stripe webhook (no auth added, uses Stripe signature) |
| `/billing/checkout` | POST | `/api/billing/checkout` | Alternate billing checkout path |
| `/billing/portal` | POST | `/api/billing/portal` | Alternate billing portal path |

### Admin Routes

| Frontend Path | Method | Backend Endpoint | Purpose |
|---------------|--------|-----------------|---------|
| `/api/admin` | GET | `/admin/health` | Admin health check |
| `/api/admin/users/[id]/toggle-test` | PATCH | `/admin/users/{id}/toggle-test` | Toggle test user |
| `/api/admin-toggle` | PATCH | Various admin endpoints | Admin toggle actions |
| `/admin-proxy` | Various | Various admin endpoints | General admin proxy |
| `/admin-action` | POST | Various admin actions | Admin action proxy |
| `/admin-audit` | GET | Admin audit endpoints | Admin audit data |
| `/admin-plan` | PATCH | `/admin/users/{id}/set-plan` | Change user plan |
| `/admin-status` | GET | `/admin/health` | Admin status check |

### Other Routes

| Frontend Path | Method | Backend/Service | Purpose |
|---------------|--------|-----------------|---------|
| `/contact-submit` | POST | Resend API | Send contact form email directly via Resend |
| `/beta-login` | POST | N/A | Set beta access cookie |
| `/scout-data` | GET | `/api/scout/insights` | Proxy for Scout page data |
| `/accept-terms-submit` | POST | `/users/accept-terms` | Accept terms and privacy policy |
| `/api/user/me` | GET | `/users/by-clerk-id/{clerkId}` | Duplicate of `/user/me` |

### SEO Routes

| File | Purpose |
|------|---------|
| `app/robots.ts` | Generates robots.txt |
| `app/sitemap.ts` | Generates sitemap.xml |

---

## 8. Data & Utilities

### `lib/data/airports.ts`
Canadian airport data with IATA codes, city names, provinces. 34 departure airports across all provinces (BC, AB, SK, MB, ON, QC, Atlantic). Exports `FROM_AIRPORTS`, `TO_AIRPORTS`, `DESTINATION_REGIONS`, and `SPECIFIC_REGIONS`. Used by the signal creation wizard for airport selection.

### `lib/utils.ts`
Utility functions, primarily `cn()` (className merger using `clsx` + `tailwind-merge`).

### `lib/utils/format-signal-name.ts`
Formats a signal name from its config (airports + destinations).

### `lib/analytics.ts`
Google Analytics 4 event tracking (GA ID: `G-2DNWZ6VJ6X`). Exports `trackEvent(name, params?)` and `setUserId(userId)`. No-ops gracefully if gtag is blocked by ad blockers.

### `lib/admin/auth.ts`
Admin authentication helper. Exports `db_admin(clerkUserId, token)` which calls `GET /api/users/by-clerk-id/{clerkUserId}` and returns boolean indicating admin role.

---

## 9. Infrastructure

### 9.1 Containers

| Container | Image | Purpose | Port |
|-----------|-------|---------|------|
| tripsignal-api | Custom (Python 3.12) | FastAPI backend | 8000 (internal) |
| tripsignal-frontend | Custom (Node 20 Alpine) | Next.js frontend | 3000 (internal) |
| tripsignal-postgres | postgres:16-alpine | Database | 5432 (internal) |
| tripsignal-caddy | caddy:2 | Reverse proxy + TLS | 80, 443 (external) |
| tripsignal-scrape-orchestrator | Custom (same as api) | Scraper coordinator | None |
| notifications_worker | Custom (same as api) | Email notification sender | None |
| lifecycle_worker | Custom (same as api) | Lifecycle email scheduler | None |

All containers on `tripsignal-network` (bridge driver).

### 9.2 Caddy Routing

**File:** `deploy/Caddyfile`

```
tripsignal.ca:
  - /api/* -> api:8000 (max body 10MB)
  - /* -> tripsignal-frontend:3000

www.tripsignal.ca:
  - 301 redirect to tripsignal.ca

staging.tripsignal.ca:
  - Same as production but frontend -> tripsignal-frontend-staging:3000
```

Security headers: HSTS, X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy: no-referrer.

### 9.3 Docker Configuration

**Backend Dockerfile** (`Dockerfile`):
- Python 3.12-slim base
- Installs gcc and postgresql-client
- Copies `requirements.txt` and `backend/` directory
- Runs uvicorn with `--app-dir /app/backend`
- Health check: HTTP GET `/health`

**Frontend Dockerfile** (`Dockerfile.prod`):
- Multi-stage build (deps -> builder -> runner)
- Node 20 Alpine base
- `output: 'standalone'` in next.config
- Runs as non-root `nextjs` user
- `node server.js` (Next.js standalone server)

### 9.4 Environment Variables

**Backend (.env)**:

| Variable | Purpose |
|----------|---------|
| POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB | Database credentials |
| POSTGRES_HOST, POSTGRES_PORT | Database connection (set by docker-compose) |
| CLERK_SECRET_KEY | Clerk backend secret |
| CLERK_JWKS_URL | Clerk JWKS endpoint for JWT verification |
| CLERK_WEBHOOK_SECRET | Svix secret for Clerk webhooks |
| STRIPE_SECRET_KEY | Stripe API key |
| STRIPE_WEBHOOK_SECRET | Stripe webhook signing secret |
| STRIPE_PRO_PRICE_ID | Stripe price ID for Pro plan |
| ADMIN_TOKEN | HMAC token for admin endpoints |
| UNSUB_SECRET | HMAC secret for unsubscribe tokens |
| RESEND_WEBHOOK_SECRET | Svix secret for Resend webhooks |
| ENABLE_EMAIL_NOTIFICATIONS | Enable email sending (true/false) |
| EMAIL_DRY_RUN | Log emails instead of sending |
| EMAIL_SUSPEND_NONCRITICAL | Pause non-critical emails |
| DEBUG, ENV | Debug mode and environment |
| PROXY_USER, PROXY_PASS | Residential proxy credentials |
| PROXY_HOST, PROXY_PORT, PROXY_COUNTRY | Proxy configuration |
| SCRAPE_DELAY_SECONDS | Delay between scraper runs |
| LIFECYCLE_POLL_SECONDS | Lifecycle worker poll interval |

**Frontend (.env.production)**:

| Variable | Purpose |
|----------|---------|
| NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY | Clerk frontend key |
| CLERK_SECRET_KEY | Clerk backend secret (for server components) |
| API_URL | Backend API URL (default `http://api:8000`) |
| ADMIN_CLERK_USER_ID | Clerk user ID for admin access |
| ADMIN_TOKEN | Admin API token |
| RESEND_API_KEY | Resend API key (for contact form) |
| BETA_PASSWORD | Beta gate password (currently disabled) |

### 9.5 Deployment

**Backend deployment:**
```bash
cd /opt/tripsignal
docker compose build api scrape_orchestrator notifications_worker lifecycle_worker
docker compose up -d api scrape_orchestrator notifications_worker lifecycle_worker
```
All four services share the same Dockerfile. Must rebuild ALL when code changes.

**Frontend deployment:**
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
Frontend is NOT in docker-compose. Managed as standalone container.

**Database migrations:**
```bash
docker exec tripsignal-api alembic upgrade head
```

---

## 10. Auth Flow

### End-to-End Authentication

1. **Browser**: User signs in via Clerk's `<SignIn>` component
2. **Clerk**: Issues JWT with `sub` claim = Clerk user ID
3. **Next.js middleware**: On protected routes, calls `auth.protect()`. Gets `userId` and `getToken()`
4. **Middleware checks**:
   - Terms acceptance: Calls `GET /users/terms-status?clerk_id=xxx` (server-to-server). Redirects to `/accept-terms` if not accepted.
   - Pro activation: Calls `GET /users/by-clerk-id/{clerkId}` to check if pro user needs onboarding. Redirects to `/pro/activate`.
5. **Frontend proxy routes**: When client calls `/user/me`, the route handler:
   - Gets Clerk auth via `await auth()`
   - Gets JWT token via `await getToken()`
   - Forwards `Authorization: Bearer <token>` to backend
6. **FastAPI `get_clerk_user_id` dependency**:
   - Priority 1: Verify JWT via JWKS, extract `sub` claim
   - Priority 2 (legacy fallback): `x-clerk-user-id` header
   - Priority 3 (legacy fallback): `x-user-id` header
7. **FastAPI routes**: Look up `User` by `clerk_id`, verify ownership, return data

### Beta Password Gate (currently disabled)

When enabled: middleware checks `beta_access` cookie against `BETA_PASSWORD` env var. Unauthenticated users without the cookie are redirected to `/beta`.

### Admin Auth

Frontend: `lib/admin/auth.ts` checks `ADMIN_CLERK_USER_ID` env var.
Backend: `X-Admin-Token` header verified via HMAC comparison against `ADMIN_TOKEN` env var.

---

## 11. Billing Flow

### Upgrade to Pro

1. User clicks "Upgrade" button on the app
2. Frontend calls `POST /api/billing/checkout` (proxied)
3. Backend creates Stripe customer (if needed), creates Checkout Session
4. Returns `{url: "https://checkout.stripe.com/..."}`
5. Frontend redirects user to Stripe Checkout
6. User completes payment
7. Stripe sends `checkout.session.completed` webhook to `POST /api/billing/webhook`
8. Backend:
   - Stores event in `stripe_events` (dedup)
   - Sets `plan_type = 'pro'`, `plan_status = 'active'`
   - Reactivates any payment-paused signals
   - Triggers `PRO_ACTIVATED` email via orchestrator
9. Stripe redirects user to `https://tripsignal.ca/signals?upgraded=true`
10. Middleware detects pro user without `pro_activation_completed_at` -> redirects to `/pro/activate`
11. User completes pro onboarding wizard, sets notification preferences

### Payment Failure

1. Stripe sends `invoice.payment_failed` webhook
2. Backend pauses all active signals (status = `'payment_paused'`)
3. Triggers `PAYMENT_FAILED` email

### Recovery

1. Stripe sends `customer.subscription.updated` with status = `'active'`
2. Backend reactivates payment-paused signals
3. User-initiated paused signals remain paused

### Subscription Cancellation

1. User cancels via Stripe portal (accessed via `POST /api/billing/portal`)
2. Stripe sends `customer.subscription.deleted` webhook
3. Backend: Sets `plan_type = 'free'`, triggers `SUBSCRIPTION_CANCELED` email

### Plan Types and Limits

| Feature | Free | Pro |
|---------|------|-----|
| Signal limit | 1 | 10 |
| Deal matching | Yes | Yes |
| Email alerts | Yes (trial) | Yes |
| Weekly summary | No | Yes |
| Market intelligence | Basic | Full |

---

## 12. Scraper -> Alert Pipeline

### Full Pipeline

```
1. scrape_orchestrator starts (docker container, runs continuously)
   |
2. Runs SellOff scraper, waits SCRAPE_DELAY_SECONDS, runs RedTag scraper
   |
3. Each scraper:
   a. Calls POST /api/system/scrape-started (creates ScrapeRun)
   b. Fetches deals from provider website (via residential proxy)
   c. Upserts deals into deals table (via shared/upsert.py)
   d. Records price history in deal_price_history
   e. Marks deals not seen in this cycle as inactive (missed_cycles)
   f. Matches deals to active signals (via shared/matching.py)
   g. Creates DealMatch rows for new matches
   h. Computes signal intel (updates signal_intel_cache)
   i. Computes route intel (updates route_intel_cache)
   j. Sends digest emails per user (via match_alert.py / email_orchestrator)
   k. Calls POST /api/system/collection-complete (updates ScrapeRun)
   l. Calls POST /api/system/next-scan (stores next scan time)
   |
4. notifications_log_worker polls notifications_outbox
   - Sends pending notifications via Resend
   |
5. lifecycle_email_worker runs every 5 minutes
   - Sends welcome, trial-expiring, trial-expired, no-signal emails
```

### Email Deduplication

- `email_log.idempotency_key` prevents duplicate sends
- User `*_email_sent_at` columns guard lifecycle emails
- `stripe_events.stripe_event_id` deduplicates webhook processing

### Deal Staleness

Deals have `missed_cycles` counter. After N consecutive missed cycles, deal is deactivated (`is_active = false`, `deactivated_at` set). This ensures expired deals don't persist.

---

## 13. Known Gotchas & Tribal Knowledge

1. **Caddy `/api/*` interception**: All paths starting with `/api/` go to FastAPI, NOT Next.js. This means Next.js API routes under `/app/api/` are only accessible via internal Docker networking, not from the browser. Frontend proxy routes that need browser access use paths like `/user/sync`, `/billing/checkout`, NOT `/api/*`.

2. **Two matching functions must stay in sync**: `selloff_scraper.py` -> `match_deal_to_signals()` and `signals.py` -> `_match_signal_against_deals()`. Both implement the same matching logic. If one changes, the other must too.

3. **Signal caps**: Free = 1 signal, Pro = 10 signals. Enforced in `create_signal()`.

4. **Date semantics**: `start_date` = earliest departure date, `end_date` = latest return date (not latest departure). Both `depart_date >= start_date` AND `return_date <= end_date` must be true.

5. **Signal status vs Deal is_active**: Signals use `status == "active"` (string field). Deals use `is_active == True` (boolean field). Don't confuse them.

6. **SQLAlchemy `None` vs `null()`**: When inserting rows with `server_default`, Python `None` lets the default kick in. Use `from sqlalchemy import null` for explicit NULL.

7. **Always `db.rollback()` in exception handlers**: After a failed `db.commit()`, the session stays dirty.

8. **Rebuild ALL backend containers**: API, scraper, notifications worker, lifecycle worker all share the same Docker image. Change one file -> rebuild all four.

9. **Frontend env vars in Docker**: The frontend container MUST be started with `--env-file`. Without it, Clerk auth fails and the site shows internal errors.

10. **Public routes in middleware**: Must be added to BOTH `isPublicRoute` (Clerk matcher) and `isBetaExempt`. Missing from either causes auth issues.

11. **`useSearchParams()` needs Suspense**: Without a `<Suspense>` boundary, Next.js static build fails.

12. **AIRPORT_CITY_MAP**: Defined in `selloff_scraper.py`. Maps IATA codes to human-readable Canadian city names for emails. Must be updated when adding new airports.

13. **`validate_user_for_email()`**: Three checks must pass: user exists, not opted out (`email_opt_out = false`), and has active plan (pro or active trial). All three required before sending.

14. **Frontend container not in docker-compose**: The Next.js frontend runs as a standalone Docker container, not managed by docker-compose. Requires manual stop/rm/run cycle for updates.

15. **Price is always in cents CAD**: `price_cents` everywhere. Divide by 100 for display. Currency is always CAD.

16. **Soft delete pattern**: Users are soft-deleted (deleted_at set), not removed. Signals cascade-delete. Admin can restore via `undelete` endpoint or permanently remove via `hard-delete`.

17. **Staging environment**: `staging.tripsignal.ca` exists in Caddyfile, routes to `tripsignal-frontend-staging:3000`.

18. **Proxy configuration**: Scraper uses DataImpulse residential proxy (`gw.dataimpulse.com:823`) with Canadian geo (`cr.ca`).

19. **Pydantic schema stripping**: Pydantic v2 strips undeclared fields from request bodies. If a new field is added to the frontend but not the Pydantic schema, it silently disappears.

20. **Email templates are code + DB overrides**: Default templates live in Python code (`email_templates/templates.py`). Admins can override subject/body via the admin panel, stored in `email_template_overrides` table. NULL override = use Python default.

21. **`base.py` missing model imports**: `backend/app/db/base.py` doesn't import all models (missing User, EmailLog, EmailTemplateOverride, StripeEvent, SystemConfig, ScrapeRun, HotelLink, DealPriceHistory). These models work because they're imported elsewhere before Alembic runs, but this could cause Alembic autogenerate to miss migrations if import order changes.
