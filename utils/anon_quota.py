"""Per-IP anonymous quotas for marketing-tasting AI features.

The Marka Nice-class AI suggester is reachable from the public landing page
without authentication so that prospects can try the feature once before
signing up. After their daily allowance, the route returns 401 and the UI
prompts them to upgrade.

Backed by Redis with a TTL on a date-keyed counter so the budget naturally
resets at UTC midnight without a sweep job.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Tuple


logger = logging.getLogger(__name__)


ANON_CLASS_SUGGEST_DAILY_LIMIT = 1
_TTL_SECONDS = 90_000  # ~25 h, gives the date-keyed entry slack across UTC

_redis_client = None


def _get_redis():
    """Lazy-init a shared Redis client on db=0. Returns None if unavailable."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis  # type: ignore
            from config.settings import settings

            _redis_client = redis.Redis(
                host=settings.redis.host,
                port=settings.redis.port,
                password=settings.redis.password,
                db=0,
            )
            _redis_client.ping()
        except Exception as exc:
            logger.warning("anon_quota: redis unavailable (%s) — falling closed", exc)
            _redis_client = None
    return _redis_client


def check_and_consume_anon_class_suggest(ip: str) -> Tuple[bool, int]:
    """Atomically increment the per-IP daily counter and decide whether the
    request is allowed.

    Returns:
        (allowed, remaining_after_this_call) — ``remaining`` is non-negative.

    Behaviour:
        * No IP / Redis unavailable → ``(False, 0)`` — fail closed so we don't
          accidentally hand out unlimited free LLM calls when Redis is down.
        * Counter <= daily limit → allowed.
        * Counter > daily limit → blocked.
    """
    if not ip:
        return False, 0
    client = _get_redis()
    if client is None:
        return False, 0
    key = f"anon_class_suggest:{date.today().isoformat()}:{ip}"
    try:
        used = client.incr(key)
        if used == 1:
            client.expire(key, _TTL_SECONDS)
    except Exception as exc:
        logger.warning("anon_quota: incr failed (%s) — falling closed", exc)
        return False, 0
    remaining = max(0, ANON_CLASS_SUGGEST_DAILY_LIMIT - int(used))
    return int(used) <= ANON_CLASS_SUGGEST_DAILY_LIMIT, remaining


def _client_ip_from_request(request) -> str:
    """Extract the originating IP. Behind cloudflared/nginx so trust the
    forwarded headers; fall back to the socket peer."""
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""
