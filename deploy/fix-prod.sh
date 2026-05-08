#!/usr/bin/env bash
# fix-prod.sh — bring ipwatchai.com back up after a deployment regression
#
# Run this on the prod server, in the deployment directory (where
# docker-compose.yml + deploy/ live). It:
#   1. Captures pre-state (so we can compare)
#   2. Pulls the latest cleaned codex branch
#   3. Rebuilds and restarts backend + nginx
#   4. Waits for backend to be healthy
#   5. Verifies /health returns 200 from inside the network
#
# Usage:
#   bash deploy/fix-prod.sh            # uses 'codex/scoring-engine-v2-text-visual'
#   bash deploy/fix-prod.sh main       # use a different branch
#
# This is non-destructive other than the backend container restart.
# Postgres + redis volumes are untouched.

set -euo pipefail

BRANCH="${1:-codex/scoring-engine-v2-text-visual}"
LOG=/tmp/ipwatch-deploy-$(date +%Y%m%d-%H%M%S).log
COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.prod.yml"

exec > >(tee -a "$LOG") 2>&1

echo "==================================================================="
echo "fix-prod.sh — branch=$BRANCH  log=$LOG"
echo "==================================================================="

echo
echo "--- pre-state: container status ---"
docker compose $COMPOSE_FILES ps || true

echo
echo "--- pre-state: backend last 50 log lines ---"
docker compose $COMPOSE_FILES logs backend --tail=50 --no-color 2>&1 | tail -60 || true

echo
echo "--- git: ensure on $BRANCH and up to date ---"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
git log --oneline -5

echo
echo "--- rebuild backend image ---"
docker compose $COMPOSE_FILES build backend

echo
echo "--- restart backend + nginx (postgres/redis untouched) ---"
docker compose $COMPOSE_FILES up -d --no-deps backend
docker compose $COMPOSE_FILES up -d --no-deps nginx

echo
echo "--- wait up to 240s for backend healthy ---"
deadline=$(( $(date +%s) + 240 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    status=$(docker compose $COMPOSE_FILES ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk '/^backend / {print $3}')
    echo "  [$(date +%H:%M:%S)] backend health: ${status:-unknown}"
    if [ "$status" = "healthy" ]; then
        break
    fi
    sleep 5
done

echo
echo "--- final: backend last 30 log lines ---"
docker compose $COMPOSE_FILES logs backend --tail=30 --no-color 2>&1 | tail -40

echo
echo "--- verify /health from inside nginx ---"
if docker compose $COMPOSE_FILES exec -T nginx curl -fsS -m 10 http://backend:8000/health; then
    echo
    echo "OK — backend /health returned 200"
else
    echo
    echo "FAIL — backend /health did not return 200"
    echo "Check the backend logs above for the crash reason."
    exit 2
fi

echo
echo "--- verify https://ipwatchai.com/health from outside ---"
if curl -fsS -m 15 -o /dev/null -w "https status=%{http_code} time=%{time_total}s\n" https://ipwatchai.com/health; then
    echo "OK — ipwatchai.com is back up"
else
    echo "FAIL — backend is healthy locally but not reachable via Cloudflare."
    echo "Check cloudflared tunnel: docker compose $COMPOSE_FILES logs cloudflared --tail=50"
    exit 3
fi

echo
echo "==================================================================="
echo "DONE. Full log: $LOG"
echo "==================================================================="
