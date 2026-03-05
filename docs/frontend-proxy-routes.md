# Frontend Proxy Routes

The Next.js frontend proxies requests to the backend API (`http://api:8000`) via route handlers. This keeps the backend URL private and lets the frontend inject auth headers server-side.

## Auth Patterns

| Pattern | Description |
|---------|-------------|
| **Clerk + Bearer** | `auth()` extracts Clerk user ID, `getToken()` provides JWT forwarded as `Authorization: Bearer ...` |
| **Clerk + Admin Token** | Same as above, plus injects `X-Admin-Token` from env var |
| **Stripe Signature** | No user auth; verified by Stripe signature header |
| **None** | Public or local-only route |

## User Routes

| Frontend Route | Method | Backend Endpoint | Auth | Description |
|---------------|--------|-----------------|------|-------------|
| `/user/me` | GET | `/users/by-clerk-id/{clerkId}` | Clerk + Bearer | Fetch authenticated user profile |
| `/user/prefs` | GET, PUT | `/users/prefs` | Clerk + Bearer | Get/update user preferences |
| `/user/sync` | POST | `/users/sync` | Clerk + Bearer | Sync session data (IP, UA, timezone) |
| `/user/delete` | DELETE | `/users/me` | Clerk + Bearer | Delete user account |
| `/user/cancel-subscription` | POST | `/users/cancel-subscription` | Clerk + Bearer | Cancel active subscription |
| `/accept-terms-submit` | POST | `/users/accept-terms` | Clerk | Accept terms of service |

## Billing Routes

| Frontend Route | Method | Backend Endpoint | Auth | Description |
|---------------|--------|-----------------|------|-------------|
| `/billing/checkout` | POST | `/billing/checkout` | Clerk + Bearer | Create Stripe checkout session |
| `/billing/portal` | POST | `/billing/portal` | Clerk + Bearer | Create Stripe customer portal session |
| `/api/billing/webhook` | POST | `/billing/webhook` | Stripe Signature | Receive Stripe webhook events |

Note: `/api/billing/checkout` and `/api/billing/portal` are duplicates of the `/billing/*` routes.

## Signals Routes

| Frontend Route | Method | Backend Endpoint | Auth | Description |
|---------------|--------|-----------------|------|-------------|
| `/api/signals` | GET, POST | `/api/signals` | Clerk + Bearer | List/create signals |
| `/api/signals/{id}` | PATCH, DELETE | `/api/signals/{id}` | Clerk + Bearer | Update/delete a signal |

## Admin Routes

| Frontend Route | Method | Backend Endpoint | Auth | Description |
|---------------|--------|-----------------|------|-------------|
| `/admin-proxy?path={path}` | GET, POST, PATCH, PUT, DELETE | `/{path}` (dynamic) | Clerk + Admin Token | Generic admin proxy; routes to any backend path |
| `/admin-plan` | PATCH | `/admin/users/{userId}/set-plan` | Clerk + Admin Token | Set user plan type |
| `/admin-status` | PATCH | `/admin/users/{userId}/set-status` | Clerk + Admin Token | Set user account status |
| `/api/admin/users/{id}/toggle-test` | PATCH | `/admin/users/{userId}/toggle-test` | Clerk + Admin Token | Toggle test mode for a user |

## Local-Only Routes (No Backend Proxy)

| Frontend Route | Method | Auth | Description |
|---------------|--------|------|-------------|
| `/contact-submit` | POST | Clerk | Send support email via Resend API |
| `/beta-login` | POST | None (rate limited) | Validate beta password, set cookie |
| `/api/admin` | GET | None | Health check, returns `{ ok: true }` |

## Middleware Direct Calls

The Next.js middleware (`middleware.ts`) makes two direct backend calls (not through proxy routes):

1. `GET /users/terms-status?clerk_id={clerkId}` -- redirects to `/accept-terms` if not accepted
2. `GET /users/by-clerk-id/{clerkId}` -- redirects to `/pro/activate` if Pro plan needs activation

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `API_URL` | All proxy routes | Backend base URL (default: `http://api:8000`) |
| `ADMIN_TOKEN` | Admin proxy routes | Forwarded as `X-Admin-Token` header |
| `RESEND_API_KEY` | `/contact-submit` | Resend email API key |
