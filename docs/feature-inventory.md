# TripSignal Feature Inventory

> **Generated**: 2026-03-04 | **Updated**: 2026-03-05
> **Format version**: 2.0
> **Source**: Codebase analysis — all references point to concrete files, routes, and line numbers.

---

## 1. Summary

| Area | Count | Active | Deprecated | Not confirmed |
|------|-------|--------|------------|---------------|
| User (F-U-xxx) | 29 | 29 | 0 | 0 |
| Admin (F-A-xxx) | 25 | 25 | 0 | 0 |
| System (F-S-xxx) | 20 | 20 | 0 | 0 |
| **Total** | **74** | **74** | **0** | **0** |

---

## 2. User Features

### F-U-001: Clerk Authentication (Sign-In / Sign-Up)
- **Status**: Active
- **What it does**: Handles user authentication via Clerk. Webhook syncs `user.created` / `user.updated` events to local DB, extracting primary email. User row may be created by webhook or by `/users/sync`.
- **Entry points**:
  - UI routes/pages: `/sign-in` (`tripsignal-ui/app/sign-in/[[...sign-in]]/page.tsx`), `/sign-up` (`tripsignal-ui/app/sign-up/[[...sign-up]]/page.tsx`)
  - API endpoints: POST `/api/clerk/webhook` (`backend/app/api/routes/clerk_webhook.py:22-89`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` (`backend/app/db/models/user.py`)
- **Access control**:
  - Auth: Clerk (`@clerk/nextjs`), Svix webhook verification
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Clerk handles all auth flows. Webhook verifies Svix signature, extracts email from event payload, upserts user row.
- **References**: `tripsignal-ui/middleware.ts:8-22`, `backend/app/api/routes/clerk_webhook.py`

### F-U-002: Beta Password Protection
- **Status**: Active
- **What it does**: Cookie-based gate requiring password `LetsFly!`. All non-exempt routes redirect to `/beta` if cookie missing/invalid.
- **Entry points**:
  - UI routes/pages: `/beta` (`tripsignal-ui/app/beta/page.tsx`), `/api/beta-login` (`tripsignal-ui/app/api/beta-login/route.ts`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: — (cookie-only)
- **Access control**:
  - Auth: Cookie `BETA_COOKIE`
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Middleware checks `BETA_COOKIE` cookie → if missing/invalid, redirect to `/beta`. Exempt routes listed in `isBetaExempt()`.
- **References**: `tripsignal-ui/middleware.ts:5-6,38-62`

### F-U-003: Terms & Privacy Acceptance
- **Status**: Active
- **What it does**: Requires users to accept terms and privacy policy. On first acceptance, starts a 14-day free trial and triggers welcome email.
- **Entry points**:
  - UI routes/pages: `/accept-terms` (`tripsignal-ui/app/accept-terms/page.tsx`), `/api/accept-terms-submit` (`tripsignal-ui/app/api/accept-terms-submit/route.ts`)
  - API endpoints: GET `/users/terms-status` (`backend/app/api/routes/users.py:104-153`), POST `/users/accept-terms` (`backend/app/api/routes/users.py:104-153`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `terms_accepted_at`, `terms_version`, `privacy_accepted_at`, `privacy_version` (`backend/app/db/models/user.py:33-37`)
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: `WELCOME`
  - SMS templates: —
  - Trigger logic: Triggered on first terms acceptance via orchestrator (`users.py:143-151`)
- **Key flows**: Middleware redirects unagreed users to `/accept-terms` → user accepts → backend sets timestamps and trial start → triggers welcome email.
- **References**: `tripsignal-ui/middleware.ts:73-86`, `backend/app/api/routes/users.py:104-153`

### F-U-004: User Sync on Sign-In
- **Status**: Active
- **What it does**: Called on every sign-in. Tracks login metadata (IP, user agent, count). Auto-sets timezone from browser if user hasn't manually set one. Creates user row if missing.
- **Entry points**:
  - UI routes/pages: — (called automatically)
  - API endpoints: POST `/users/sync` (`backend/app/api/routes/users.py:60-99`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `last_login_at`, `login_count`, `last_login_ip`, `last_login_user_agent` (`backend/app/db/models/user.py:57-64`)
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Frontend proxy `/api/user/sync` → backend creates/updates user row with login metadata.
- **References**: `tripsignal-ui/app/api/user/sync/route.ts`, `backend/app/api/routes/users.py:60-99`

### F-U-005: Pro Activation Flow
- **Status**: Active
- **What it does**: After Stripe checkout completes, redirects pro users to onboarding flow. Cookie `activation_complete` bypasses redirect for certain routes.
- **Entry points**:
  - UI routes/pages: `/pro/activate` (`tripsignal-ui/app/pro/activate/page.tsx`)
  - API endpoints: PUT `/users/prefs` with `complete_activation: true` (`backend/app/api/routes/users.py:193-224`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `pro_activation_completed_at` (`backend/app/db/models/user.py:54-56`)
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: Pro only
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Middleware detects `plan_type=pro` + no `pro_activation_completed_at` → redirect to `/pro/activate` → user completes onboarding → sets timestamp.
- **References**: `tripsignal-ui/middleware.ts:89-109`, `backend/app/api/routes/users.py:193-224`

### F-U-006: Create Signal
- **Status**: Active
- **What it does**: Creates a travel deal signal with departure airports, destination regions, travel window, budget, star rating, and notification prefs. Immediately runs background matching against existing deals. Triggers FIRST_SIGNAL email on first signal creation.
- **Entry points**:
  - UI routes/pages: `/signals/new` (`tripsignal-ui/app/signals/new/page.tsx`)
  - API endpoints: POST `/api/signals` (`backend/app/api/signals.py:155-212`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signals` (`backend/app/db/models/signal.py`), `deal_matches`
- **Access control**:
  - Auth: Clerk JWT (`Authorization: Bearer`, verified via JWKS RS256 in `deps.py:get_clerk_user_id()`)
  - Role: —
  - Plan gate: Free: 1 signal; Pro: 10 signals (frontend-enforced only)
- **Notifications**:
  - Email templates: `FIRST_SIGNAL`
  - SMS templates: —
  - Trigger logic: On first signal creation (`signals.py:196-209`)
- **Key flows**: User fills form → POST signal → store config as JSONB + mirrored columns → `_match_signal_against_deals()` runs immediately → if first signal, trigger FIRST_SIGNAL email.
- **References**: `backend/app/api/signals.py:155-212`, `tripsignal-ui/app/api/signals/route.ts`

### F-U-007: List Signals
- **Status**: Active
- **What it does**: Returns user's signals with match counts, ordered by `created_at DESC`.
- **Entry points**:
  - UI routes/pages: `/signals` (`tripsignal-ui/app/signals/page.tsx`)
  - API endpoints: GET `/api/signals` (`backend/app/api/signals.py:215-248`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signals`, `deal_matches` (outer join for counts)
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query signals with match counts via outer join → return list.
- **References**: `backend/app/api/signals.py:215-248`

### F-U-008: Edit Signal
- **Status**: Active
- **What it does**: Partial update of signal config using deep-merge. Re-matches deals when search criteria change.
- **Entry points**:
  - UI routes/pages: `/signals/[id]/edit` (`tripsignal-ui/app/signals/[id]/edit/page.tsx`)
  - API endpoints: PATCH `/api/signals/{signal_id}` (`backend/app/api/signals.py:288-348`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signals`, `deal_matches`
- **Access control**:
  - Auth: Clerk (ownership check via `x_user_id`)
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → deep-merge config → update mirrored columns → if search criteria changed, delete existing matches and re-run `_match_signal_against_deals()`.
- **References**: `backend/app/api/signals.py:288-348`

### F-U-009: Delete Signal
- **Status**: Active
- **What it does**: Hard deletes a signal. Validates ownership via `x_user_id` header.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: DELETE `/api/signals/{signal_id}` (`backend/app/api/signals.py:266-285`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signals`
- **Access control**:
  - Auth: Clerk (ownership check)
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: DELETE request → verify ownership → hard delete signal row.
- **References**: `backend/app/api/signals.py:266-285`

### F-U-010: Get Signal Details
- **Status**: Active
- **What it does**: Returns full signal config. No ownership check (by design — used internally).
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/api/signals/{signal_id}` (`backend/app/api/signals.py:251-263`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signals`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → return signal config.
- **References**: `backend/app/api/signals.py:251-263`

### F-U-011: View Signal Matches
- **Status**: Active
- **What it does**: Returns active deals matched to a signal, favourites first. Includes price trend data and TripAdvisor URLs.
- **Entry points**:
  - UI routes/pages: Signal detail page
  - API endpoints: GET `/api/signals/{signal_id}/matches` (`backend/app/api/routes/deal_matches.py:47-110`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `deal_matches`, `deals`, `hotel_links`, `deal_price_history`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query matches joined with deals → compute price trend via `get_price_trend()` → attach TripAdvisor URLs → return sorted (favourites first).
- **References**: `backend/app/api/routes/deal_matches.py:47-110`

### F-U-012: Toggle Favourite Match
- **Status**: Active
- **What it does**: Toggles `is_favourite` boolean on a deal match.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PATCH `/api/signals/{signal_id}/matches/{match_id}/favourite` (`backend/app/api/routes/deal_matches.py:113-169`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `deal_matches`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → toggle `is_favourite` → return updated match with deal and price trend.
- **References**: `backend/app/api/routes/deal_matches.py:113-169`

### F-U-013: Manual Match Creation
- **Status**: Active
- **What it does**: Creates a match between a signal and a deal (idempotent). Creates a `SignalRun` with `run_type="manual"`. On new match, creates a notification outbox entry.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/api/signals/{signal_id}/matches` (`backend/app/api/routes/deal_matches.py:172-274`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `deal_matches`, `signal_runs`, `notifications_outbox`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: Creates notification outbox entry on new match
- **Key flows**: POST request → check for existing match → create `SignalRun` → insert match → create notification outbox entry.
- **References**: `backend/app/api/routes/deal_matches.py:172-274`

### F-U-014: Stripe Checkout (Upgrade to Pro)
- **Status**: Active
- **What it does**: Creates Stripe checkout session for Pro subscription. Creates Stripe customer if not exists.
- **Entry points**:
  - UI routes/pages: `/pricing` (`tripsignal-ui/app/pricing/page.tsx`)
  - API endpoints: POST `/api/billing/checkout` (`backend/app/api/routes/billing.py:36-56`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `stripe_customer_id`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: N/A (initiates upgrade)
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST request → create/retrieve Stripe customer → create checkout session with `STRIPE_PRO_PRICE_ID` → return checkout URL. Success URL: `/signals?upgraded=true`.
- **References**: `backend/app/api/routes/billing.py:36-56`, `tripsignal-ui/app/api/billing/checkout/route.ts`

### F-U-015: Stripe Billing Portal
- **Status**: Active
- **What it does**: Opens Stripe's hosted billing portal for subscription management.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/api/billing/portal` (`backend/app/api/routes/billing.py:59-68`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `stripe_customer_id`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: Pro only (requires `stripe_customer_id`)
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST request → create portal session → return portal URL. Return URL: `/signals`.
- **References**: `backend/app/api/routes/billing.py:59-68`

### F-U-016: Stripe Webhook Processing
- **Status**: Active
- **What it does**: Processes Stripe webhook events. Event deduplication via `StripeEvent` table. Handles checkout completion, subscription changes, and payment failures.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/api/billing/webhook` (`backend/app/api/routes/billing.py:71-124`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `stripe_events` (`backend/app/db/models/stripe_event.py`), `users`, `signals`
- **Access control**:
  - Auth: Stripe signature verification
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: `PRO_ACTIVATED`, `SUBSCRIPTION_CANCELED`, `PAYMENT_FAILED`
  - SMS templates: —
  - Trigger logic: On checkout complete → PRO_ACTIVATED. On subscription deleted → SUBSCRIPTION_CANCELED. On payment failed → PAYMENT_FAILED + pause all active signals.
- **Key flows**: Verify Stripe signature → dedup via `StripeEvent` upsert → handle event type → update user plan/status → trigger appropriate email → reactivate/pause signals as needed.
- **References**: `backend/app/api/routes/billing.py:71-124`, `backend/app/api/routes/billing.py:255-285`

### F-U-017: Cancel Subscription
- **Status**: Active
- **What it does**: Marks `plan_status=cancelled` locally. Actual Stripe cancellation handled by webhook.
- **Entry points**:
  - UI routes/pages: `/account/cancel` (`tripsignal-ui/app/account/cancel/page.tsx`)
  - API endpoints: POST `/users/cancel-subscription` (`backend/app/api/routes/users.py:263-272`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `plan_status`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: Pro only
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST request → set `plan_status=cancelled` → Stripe webhook handles actual cancellation.
- **References**: `backend/app/api/routes/users.py:263-272`

### F-U-018: View Preferences
- **Status**: Active
- **What it does**: Returns user preferences: plan type/status, notification settings, timezone, email opt-out status.
- **Entry points**:
  - UI routes/pages: `/account/notifications` (`tripsignal-ui/app/account/notifications/page.tsx`)
  - API endpoints: GET `/users/prefs` (`backend/app/api/routes/users.py:158-177`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → return user prefs object.
- **References**: `backend/app/api/routes/users.py:158-177`, `tripsignal-ui/app/api/user/prefs/route.ts`

### F-U-019: Update Preferences
- **Status**: Active
- **What it does**: Updates notification delivery frequency, email/SMS enabled, timezone, and pro activation completion.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PUT `/users/prefs` (`backend/app/api/routes/users.py:193-224`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PUT request → validate frequency values (`all`, `morning`, `noon`, `evening`, comma-separated, `all` cannot be combined) → update user fields.
- **References**: `backend/app/api/routes/users.py:193-224`

### F-U-020: Account Settings
- **Status**: Active
- **What it does**: Account settings page (frontend only).
- **Entry points**:
  - UI routes/pages: `/account/settings` (`tripsignal-ui/app/account/settings/page.tsx`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Renders account settings UI.
- **References**: `tripsignal-ui/app/account/settings/page.tsx`

### F-U-021: Delete Account
- **Status**: Active
- **What it does**: PIPEDA-compliant 2-phase delete. Phase 1: cancel Stripe, mark deleted, send confirmation email. Phase 2: scrub PII, deactivate signals, scrub related tables. Best-effort Clerk user deletion.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: DELETE `/users/me` (`backend/app/api/routes/users.py:234-258`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`, `signals`, `email_log`, `notifications_outbox`
- **Access control**:
  - Auth: Clerk
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: `ACCOUNT_DELETED_FREE`, `ACCOUNT_DELETED_PRO`
  - SMS templates: —
  - Trigger logic: Sends deletion confirmation email between Phase 1 and Phase 2
- **Key flows**: Cancel Stripe → mark `deleted_at` → send confirmation email → scrub PII (email → sentinel, clerk_id → `deleted:{id}`, null IP/UA/stripe) → deactivate signals → scrub `to_email` in logs → delete Clerk user.
- **References**: `backend/app/services/account.py:40-199`, `backend/app/api/routes/users.py:234-258`

### F-U-022: Token-Based Unsubscribe
- **Status**: Active
- **What it does**: HMAC token-based email unsubscribe (no auth required, CASL/CAN-SPAM compliant). Supports opt-out, resubscribe, change frequency, update prefs.
- **Entry points**:
  - UI routes/pages: `/unsubscribe` (`tripsignal-ui/app/unsubscribe/page.tsx`)
  - API endpoints: GET `/api/unsubscribe` (`backend/app/api/routes/unsubscribe.py:46-58`), POST `/api/unsubscribe` (`backend/app/api/routes/unsubscribe.py:73-110`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: HMAC token (no Clerk auth, public)
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET with token → validate HMAC → return masked email + current prefs. POST with action → opt_out / resubscribe / change_frequency / update_prefs.
- **References**: `backend/app/api/routes/unsubscribe.py`, `backend/app/workers/selloff_scraper.py` (token gen/validate)

### F-U-023: Landing Page
- **Status**: Active
- **What it does**: Marketing landing page with hero section, authority statement (34 airports, 34 destinations), how-it-works, FAQ with JSON-LD schema, pricing comparison, CTA sections.
- **Entry points**:
  - UI routes/pages: `/` (`tripsignal-ui/app/page.tsx`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None (public)
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Static page render.
- **References**: `tripsignal-ui/app/page.tsx`

### F-U-024: Pricing Page
- **Status**: Active
- **What it does**: Pricing comparison page (Free trial vs Pro).
- **Entry points**:
  - UI routes/pages: `/pricing` (`tripsignal-ui/app/pricing/page.tsx`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None (public)
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Static page render.
- **References**: `tripsignal-ui/app/pricing/page.tsx`

### F-U-025: Contact Page
- **Status**: Active
- **What it does**: Contact form page.
- **Entry points**:
  - UI routes/pages: `/contact` (`tripsignal-ui/app/contact/page.tsx`)
  - API endpoints: POST `/api/contact-submit` (`tripsignal-ui/app/api/contact-submit/route.ts`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None (public)
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: User fills contact form → submit to API route.
- **References**: `tripsignal-ui/app/contact/page.tsx`, `tripsignal-ui/app/api/contact-submit/route.ts`

### F-U-026: Legal Pages (Terms & Privacy)
- **Status**: Active
- **What it does**: Terms of Service (17 sections) and Privacy Policy (13 sections). Operated by "Mighty Web Design" (Saskatchewan). 7-day money-back guarantee. CASL-compliant.
- **Entry points**:
  - UI routes/pages: `/terms` (`tripsignal-ui/app/(legal)/terms/page.tsx`), `/privacy` (`tripsignal-ui/app/(legal)/privacy/page.tsx`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None (public)
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Static page render.
- **References**: `tripsignal-ui/app/(legal)/terms/page.tsx`, `tripsignal-ui/app/(legal)/privacy/page.tsx`

### F-U-027: 404 Page
- **Status**: Active
- **What it does**: Radar-themed 404 graphic with animations. Links to home and `/signals`.
- **Entry points**:
  - UI routes/pages: (catch-all) (`tripsignal-ui/app/not-found.tsx`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Next.js renders on unmatched routes.
- **References**: `tripsignal-ui/app/not-found.tsx`

### F-U-028: Sitemap & Robots
- **Status**: Active
- **What it does**: Dynamic sitemap and robots.txt. Sitemap includes `/` (priority 1.0), `/pricing` (0.8), `/sign-up` (0.7), `/sign-in` (0.5), `/contact` (0.5), `/terms` (0.3), `/privacy` (0.3). Robots disallows `/admin`, `/account/`, `/pro/`, `/accept-terms`, `/api/`.
- **Entry points**:
  - UI routes/pages: `/sitemap.xml` (`tripsignal-ui/app/sitemap.ts`), `/robots.txt` (`tripsignal-ui/app/robots.ts`)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Next.js generates at build/request time.
- **References**: `tripsignal-ui/app/sitemap.ts`, `tripsignal-ui/app/robots.ts`

### F-U-029: Google Analytics
- **Status**: Active
- **What it does**: GA4 tracking with lightweight gtag wrapper. Tracks `upgrade_to_pro`, `signal_created`, `signal_deleted` events. Handles ad blockers gracefully.
- **Entry points**:
  - UI routes/pages: — (loaded globally in layout)
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: None
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Script loaded in layout → events fired on user actions.
- **References**: `tripsignal-ui/lib/analytics.ts`, `tripsignal-ui/app/layout.tsx` (GA4 ID: `G-2DNWZ6VJ6X`)

---

## 3. Admin Features

### F-A-001: Admin Token Auth
- **Status**: Active
- **What it does**: All admin endpoints require `X-Admin-Token` header matching `ADMIN_TOKEN` env var. Frontend admin panel uses email-based access control (`ADMIN_EMAILS` env var) as a second gate.
- **Entry points**:
  - UI routes/pages: `/admin` (`tripsignal-ui/app/admin/page.tsx`)
  - API endpoints: — (dependency injected on all admin routes)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: `X-Admin-Token` header + `ADMIN_EMAILS` env var (frontend)
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Request includes `X-Admin-Token` header → `verify_admin()` compares against env var → 401 if mismatch.
- **References**: `backend/app/api/routes/admin.py:27-32`, `tripsignal-ui/app/admin/layout.tsx`

### F-A-002: List/Search Users
- **Status**: Active
- **What it does**: List users with basic filtering and a unified view with search/filter capabilities.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/users` (`backend/app/api/routes/admin.py:230`), GET `/admin/users-unified` (`backend/app/api/routes/admin.py:799`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query users with optional filters → return list.
- **References**: `backend/app/api/routes/admin.py:230`, `backend/app/api/routes/admin.py:799`

### F-A-003: User Lookup by Clerk ID
- **Status**: Active
- **What it does**: Look up a user by their Clerk ID.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/users/by-clerk-id/{clerk_id}` (`backend/app/api/routes/admin.py:212`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request with clerk_id → query user → return user data.
- **References**: `backend/app/api/routes/admin.py:212`

### F-A-004: Toggle Test User
- **Status**: Active
- **What it does**: Toggles `is_test_user` flag on a user.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PATCH `/admin/users/{user_id}/toggle-test` (`backend/app/api/routes/admin.py:274`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `is_test_user` (`backend/app/db/models/user.py:66-68`)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → toggle boolean → commit.
- **References**: `backend/app/api/routes/admin.py:274`

### F-A-005: Set User Plan
- **Status**: Active
- **What it does**: Manually set a user's plan type.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PATCH `/admin/users/{user_id}/set-plan` (`backend/app/api/routes/admin.py:300`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `plan_type`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → set plan type → commit.
- **References**: `backend/app/api/routes/admin.py:300`

### F-A-006: Set User Status
- **Status**: Active
- **What it does**: Manually set a user's plan status.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PATCH `/admin/users/{user_id}/set-status` (`backend/app/api/routes/admin.py:325`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `plan_status`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → set plan status → commit.
- **References**: `backend/app/api/routes/admin.py:325`

### F-A-007: Delete User (Admin)
- **Status**: Active
- **What it does**: Admin-initiated account deletion. Same PIPEDA-compliant 2-phase delete as user self-delete.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: DELETE `/admin/users/{user_id}` (`backend/app/api/routes/admin.py:351`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`, `signals`, `email_log`, `notifications_outbox`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: `ACCOUNT_DELETED_FREE`, `ACCOUNT_DELETED_PRO`
  - SMS templates: —
  - Trigger logic: Same as F-U-021
- **Key flows**: DELETE request → `delete_account(initiated_by="admin")` → same flow as user self-delete.
- **References**: `backend/app/api/routes/admin.py:351`, `backend/app/services/account.py:40-199`

### F-A-008: Undelete User
- **Status**: Active
- **What it does**: Restores a soft-deleted user. Clears deletion metadata, restores `plan_status=active`, re-enables email. Cannot restore if PII already scrubbed.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/admin/users/{user_id}/undelete` (`backend/app/api/routes/admin.py:387`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST request → `restore_account()` → clear deletion fields → set `plan_status=active` → re-enable email.
- **References**: `backend/app/api/routes/admin.py:387`, `backend/app/services/account.py:240-279`

### F-A-009: Hard Delete User
- **Status**: Active
- **What it does**: Permanently deletes a user and all associated data.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: DELETE `/admin/users/{user_id}/hard-delete` (`backend/app/api/routes/admin.py:414`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` (cascade)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: DELETE request → hard delete user row (cascades).
- **References**: `backend/app/api/routes/admin.py:414`

### F-A-010: Extend Trial
- **Status**: Active
- **What it does**: Extends a user's trial period.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PATCH `/admin/users/{user_id}/extend-trial` (`backend/app/api/routes/admin.py:450`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `trial_ends_at`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → extend `trial_ends_at` → commit.
- **References**: `backend/app/api/routes/admin.py:450`

### F-A-011: Reset Trial
- **Status**: Active
- **What it does**: Resets a user's trial to a fresh 14-day period.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: PATCH `/admin/users/{user_id}/reset-trial` (`backend/app/api/routes/admin.py:483`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users` — `trial_ends_at`, `trial_started_at`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: PATCH request → reset trial timestamps → commit.
- **References**: `backend/app/api/routes/admin.py:483`

### F-A-012: View User Feedback
- **Status**: Active
- **What it does**: View feedback entries for a user.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/users/{user_id}/feedback` (`backend/app/api/routes/admin.py:511`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: feedback (user-associated)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query feedback for user → return list.
- **References**: `backend/app/api/routes/admin.py:511`

### F-A-013: List Signals (Admin)
- **Status**: Active
- **What it does**: List all signals across all users.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/signals` (`backend/app/api/routes/admin.py:164`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signals`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query all signals → return list.
- **References**: `backend/app/api/routes/admin.py:164`

### F-A-014: Browse Deals
- **Status**: Active
- **What it does**: Browse active, new, and removed deals with filtering.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/deals` (`backend/app/api/routes/admin.py:701`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `deals`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request with optional filters → query deals → return list.
- **References**: `backend/app/api/routes/admin.py:701`

### F-A-015: Hotel Management
- **Status**: Active
- **What it does**: List hotels and update TripAdvisor URLs.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/hotels` (`backend/app/api/routes/admin.py:938`), PUT `/admin/hotels/{hotel_id}` (`backend/app/api/routes/admin.py:999`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `hotel_links` (`backend/app/db/models/hotel_link.py`)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET → list hotels. PUT → update TripAdvisor URL for hotel.
- **References**: `backend/app/api/routes/admin.py:938`, `backend/app/api/routes/admin.py:999`

### F-A-016: Health Dashboard
- **Status**: Active
- **What it does**: Admin-level system health overview.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/health` (`backend/app/api/routes/admin.py:98`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: various (aggregates)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → aggregate system metrics → return dashboard data.
- **References**: `backend/app/api/routes/admin.py:98`

### F-A-017: Notification Outbox
- **Status**: Active
- **What it does**: View notification outbox entries for debugging.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/debug/outbox` (`backend/app/api/routes/admin.py:68`), GET `/admin/notifications` (`backend/app/api/routes/admin.py:586`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `notifications_outbox` (`backend/app/db/models/notification_outbox.py`)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query outbox entries → return list.
- **References**: `backend/app/api/routes/admin.py:68`, `backend/app/api/routes/admin.py:586`

### F-A-018: Scrape Run History
- **Status**: Active
- **What it does**: View history of scrape runs with timing, deal counts, errors, proxy info.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/scrape-runs` (`backend/app/api/routes/admin.py:646`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `scrape_runs` (`backend/app/db/models/scrape_run.py`)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → query scrape_runs ordered by date → return list.
- **References**: `backend/app/api/routes/admin.py:646`

### F-A-019: Run Trial Expiry Check
- **Status**: Active
- **What it does**: Manually trigger trial expiry check.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/admin/run-trial-expiry` (`backend/app/api/routes/admin.py:534`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST request → run trial expiry check → return results.
- **References**: `backend/app/api/routes/admin.py:534`

### F-A-020: Enqueue Test Email
- **Status**: Active
- **What it does**: Creates a notification outbox entry for testing.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/admin/test-email` (`backend/app/api/routes/admin.py:43`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `notifications_outbox`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: — (test)
  - SMS templates: —
  - Trigger logic: Creates outbox entry directly
- **Key flows**: POST request → insert notification outbox entry → return.
- **References**: `backend/app/api/routes/admin.py:43`

### F-A-021: Send Test Email (Orchestrator)
- **Status**: Active
- **What it does**: Sends an email through the orchestrator for end-to-end testing.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/admin/send-test-email` (`backend/app/api/routes/admin.py:1050`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `email_log`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: Any (specified in request)
  - SMS templates: —
  - Trigger logic: Calls orchestrator `trigger()` directly
- **Key flows**: POST request with email type → orchestrator trigger → send → return result.
- **References**: `backend/app/api/routes/admin.py:1050`

### F-A-022: Preview Email Template
- **Status**: Active
- **What it does**: Renders an email template without sending, for preview/debugging.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/admin/preview-email` (`backend/app/api/routes/admin.py:1113`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `email_template_overrides`
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: Any (specified in request)
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST request with email type → render template → return HTML preview.
- **References**: `backend/app/api/routes/admin.py:1113`

### F-A-023: List Email Types
- **Status**: Active
- **What it does**: Lists all available email types in the system.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/email-types` (`backend/app/api/routes/admin.py:1031`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → return list of EmailType enum values.
- **References**: `backend/app/api/routes/admin.py:1031`

### F-A-024: Email Template CRUD
- **Status**: Active
- **What it does**: CRUD operations for DB-stored email template overrides.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/admin/email-templates` (`backend/app/api/routes/admin.py:1197`), GET `/admin/email-templates/{email_type}` (`backend/app/api/routes/admin.py:1229`), PUT `/admin/email-templates/{email_type}` (`backend/app/api/routes/admin.py:1265`), DELETE `/admin/email-templates/{email_type}` (`backend/app/api/routes/admin.py:1316`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `email_template_overrides` (`backend/app/db/models/email_template_override.py`)
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Standard CRUD on template override rows. Overrides take precedence over code-defined templates.
- **References**: `backend/app/api/routes/admin.py:1197-1316`

### F-A-025: Scraper Lab (Diagnostics)
- **Status**: Active
- **What it does**: Admin-only endpoints for testing SellOff scraper. Test page fetch by category and gateway. Returns parsed deals for validation.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/admin/scraper-lab/test-scrape` (`backend/app/api/routes/scraper_lab.py`), GET `/admin/scraper-lab/test-scrape-categories` (`backend/app/api/routes/scraper_lab.py`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: Admin token
  - Role: Admin
  - Plan gate: Admin token
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: POST with category/gateway → scrape page → parse deals → return parsed results (no DB write).
- **References**: `backend/app/api/routes/scraper_lab.py`

---

## 4. System Features

### F-S-001: SellOff Vacations Scraper
- **Status**: Active
- **What it does**: Scrapes SellOff Vacations website across 5 categories x 34 gateways. Parses deals via regex. Upserts deals with dedupe key. Tracks price deltas via `DealPriceHistory`. Deactivates stale/expired deals. Geo-locates proxy IP.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Docker service `selloff_scraper` (`docker-compose.yml:105-132`). 3 daily windows (7-9 AM, 12-2 PM, 6-8 PM ET), random scheduling within windows.
- **Data involved**:
  - Tables/models: `deals`, `deal_price_history`, `scrape_runs`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Wait for scrape window → fetch pages via proxy → parse HTML → upsert deals → record price history → deactivate stale deals → report to API.
- **References**: `backend/app/workers/selloff_scraper.py`, `docker-compose.yml:105-132` (proxy: DataImpulse `gw.dataimpulse.com:823`, country: `cr.ca`)

### F-S-002: Deal Matching (Scraper Cycle)
- **Status**: Active
- **What it does**: Region-based hierarchical matching (sub-region to parent). Filters: gateway, region, travel window (exact dates or month range), min/max nights, star rating, budget (per-person). Two copies exist and must be kept in sync.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Runs after each scrape cycle and on signal create/edit
- **Data involved**:
  - Tables/models: `deals`, `signals`, `deal_matches`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: For each deal, check all active signals → match on region hierarchy, gateway, dates, nights, stars, budget → create `DealMatch` row.
- **References**: `backend/app/workers/selloff_scraper.py` (`match_deal_to_signals()`), `backend/app/api/signals.py:59-152` (`_match_signal_against_deals()`)

### F-S-003: Scrape Lifecycle Reporting
- **Status**: Active
- **What it does**: Scraper reports cycle start/completion to API. Tracks: total deals, matches, errors, deactivated/expired deals, proxy IP/geo. Next scan time stored in `system_config` table.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/api/system/next-scan` (`backend/app/main.py:148-162`), GET `/api/system/next-scan` (`backend/app/main.py:164-180`), POST `/api/system/scrape-started` (`backend/app/main.py:183-196`), POST `/api/system/collection-complete` (`backend/app/main.py:199-248`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `scrape_runs` (`backend/app/db/models/scrape_run.py`), `system_config`
- **Access control**:
  - Auth: System token (`_verify_system_token`) for POST endpoints
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Scraper calls `scrape-started` → scrape → calls `collection-complete` with stats → calls `next-scan` with next scheduled time.
- **References**: `backend/app/main.py:148-248`

### F-S-004: Email Orchestrator
- **Status**: Active
- **What it does**: Central entry point for all 16 email types. Flow: load user → suppression checks → compute idempotency key → insert pending `email_log` row → render template → send via Resend → update `email_log` → stamp user-level `sent_at` flags.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Called by workers and API endpoints
- **Data involved**:
  - Tables/models: `email_log` (`backend/app/db/models/email_log.py`), `users`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: All 16 types
  - SMS templates: —
  - Trigger logic: `trigger()` function at line 89
- **Key flows**: `trigger(db, email_type, user_id, **kwargs)` → suppression check → idempotency check → render → send → log.
- **References**: `backend/app/services/email_orchestrator.py` (entry: line 89)

### F-S-005: Email Suppression Engine
- **Status**: Active
- **What it does**: 11 suppression rules in priority order that control email delivery.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `users`, `email_log`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: Rules: (1) `EMAIL_SUSPEND_NONCRITICAL` global kill, (2) deleted user, (3) `email_opt_out`, (4) `email_enabled=false`, (5) rate limit 2/24h, (6) upsell cooldown 48h, (7) canceled-after-deletion guard 24h, (8) daily cap 1 alert/day, (9) 3-strike rule, (10) frequency deferral, (11) re-engagement cap 1/60d
- **Key flows**: `_check_suppression(db, user, email_type)` → evaluate rules in order → return first matching suppression reason or None.
- **References**: `backend/app/services/email_orchestrator.py:226-337`

### F-S-006: Email Idempotency
- **Status**: Active
- **What it does**: Deterministic idempotency keys per email type to prevent duplicate sends. Format: `{type_prefix}:{scope_id}[:{qualifier}]`. Deduped via unique constraint on `email_log.idempotency_key`.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `email_log` — `idempotency_key` (unique)
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: `_build_idempotency_key(email_type, user_id, **kwargs)` → return deterministic key → insert with unique constraint.
- **References**: `backend/app/services/email_orchestrator.py:365-437`

### F-S-007: Deferred Email Delivery
- **Status**: Active
- **What it does**: Frequency-based deferral for non-instant users. Delivery windows: morning=8AM, noon=12PM, evening=6PM in user's timezone. Called by lifecycle worker.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Called by lifecycle worker every poll cycle
- **Data involved**:
  - Tables/models: `email_log`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: `drain_deferred_emails(db)` → find deferred `email_log` entries → check if delivery window matches user timezone → send.
- **References**: `backend/app/services/email_orchestrator.py:514-612`

### F-S-008: 16 Email Types (5 Categories)
- **Status**: Active
- **What it does**: Defines all email templates across 5 categories.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: **Transactional**: WELCOME, FIRST_SIGNAL, ACCOUNT_DELETED_FREE, ACCOUNT_DELETED_PRO. **Billing**: PRO_ACTIVATED, PAYMENT_FAILED, PAYMENT_FAILED_REMINDER, SUBSCRIPTION_CANCELED. **Alert**: MATCH_ALERT, MAJOR_DROP_ALERT, WEEKLY_DIGEST. **Upsell**: TRIAL_EXPIRING_SOON, TRIAL_EXPIRED_UPSELL. **Engagement**: NO_SIGNAL_REMINDER, NO_MATCH_UPDATE, INACTIVE_REENGAGEMENT.
  - SMS templates: —
  - Trigger logic: Each type has specific trigger conditions
- **Key flows**: Template render → subject line generation → HTML body → send via Resend.
- **References**: `backend/app/services/email_templates/templates.py`, `backend/app/services/email_orchestrator.py:41-57`

### F-S-009: Match Alert Intelligence
- **Status**: Active
- **What it does**: Batches deals per signal per run. Computes: new low, pct drop, percentile. Builds intel sentence (11 priority levels). Filters repeat deals (same deal within 7 days, price within +/-3%). Noise filter (skip if <3% change). Airport arbitrage and departure heatmap context.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Called during scrape cycle
- **Data involved**:
  - Tables/models: `deals`, `deal_matches`, `deal_price_history`, `signal_intel_cache`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: `process_signal_matches()` → batch deals per signal → compute intelligence → filter noise → build intel sentences.
- **References**: `backend/app/services/match_alert.py`

### F-S-010: Signal Intelligence
- **Status**: Active
- **What it does**: 7 intelligence modules: price history/percentile, trend direction/velocity/inflection, night-length sweet spot, star-price anomaly, price floor proximity, value score. Route-level: departure heatmap, destination index, booking countdown.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `signal_intel_cache` (`backend/app/db/models/signal_intel_cache.py`), `route_intel_cache` (`backend/app/db/models/route_intel_cache.py`)
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Compute intelligence metrics → cache results → serve to match alert and email systems.
- **References**: `backend/app/services/signal_intel.py`

### F-S-011: Lifecycle Email Worker
- **Status**: Active
- **What it does**: Polls every 300 seconds running 10 jobs in order: (0) drain deferred emails, (1) trial auto-extension, (2) trial expiring soon warning, (3) trial expired upsell, (4) no-signal reminder, (5) inactive re-engagement, (6) no-match update (PRO only), (7) payment failed reminders, (8) user mode refresh, (9) weekly digests.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Docker service `lifecycle_worker` (`docker-compose.yml:81-103`), poll interval: `LIFECYCLE_POLL_SECONDS=300`
- **Data involved**:
  - Tables/models: `users`, `email_log`, `signals`
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: TRIAL_EXPIRING_SOON, TRIAL_EXPIRED_UPSELL, NO_SIGNAL_REMINDER, INACTIVE_REENGAGEMENT, NO_MATCH_UPDATE, PAYMENT_FAILED_REMINDER, WEEKLY_DIGEST
  - SMS templates: —
  - Trigger logic: Each job has specific timing/eligibility conditions
- **Key flows**: Poll loop → run jobs 0-9 in sequence → sleep `LIFECYCLE_POLL_SECONDS`.
- **References**: `backend/app/workers/lifecycle_email_worker.py`, `docker-compose.yml:81-103`

### F-S-012: Notifications Log Worker
- **Status**: Active
- **What it does**: Outbox pattern worker. Claims batches of pending "log" channel notifications. Retry with exponential backoff: 30s, 2m, 10m, 30m, 2h. Max 8 attempts → status "dead".
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Docker service `notifications_worker` (`docker-compose.yml:58-79`)
- **Data involved**:
  - Tables/models: `notifications_outbox` (`backend/app/db/models/notification_outbox.py`)
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: Processes outbox entries with retry logic
- **Key flows**: Claim pending entries → attempt send → on failure, increment retry count and backoff → after 8 failures, mark "dead".
- **References**: `backend/app/workers/notifications_log_worker.py`, `docker-compose.yml:58-79`

### F-S-013: User Mode System
- **Status**: Active
- **What it does**: Email engagement modes: `active` → `passive` → `dormant`. Mode transitions driven by email open/click tracking. 3-strike rule downgrades active to passive. Click restores to active immediately.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: Lifecycle worker job 8 (user mode refresh)
- **Data involved**:
  - Tables/models: `users` — `email_mode` (`backend/app/db/models/user.py:97-99`)
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Check email engagement metrics → compute mode → update user.
- **References**: `backend/app/services/user_mode.py`

### F-S-014: Resend Email Webhooks
- **Status**: Active
- **What it does**: Handles Resend webhook events for email tracking. Events: opened (tracks opens, updates `last_email_opened_at`), clicked (restores `email_mode=active`), delivered, bounced, complained (auto-opts out).
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: POST `/api/webhooks/resend` (`backend/app/api/routes/resend_webhooks.py:77-163`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `email_log` — `metadata_json`, `users` — `last_email_opened_at`, `last_email_clicked_at`, `email_mode`, `email_opt_out`
- **Access control**:
  - Auth: Svix signature verification
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Receive webhook → verify Svix signature → look up `email_log` by `provider_message_id` → update tracking data → commit.
- **References**: `backend/app/api/routes/resend_webhooks.py:77-163`

### F-S-015: Email Open Tracking Pixel
- **Status**: Active
- **What it does**: Returns 1x1 transparent PNG. Tracks `opened_at` and `open_count` on `NotificationOutbox`. Cache-Control: no-cache.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/api/notifications/{notification_id}/pixel.png` (`backend/app/main.py:260-281`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: `notifications_outbox` — `opened_at`, `open_count`
- **Access control**:
  - Auth: — (public, embedded in emails)
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET request → look up notification → set `opened_at` if first open → increment `open_count` → return 1x1 PNG.
- **References**: `backend/app/main.py:260-281`

### F-S-016: Health Check
- **Status**: Active
- **What it does**: Simple health check endpoint.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: GET `/health` (`backend/app/api/routes/health.py:7-13`)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: GET `/health` → return `{"status": "ok"}`.
- **References**: `backend/app/api/routes/health.py:7-13`

### F-S-017: Request Logging Middleware
- **Status**: Active
- **What it does**: Logs all requests except `/health` and `/`. Format: timestamp, client IP, method, path, user ID, status code, elapsed ms.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: — (middleware, all routes)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Request enters → check path against skip list → time request → log on response.
- **References**: `backend/app/main.py:83-109`

### F-S-018: CORS Configuration
- **Status**: Active
- **What it does**: CORS middleware. Allowed origins: `https://tripsignal.ca`, `https://www.tripsignal.ca`.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: — (middleware, all routes)
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Request → check origin → add CORS headers.
- **References**: `backend/app/main.py:44-60`

### F-S-019: Docker Services
- **Status**: Active
- **What it does**: 6 Docker services: `postgres` (16-alpine), `api` (FastAPI), `caddy` (reverse proxy), `notifications_worker`, `lifecycle_worker`, `selloff_scraper`. Frontend runs as standalone container (not in compose).
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: `docker compose up -d` → all services start with health checks and restart policies.
- **References**: `docker-compose.yml`

### F-S-020: Reverse Proxy (Caddy)
- **Status**: Active
- **What it does**: Caddy routes `/api/*` to `api:8000`, everything else to `tripsignal-frontend:3000`. Handles TLS.
- **Entry points**:
  - UI routes/pages: —
  - API endpoints: —
  - Jobs/cron: —
- **Data involved**:
  - Tables/models: —
- **Access control**:
  - Auth: —
  - Role: —
  - Plan gate: N/A
- **Notifications**:
  - Email templates: —
  - SMS templates: —
  - Trigger logic: —
- **Key flows**: Incoming request → Caddy matches path → proxy to appropriate backend.
- **References**: `deploy/Caddyfile`

---

## 5. Feature Map

| ID | Feature | Area | Status | Plan Gate | API Endpoint(s) | UI Route(s) | Primary File(s) |
|----|---------|------|--------|-----------|------------------|-------------|------------------|
| F-U-001 | Clerk Auth | User | Active | None | POST `/api/clerk/webhook` | `/sign-in`, `/sign-up` | `clerk_webhook.py`, `middleware.ts` |
| F-U-002 | Beta Gate | User | Active | None | — | `/beta` | `middleware.ts` |
| F-U-003 | Terms Acceptance | User | Active | None | GET/POST `/users/terms-status`, `/users/accept-terms` | `/accept-terms` | `users.py` |
| F-U-004 | User Sync | User | Active | None | POST `/users/sync` | — | `users.py` |
| F-U-005 | Pro Activation | User | Active | Pro | PUT `/users/prefs` | `/pro/activate` | `users.py`, `middleware.ts` |
| F-U-006 | Create Signal | User | Active | Free:1 / Pro:10 | POST `/api/signals` | `/signals/new` | `signals.py` |
| F-U-007 | List Signals | User | Active | None | GET `/api/signals` | `/signals` | `signals.py` |
| F-U-008 | Edit Signal | User | Active | None | PATCH `/api/signals/{id}` | `/signals/[id]/edit` | `signals.py` |
| F-U-009 | Delete Signal | User | Active | None | DELETE `/api/signals/{id}` | — | `signals.py` |
| F-U-010 | Get Signal | User | Active | None | GET `/api/signals/{id}` | — | `signals.py` |
| F-U-011 | View Matches | User | Active | None | GET `/api/signals/{id}/matches` | — | `deal_matches.py` |
| F-U-012 | Toggle Favourite | User | Active | None | PATCH `…/matches/{id}/favourite` | — | `deal_matches.py` |
| F-U-013 | Manual Match | User | Active | None | POST `…/matches` | — | `deal_matches.py` |
| F-U-014 | Stripe Checkout | User | Active | N/A | POST `/api/billing/checkout` | `/pricing` | `billing.py` |
| F-U-015 | Billing Portal | User | Active | Pro | POST `/api/billing/portal` | — | `billing.py` |
| F-U-016 | Stripe Webhook | System | Active | N/A | POST `/api/billing/webhook` | — | `billing.py` |
| F-U-017 | Cancel Sub | User | Active | Pro | POST `/users/cancel-subscription` | `/account/cancel` | `users.py` |
| F-U-018 | View Prefs | User | Active | None | GET `/users/prefs` | `/account/notifications` | `users.py` |
| F-U-019 | Update Prefs | User | Active | None | PUT `/users/prefs` | — | `users.py` |
| F-U-020 | Account Settings | User | Active | None | — | `/account/settings` | — |
| F-U-021 | Delete Account | User | Active | None | DELETE `/users/me` | — | `users.py`, `account.py` |
| F-U-022 | Unsubscribe | User | Active | None | GET/POST `/api/unsubscribe` | `/unsubscribe` | `unsubscribe.py` |
| F-U-023 | Landing Page | User | Active | None | — | `/` | `page.tsx` |
| F-U-024 | Pricing Page | User | Active | None | — | `/pricing` | — |
| F-U-025 | Contact Page | User | Active | None | — | `/contact` | — |
| F-U-026 | Legal Pages | User | Active | None | — | `/terms`, `/privacy` | — |
| F-U-027 | 404 Page | User | Active | None | — | — | `not-found.tsx` |
| F-U-028 | Sitemap & Robots | User | Active | None | — | — | `sitemap.ts`, `robots.ts` |
| F-U-029 | Google Analytics | User | Active | None | — | — | `analytics.ts` |
| F-A-001 | Admin Auth | Admin | Active | Admin token | — | `/admin` | `admin.py` |
| F-A-002 | List Users | Admin | Active | Admin token | GET `/admin/users`, `/admin/users-unified` | — | `admin.py` |
| F-A-003 | User Lookup | Admin | Active | Admin token | GET `/admin/users/by-clerk-id/{id}` | — | `admin.py` |
| F-A-004 | Toggle Test | Admin | Active | Admin token | PATCH `/admin/users/{id}/toggle-test` | — | `admin.py` |
| F-A-005 | Set Plan | Admin | Active | Admin token | PATCH `/admin/users/{id}/set-plan` | — | `admin.py` |
| F-A-006 | Set Status | Admin | Active | Admin token | PATCH `/admin/users/{id}/set-status` | — | `admin.py` |
| F-A-007 | Admin Delete | Admin | Active | Admin token | DELETE `/admin/users/{id}` | — | `admin.py`, `account.py` |
| F-A-008 | Undelete | Admin | Active | Admin token | POST `/admin/users/{id}/undelete` | — | `admin.py`, `account.py` |
| F-A-009 | Hard Delete | Admin | Active | Admin token | DELETE `/admin/users/{id}/hard-delete` | — | `admin.py` |
| F-A-010 | Extend Trial | Admin | Active | Admin token | PATCH `/admin/users/{id}/extend-trial` | — | `admin.py` |
| F-A-011 | Reset Trial | Admin | Active | Admin token | PATCH `/admin/users/{id}/reset-trial` | — | `admin.py` |
| F-A-012 | User Feedback | Admin | Active | Admin token | GET `/admin/users/{id}/feedback` | — | `admin.py` |
| F-A-013 | List Signals | Admin | Active | Admin token | GET `/admin/signals` | — | `admin.py` |
| F-A-014 | Browse Deals | Admin | Active | Admin token | GET `/admin/deals` | — | `admin.py` |
| F-A-015 | Hotel Mgmt | Admin | Active | Admin token | GET/PUT `/admin/hotels` | — | `admin.py` |
| F-A-016 | Health Dashboard | Admin | Active | Admin token | GET `/admin/health` | — | `admin.py` |
| F-A-017 | Notif Outbox | Admin | Active | Admin token | GET `/admin/debug/outbox`, `/admin/notifications` | — | `admin.py` |
| F-A-018 | Scrape Runs | Admin | Active | Admin token | GET `/admin/scrape-runs` | — | `admin.py` |
| F-A-019 | Trial Expiry | Admin | Active | Admin token | POST `/admin/run-trial-expiry` | — | `admin.py` |
| F-A-020 | Test Email (outbox) | Admin | Active | Admin token | POST `/admin/test-email` | — | `admin.py` |
| F-A-021 | Test Email (orch) | Admin | Active | Admin token | POST `/admin/send-test-email` | — | `admin.py` |
| F-A-022 | Preview Email | Admin | Active | Admin token | POST `/admin/preview-email` | — | `admin.py` |
| F-A-023 | List Email Types | Admin | Active | Admin token | GET `/admin/email-types` | — | `admin.py` |
| F-A-024 | Template CRUD | Admin | Active | Admin token | GET/PUT/DELETE `/admin/email-templates` | — | `admin.py` |
| F-A-025 | Scraper Lab | Admin | Active | Admin token | POST/GET `/admin/scraper-lab/*` | — | `scraper_lab.py` |
| F-S-001 | Scraper | System | Active | N/A | — | — | `selloff_scraper.py` |
| F-S-002 | Deal Matching | System | Active | N/A | — | — | `selloff_scraper.py`, `signals.py` |
| F-S-003 | Scrape Reporting | System | Active | N/A | POST `/api/system/*` | — | `main.py` |
| F-S-004 | Email Orchestrator | System | Active | N/A | — | — | `email_orchestrator.py` |
| F-S-005 | Suppression Engine | System | Active | N/A | — | — | `email_orchestrator.py` |
| F-S-006 | Idempotency | System | Active | N/A | — | — | `email_orchestrator.py` |
| F-S-007 | Deferred Delivery | System | Active | N/A | — | — | `email_orchestrator.py` |
| F-S-008 | 16 Email Types | System | Active | N/A | — | — | `templates.py`, `email_orchestrator.py` |
| F-S-009 | Match Alert Intel | System | Active | N/A | — | — | `match_alert.py` |
| F-S-010 | Signal Intel | System | Active | N/A | — | — | `signal_intel.py` |
| F-S-011 | Lifecycle Worker | System | Active | N/A | — | — | `lifecycle_email_worker.py` |
| F-S-012 | Notif Worker | System | Active | N/A | — | — | `notifications_log_worker.py` |
| F-S-013 | User Modes | System | Active | N/A | — | — | `user_mode.py` |
| F-S-014 | Resend Webhooks | System | Active | N/A | POST `/api/webhooks/resend` | — | `resend_webhooks.py` |
| F-S-015 | Tracking Pixel | System | Active | N/A | GET `/api/notifications/{id}/pixel.png` | — | `main.py` |
| F-S-016 | Health Check | System | Active | N/A | GET `/health` | — | `health.py` |
| F-S-017 | Request Logging | System | Active | N/A | — | — | `main.py` |
| F-S-018 | CORS | System | Active | N/A | — | — | `main.py` |
| F-S-019 | Docker Services | System | Active | N/A | — | — | `docker-compose.yml` |
| F-S-020 | Reverse Proxy | System | Active | N/A | — | — | `Caddyfile` |

---

## 6. Open Questions / Gaps

1. **SMS notifications not implemented**: `sms_enabled` field exists on User model but no SMS sending implementation exists. Pricing page now says "Email alerts (SMS coming soon)" (fixed 2026-03-05).

2. ~~**Legacy `notification_delivery_speed` column**~~: **RESOLVED** (2026-03-05) — Column dropped via migration `s9h0i1j2k3l4`.

3. ~~**Signal limit backend enforcement missing**~~: **RESOLVED** (2026-03-05) — Backend now enforces free=1, pro=10 in `signals.py`.

4. ~~**Testimonials commented out**~~: **RESOLVED** (2026-03-05) — Removed `TestimonialsSection` component and data from landing page.

5. ~~**Legacy `alert_threshold` column**~~: **RESOLVED** (2026-03-05) — Column dropped via migration `s9h0i1j2k3l4`.

6. ~~**Quiet hours partially implemented**~~: **DECOMMISSIONED** (2026-03-05) — Feature decommissioned per product decision. Backend columns remain but are unused.

7. ~~**Price trend calculation inconsistency**~~: **RESOLVED** (2026-03-05) — `deal_matches.py` now compares current vs previous price (not first-seen), matching the scraper's approach.

8. ~~**`system_config` table has no model**~~: **RESOLVED** (2026-03-05) — Model added at `backend/app/db/models/system_config.py` with migration.

9. ~~**Dual contact form implementations**~~: **RESOLVED** (2026-03-05) — Deleted dead `/api/contact` route. `/contact-submit` is the sole working contact form.

10. ~~**Welcome email sent twice**~~: **RESOLVED** (2026-03-05) — Removed direct Resend call from `/accept-terms-submit`. Backend orchestrator is now the single source.

11. ~~**Cancel subscription frontend route gap**~~: **RESOLVED** (2026-03-05) — Created `tripsignal-ui/app/user/cancel-subscription/route.ts` proxy route.

12. **Dual admin auth mechanisms**: Frontend checks DB `user.role`, backend checks `X-Admin-Token` env var. These are disconnected — revoking DB role hides UI but doesn't revoke backend access. Recommendation: unify on Clerk JWT + DB role check. Accepted risk for now.

13. ~~**`collection-complete` endpoint missing auth**~~: **RESOLVED** (verified 2026-03-05) — Endpoint already had `_verify_system_token` dependency; original audit was stale.

14. ~~**Frontend API proxy routes undocumented**~~: **RESOLVED** (2026-03-05) — Full documentation at `docs/frontend-proxy-routes.md`. Covers 17 routes, auth patterns, middleware calls, and env vars.

---

## 7. Changelog

### 2026-03-05 — Gap fixes + scraper reliability

**Resolved gaps**: #2 (dead `notification_delivery_speed` column), #3 (signal limit enforcement), #4 (dead testimonials), #5 (dead `alert_threshold` column), #6 (quiet hours decommissioned), #7 (price trend inconsistency), #8 (system_config model), #9 (dual contact forms), #10 (duplicate welcome email), #11 (cancel subscription proxy), #13 (collection-complete auth), #14 (proxy routes documented)

**New features**:
- Scraper reliability: outer try/except with crash reporting, run_id correlation, SIGTERM graceful shutdown
- Graduated deal staleness: deals must miss 3 consecutive scrape cycles (~24h) before deactivation. New `last_seen_at` and `missed_cycles` columns on Deal model.
- Admin panel: consolidated ScraperTab + ScraperLabTab into ScrapeDataTab
- Frontend build fix: lazy-init Resend client in contact-submit route

### 2026-03-04 — Format v2.0 (initial)

**Added** (migrated from v1.0 format):
F-U-001, F-U-002, F-U-003, F-U-004, F-U-005, F-U-006, F-U-007, F-U-008, F-U-009, F-U-010,
F-U-011, F-U-012, F-U-013, F-U-014, F-U-015, F-U-016, F-U-017, F-U-018, F-U-019, F-U-020,
F-U-021, F-U-022, F-U-023, F-U-024, F-U-025, F-U-026, F-U-027, F-U-028, F-U-029,
F-A-001, F-A-002, F-A-003, F-A-004, F-A-005, F-A-006, F-A-007, F-A-008, F-A-009, F-A-010,
F-A-011, F-A-012, F-A-013, F-A-014, F-A-015, F-A-016, F-A-017, F-A-018, F-A-019, F-A-020,
F-A-021, F-A-022, F-A-023, F-A-024, F-A-025,
F-S-001, F-S-002, F-S-003, F-S-004, F-S-005, F-S-006, F-S-007, F-S-008, F-S-009, F-S-010,
F-S-011, F-S-012, F-S-013, F-S-014, F-S-015, F-S-016, F-S-017, F-S-018, F-S-019, F-S-020

**Changed**: —

**Deprecated**: —

**ID mapping from v1.0**: `F-U01` → `F-U-001`, `F-A01` → `F-A-001`, `F-S01` → `F-S-001` (pattern: insert hyphen, zero-pad to 3 digits)
