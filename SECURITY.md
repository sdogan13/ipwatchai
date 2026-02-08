# Security & Subscription Architecture

## Authentication
- JWT (HS256) with access (30 min) and refresh (7 day) tokens
- Refresh tokens accepted via POST body (not Authorization header)
- Every authenticated request verified against DB (user active, org active)
- Rate limiting on all endpoints via slowapi
- Password hashing with bcrypt (10 rounds)
- Token type verification (`access` vs `refresh`) prevents token misuse

## Authorization
- Role-based access control: `owner`, `admin`, `member`, `viewer`
- `require_role()` dependency for admin-only endpoints
- `require_permission()` dependency for granular permissions
- All admin/IDF/pipeline endpoints require `owner` or `admin` role

## Plan Limits
- All limits defined in `utils/subscription.py` -> `PLAN_FEATURES`
- Use `get_plan_limit(plan_name, feature)` to check any limit
- Never hardcode plan names or limit values in endpoint code
- Four plans: `free`, `starter`, `professional`, `enterprise`

### Plan Feature Matrix

| Feature | Free | Starter | Professional | Enterprise |
|---------|------|---------|-------------|------------|
| Daily Quick Searches | 50 | 200 | 500 | Unlimited |
| Monthly Live Searches | 0 | 0 | 50 | 500 |
| Max Watchlist Items | 5 | 25 | 50 | 500 |
| Max Users | 3 | 5 | 10 | 50 |
| Monthly Name Generations | 20 | 50 | 200 | 1000 |
| Monthly Logo Runs | 1 | 3 | 15 | 50 |
| Holder Portfolio | No | No | Yes | Yes |
| CSV Export (Leads) | No | No | No | Yes |
| Live Scraping | No | No | Yes | Yes |
| Auto-Scan | None | Weekly (25) | Daily (50) | Daily (500) |

## Adding a New Plan Limit
1. Add the key to every plan in `PLAN_FEATURES`
2. Add tracking in `api_usage` table if it's a counted limit
3. Add the check in the relevant endpoint using `get_plan_limit()`
4. Pricing page updates automatically (Jinja2 renders from `PLAN_FEATURES`)
5. Add tests in `tests/test_plan_features.py` and `tests/test_security_audit.py`

## Environment Variables Required in Production
- `AUTH_SECRET_KEY` -- must not be default (app refuses to start when `ENVIRONMENT=production`)
- `DB_PASSWORD` -- no hardcoded fallback (required field, no default)
- `ENVIRONMENT=production` -- triggers secret key validation

## Rate Limits
- Login/register/refresh: `LOGIN_RATE_LIMIT`/minute per IP (default: 5)
- Public search endpoints: 10/minute per IP
- General API: `API_RATE_LIMIT`/minute per user (default: 100)
- Rate limit hits are logged with user identity, endpoint, and IP

## Logging
- Failed login attempts: IP, email, reason (user_not_found/deactivated/wrong_password)
- Successful login/registration: user ID, email, IP
- Token refresh events
- Blocked authentication: inactive user, deactivated org
- Plan limits reached: user, plan, feature, limit value
- High usage indicators: 80% of daily quick search cap
- Rate limit hits: identity, endpoint, IP, limit exceeded

## Security Audit (Completed 2026-02-08)

### Step 1 -- Critical Security Fixes
- Removed hardcoded database passwords
- Deleted public debug/test endpoints
- Enforced JWT secret in production via validator
- Added DB verification in `get_current_user()` (user active, org active)
- Activated slowapi rate limiting with custom logging handler
- Fixed refresh token flow (body-based, type verification)
- Secured `/api/search/simple` endpoint

### Step 2 -- Limit Enforcement & Abuse Prevention
- Consolidated all limits into single `PLAN_FEATURES` dict
- Removed duplicated limit definitions across codebase
- Added daily quick search caps with usage tracking
- Added monthly name generation caps per organization
- Added auto-scan plan gating (free orgs skipped)
- Enforced watchlist item limits via `get_plan_limit()`

### Step 3 -- Frontend Alignment, Cleanup & Logging
- Pricing page renders dynamically from `PLAN_FEATURES`
- Credit badges and plan indicators in dashboard
- API key auth stub removed (was always returning None)
- Security & operational logging added throughout
- Unified `GET /api/v1/usage/summary` endpoint created

### Step 4 -- Static Verification & Documentation
- Grep audit: no hardcoded passwords, no duplicated limits, no dead code
- Endpoint auth audit: all admin endpoints require role check
- Code review: all fixes verified
- Migration files reviewed (non-destructive, safe defaults)
- Test suite created (16 plan feature tests passing)

## Known Limitations (Future Work)
- No payment integration (Stripe) -- upgrade via email to sales
- No self-service credit purchase
- No plan expiration/cancellation logic
- No billing portal or invoicing
- Email verification exists in schema but is not enforced
- Runtime integration tests pending server availability
- No API key authentication (placeholder TODO exists)
- No CSRF protection for form submissions (JWT-based, so not required for API)
