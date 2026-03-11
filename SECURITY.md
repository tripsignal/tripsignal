# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in TripSignal, please report it responsibly.

**Email**: security@tripsignal.ca

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Triage**: Within 7 days
- **Fix**: Dependent on severity, typically within 30 days

## Scope

- tripsignal.ca (web application)
- API endpoints at tripsignal.ca/api/*
- Infrastructure and deployment configuration

## Out of Scope

- Social engineering attacks
- Denial of service attacks
- Issues in third-party dependencies (report upstream)

## Bug Bounty

TripSignal does not currently operate a bug bounty program.

## Accepted Risks

The following findings have been reviewed and accepted:

| Finding | Severity | Justification |
|---------|----------|---------------|
| B310: `urllib.request.urlopen` in scraper | Medium | Scraper only fetches hardcoded selloffvacations.com URLs. No user-controlled input reaches `urlopen`. |
| F821: SQLAlchemy string forward references | Low | Standard SQLAlchemy `Mapped["Model"]` pattern for relationship type hints. Not actual undefined names. |
| B008: `Depends()` in FastAPI defaults | Low | Standard FastAPI dependency injection pattern. Not a security issue. |
| Backend `x-clerk-user-id` header trust | Medium | Resolved. Backend now requires cryptographic JWT verification via Clerk JWKS (RS256). User ID is derived from the verified `sub` claim in `deps.py:get_clerk_user_id()`. The `x-clerk-user-id` header is neither accepted nor trusted. |
| pip-audit skipped locally | Info | Local Python 3.14 incompatible with pinned `psycopg[binary]==3.2.3`. Audit runs correctly in Docker (Python 3.12). |
