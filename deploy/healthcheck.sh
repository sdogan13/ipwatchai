#!/bin/bash
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:80/health 2>/dev/null || echo "000")
if [ "$HTTP_CODE" != "200" ]; then
    echo "$(date) ALERT: Backend returned HTTP $HTTP_CODE" >> /var/log/trademark-health.log
    cd /opt/trademark-app
    docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart backend
fi
