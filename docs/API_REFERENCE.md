# IP Watch AI API Reference

Last updated: 2026-04-19
Status: Current high-level map

## Purpose

This file is a high-level map of the current API surface.

It is not a generated OpenAPI dump.
- use `/docs` when debug mode is enabled
- use `tests/test_api_endpoints.py` for the broadest contract coverage in this repo
- use the route modules in `api/` and `app_*.py` for implementation detail

## Base URLs

Local app:

```text
http://127.0.0.1:8000
```

Primary API prefix:

```text
/api/v1
```

Docs UI:
- `/docs` only when debug mode is enabled

## Authentication

Protected routes use JWT bearer auth:

```text
Authorization: Bearer <access_token>
```

Current auth flow lives under:
- `/api/v1/auth/register`
- `/api/v1/auth/login`
- `/api/v1/auth/refresh`
- `/api/v1/auth/change-password`
- `/api/v1/auth/forgot-password`
- `/api/v1/auth/reset-password`
- `/api/v1/auth/verify-email`
- `/api/v1/auth/resend-verification`
- `/api/v1/auth/me`

`/api/v1/auth/login` accepts either:
- JSON body with `email` and `password`
- form body with `username` or `email`, plus `password`

## Public And System Endpoints

System:
- `GET /health`
- `GET /api/info`
- `GET /api/v1/status`
- `GET /api/v1/config`

Public search and portfolio:
- `GET /api/v1/search/public`
- `POST /api/v1/search/public`
- `GET /api/v1/portfolio/public`
- `GET /api/v1/portfolio/public/csv`

Nice class helpers:
- `GET /api/nice-classes`
- `POST /api/validate-classes`
- `POST /api/suggest-classes`

Legacy compatibility search utilities:
- `POST /api/search`
- `POST /api/search-by-image`
- `GET /api/search/simple` (deprecated)
- `POST /api/search/unified` (deprecated)

## Authenticated Route Groups

The current authenticated API is split by feature area.

Core account and org:
- `/api/v1/users`
- `/api/v1/user`
- `/api/v1/organization`
- `/api/v1/usage`

Search and trademark:
- `/api/v1/search/quick`
- `/api/v1/search/intelligent`
- `/api/v1/trademark`

Portfolio and monitoring:
- `/api/v1/watchlist`
- `/api/v1/alerts`
- `/api/v1/reports`
- `/api/v1/dashboard`

Commercial and workflow:
- `/api/v1/leads`
- `/api/v1/holders`
- `/api/v1/attorneys`
- `/api/v1/applications`
- `/api/v1/billing`
- `/api/v1/payments`

Admin, tooling, and pipeline:
- `/api/v1/admin`
- `/api/v1/tools`
- `/api/v1/pipeline`

## Common Usage Patterns

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

Public search:

```powershell
curl "http://127.0.0.1:8000/api/v1/search/public?query=wosen"
```

Login with JSON:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/auth/login `
  -H "Content-Type: application/json" `
  -d "{\"email\":\"mobiletest@test.com\",\"password\":\"Test1234!\"}"
```

Authenticated quick search:

```powershell
curl "http://127.0.0.1:8000/api/v1/search/quick?query=wosen&classes=9,35" `
  -H "Authorization: Bearer <access_token>"
```

Report generation:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/reports/generate `
  -H "Authorization: Bearer <access_token>" `
  -H "Content-Type: application/json" `
  -d "{\"report_type\":\"watchlist_summary\",\"file_format\":\"pdf\"}"
```

## Notes

- public search is rate-limited separately from authenticated search
- public landing-page search also enforces the free-tier daily quota and returns structured `429` detail when that quota is exhausted
- authenticated quick search reads the plan daily cap from runtime settings, and startup now realigns the known legacy quick-search overrides to the current product defaults
- some legacy routes remain for compatibility while newer flows live under `/api/v1`
- browser and live E2E suites in `tests/` are often the best source for real end-to-end request/response behavior
