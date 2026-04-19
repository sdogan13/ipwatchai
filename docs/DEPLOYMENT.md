# IP Watch AI Deployment

Last updated: 2026-04-19
Status: Current

## Purpose

This file documents the current deployment paths used by this repo.

There are two main modes:
- local Docker stack for development and smoke testing
- prod-style Docker Compose overlay for server deployment

## Core Services

The current compose setup is built around:
- `postgres`
- `redis`
- `backend`
- `nginx`

Optional services:
- `cloudflared`
- `caddy`

## Environment Files

Local Docker stack:
- `.env.production`

Prod-style overlay:
- `deploy/.env.prod`

Local Python app:
- `.env`

Important:
- the base compose file mounts local host paths and is developer-machine oriented
- the prod overlay is the current canonical server deploy path

## Local Docker Stack

Use this when you want the app, PostgreSQL, Redis, and nginx on a local machine.

Setup:

```powershell
Copy-Item .env.production.example .env.production
```

Set at least:
- `DB_PASSWORD`
- `AUTH_SECRET_KEY`
- `REDIS_PASSWORD` if enabled
- `DATA_PATH`
- `CLIENTS_PATH`
- `HF_HOME`
- `TORCH_HOME`

Start the stack:

```powershell
docker compose up -d postgres redis backend nginx
```

Useful endpoints:
- app health: `http://127.0.0.1:8000/health`
- nginx: `http://127.0.0.1:8080`
- postgres: `127.0.0.1:5433`
- redis: `127.0.0.1:6379`

Notes:
- Docker bootstraps the database from `deploy/schema.sql`
- the base local stack exposes PostgreSQL on host port `5433`

## Prod-Style Deploy Path

This is the current canonical deploy path for server-style environments:

```powershell
docker compose --env-file deploy/.env.prod -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
```

Stop:

```powershell
docker compose --env-file deploy/.env.prod -f docker-compose.yml -f deploy/docker-compose.prod.yml down
```

Check merged config:

```powershell
docker compose --env-file deploy/.env.prod -f docker-compose.yml -f deploy/docker-compose.prod.yml config
```

Current prod-style behavior:
- backend reads `deploy/.env.prod`
- nginx uses `deploy/nginx.prod.conf`
- postgres is exposed on `127.0.0.1:5432`
- GPU reservation is disabled by default in the prod overlay unless explicitly reintroduced

## Database Bootstrap

Fresh database bootstrap starts from:
- `deploy/schema.sql`

Additional schema evolution lives in:
- `migrations/`

Notable migration-backed areas include:
- payments
- creative suite tables
- trademark applications
- trademark events

## Health Checks

Backend:

```powershell
curl http://127.0.0.1:8000/health
```

Nginx:

```powershell
curl http://127.0.0.1:8080/health
```

## Operational Notes

- `main.py` is still the supported app entrypoint
- the backend container runs `uvicorn main:app`
- local and prod-style compose modes use different env files; do not assume `.env` drives Docker
- if you change ports, env files, or bootstrap schema behavior, update `README.md` and this file together
