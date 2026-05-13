"""EUIPO EU Trade Mark (EUTM) data collector.

Sister collector to ``data_collection.py`` (Marka TR), ``data_collection_patent.py``
and ``data_collection_tasarim.py``. Pulls EUTM records from the EUIPO
Trademark Search API v1.1.0 into ``bulletins/Marka_EU/`` on disk.

Output layout::

    bulletins/Marka_EU/
    ├── _media/                          (shared media pool, keyed by application_no)
    │   ├── 000274084.jpg
    │   ├── 000274084_thumb.jpg
    │   └── ...
    ├── BACKFILL_1996-01/
    │   ├── manifest.json
    │   ├── page_0001.json
    │   └── page_0002.json
    └── DELTA_2026-05-12/
        ├── manifest.json
        └── page_0001.json

A window is "complete" iff its ``manifest.json`` carries ``completed_at`` and
the on-disk page count matches ``total_pages``. Re-runs skip complete windows;
partial windows resume from the next missing page.

CLI::

    # Full historical corpus (year-month windows, 1996 → today, including media)
    python data_collection_eutm.py --backfill

    # Partial backfill from a date
    python data_collection_eutm.py --backfill --since 2020-01-01

    # One specific month
    python data_collection_eutm.py --window 1996-01

    # Yesterday's delta (default if no flags given is --delta yesterday)
    python data_collection_eutm.py --delta 2026-05-12

    # Skip media downloads (metadata-only)
    python data_collection_eutm.py --backfill --no-media

    # Smoke test
    python data_collection_eutm.py --window 1996-01 --limit 2

    # Scan existing windows and fill in missing media
    python data_collection_eutm.py --media-only

See docs/EUIPO_DATA_NOTES.md for API + field reference.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_LOCAL_PROJECT_ROOT))
load_dotenv()

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka_EU"
MEDIA_DIR = BULLETINS_ROOT / "_media"

# Sandbox endpoints. Switch to production via --production once confirmed.
SANDBOX_TOKEN_URL = "https://auth-sandbox.euipo.europa.eu/oidc/accessToken"
SANDBOX_API_BASE = "https://api-sandbox.euipo.europa.eu/trademark-search"
PROD_TOKEN_URL = "https://auth.euipo.europa.eu/oidc/accessToken"
PROD_API_BASE = "https://api.euipo.europa.eu/trademark-search"

PAGE_SIZE = 100                  # Spec: min 10, max 100
RATE_LIMIT_BUFFER = 1000         # Pause if remaining drops below this
RATE_LIMIT_HARD_FLOOR = 100      # Long sleep if remaining drops below this
RATE_LIMIT_PAUSE_SECS = 60       # Sleep when at buffer
RATE_LIMIT_HARD_PAUSE_SECS = 300 # Sleep when at hard floor
HTTP_TIMEOUT = 60
MAX_RETRIES = 5  # 2+4+8+16 = 30s max backoff before giving up on persistent 5xx
MEDIA_WORKERS = 5
INTER_REQUEST_SLEEP = 0.1        # Polite pacing between sequential requests

# Mark features that have no image (skip image fetch for these).
NO_IMAGE_FEATURES = {"WORD"}

# Mark features that may have sound/video/model attachments.
SOUND_FEATURES = {"SOUND", "MULTIMEDIA"}
VIDEO_FEATURES = {"MOTION", "MULTIMEDIA"}
MODEL_FEATURES = {"SHAPE_3D", "HOLOGRAM"}

CONTENT_TYPE_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/tiff": ".tif",
    "image/png": ".png",
    "image/gif": ".gif",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "video/mp4": ".mp4",
    "model/obj": ".obj",
    "model/x3d+xml": ".x3d",
    "model/stl": ".stl",
    "application/octet-stream": ".bin",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [EUTM] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.eutm_collector")


# --------------------------------------------------------------------------
# OAuth + HTTP layer
# --------------------------------------------------------------------------


@dataclass
class ApiClient:
    """Stateful client: caches OAuth token, throttles on rate-limit signals."""
    api_key: str
    api_secret: str
    token_url: str
    api_base: str
    _token: Optional[str] = None
    _token_expires_at: float = 0.0
    _session: requests.Session = field(default_factory=requests.Session)

    def _refresh_token(self) -> None:
        logger.info("Requesting new access token (scope=uid)")
        resp = self._session.post(
            self.token_url,
            data={"grant_type": "client_credentials", "scope": "uid"},
            auth=(self.api_key, self.api_secret),
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        # Refresh 5 minutes before actual expiry to be safe.
        self._token_expires_at = time.time() + max(60, int(body.get("expires_in", 7200)) - 300)

    def _ensure_token(self) -> str:
        if not self._token or time.time() >= self._token_expires_at:
            self._refresh_token()
        return self._token  # type: ignore[return-value]

    def _headers(self, accept: str = "application/json") -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "X-IBM-Client-Id": self.api_key,
            "Accept": accept,
        }

    @staticmethod
    def _parse_remaining(header_value: Optional[str]) -> Optional[int]:
        """Parse 'name=default,24993;' → 24993. Returns None on failure."""
        if not header_value:
            return None
        try:
            inner = header_value.strip().rstrip(";").split(",")[-1]
            return int(inner)
        except (ValueError, IndexError):
            return None

    def _maybe_throttle(self, resp: requests.Response) -> None:
        remaining = self._parse_remaining(resp.headers.get("X-RateLimit-Remaining"))
        if remaining is None:
            return
        if remaining < RATE_LIMIT_HARD_FLOOR:
            logger.warning("Rate-limit remaining=%d below hard floor, sleeping %ds",
                           remaining, RATE_LIMIT_HARD_PAUSE_SECS)
            time.sleep(RATE_LIMIT_HARD_PAUSE_SECS)
        elif remaining < RATE_LIMIT_BUFFER:
            logger.info("Rate-limit remaining=%d below buffer, sleeping %ds",
                        remaining, RATE_LIMIT_PAUSE_SECS)
            time.sleep(RATE_LIMIT_PAUSE_SECS)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None,
            accept: str = "application/json", stream: bool = False) -> requests.Response:
        """GET with retry on 401 (refresh token), 429 (Retry-After), 5xx (backoff)."""
        url = f"{self.api_base}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.get(
                    url,
                    headers=self._headers(accept=accept),
                    params=params,
                    timeout=HTTP_TIMEOUT,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_exc = exc
                backoff = min(60, 2 ** attempt)
                logger.warning("HTTP error on %s (attempt %d/%d): %s — retry in %ds",
                               url, attempt, MAX_RETRIES, exc, backoff)
                time.sleep(backoff)
                continue

            if resp.status_code == 401 and attempt < MAX_RETRIES:
                logger.info("401 received, refreshing token and retrying")
                self._token = None
                continue
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "60") or "60")
                logger.warning("429 rate-limited, sleeping %ds", retry_after)
                time.sleep(retry_after)
                continue
            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                backoff = min(60, 2 ** attempt)
                logger.warning("5xx (%d) on %s — retry in %ds",
                               resp.status_code, url, backoff)
                time.sleep(backoff)
                continue

            self._maybe_throttle(resp)
            return resp
        raise RuntimeError(f"GET {url} exhausted retries; last error: {last_exc}")


# --------------------------------------------------------------------------
# Window enumeration
# --------------------------------------------------------------------------


def _enumerate_year_month_windows(since: date, until: date) -> List[str]:
    """Yield 'YYYY-MM' strings from `since`'s month to `until`'s month inclusive."""
    out = []
    y, m = since.year, since.month
    end_y, end_m = until.year, until.month
    while (y, m) <= (end_y, end_m):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _month_bounds(window: str) -> Tuple[date, date]:
    """'1996-01' → (date(1996,1,1), date(1996,2,1))."""
    y, m = (int(p) for p in window.split("-"))
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return start, end


# --------------------------------------------------------------------------
# Manifest + idempotency
# --------------------------------------------------------------------------


def _window_dir(window: str, kind: str) -> Path:
    return BULLETINS_ROOT / f"{kind}_{window}"


def _manifest_path(window_dir: Path) -> Path:
    return window_dir / "manifest.json"


def _read_manifest(window_dir: Path) -> Optional[Dict[str, Any]]:
    p = _manifest_path(window_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_manifest(window_dir: Path, manifest: Dict[str, Any]) -> None:
    window_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(window_dir).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _window_is_complete(window_dir: Path) -> bool:
    m = _read_manifest(window_dir)
    if not m or not m.get("completed_at"):
        return False
    if m.get("partial"):
        return False
    expected = m.get("expected_pages") or m.get("total_pages", 0)
    actual = sum(1 for p in window_dir.glob("page_*.json"))
    return actual >= expected


def _existing_page_count(window_dir: Path) -> int:
    return sum(1 for p in window_dir.glob("page_*.json"))


# --------------------------------------------------------------------------
# Page + media download
# --------------------------------------------------------------------------


def _page_path(window_dir: Path, page_idx_0based: int) -> Path:
    # page_0001 = 0-indexed page 0, etc.
    return window_dir / f"page_{page_idx_0based + 1:04d}.json"


def _media_ext_for_response(resp: requests.Response, fallback: str) -> str:
    ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return CONTENT_TYPE_TO_EXT.get(ct, fallback)


def _media_filename(application_no: str, kind: str, ext: str) -> str:
    """kind ∈ {image, thumb, sound, video, model}. ext starts with '.'."""
    if kind == "thumb":
        return f"{application_no}_thumb{ext}"
    return f"{application_no}{ext}"


def _media_exists(application_no: str, kind: str) -> bool:
    """Quick check: does any media file already exist for this app+kind?"""
    if kind == "thumb":
        pattern = f"{application_no}_thumb.*"
    else:
        pattern = f"{application_no}.*"
    for p in MEDIA_DIR.glob(pattern):
        if kind == "thumb" and "_thumb." in p.name:
            if p.stat().st_size > 0:
                return True
        elif kind != "thumb" and "_thumb." not in p.name:
            # Skip the thumb file when checking for the main image.
            if p.stat().st_size > 0:
                return True
    return False


def _download_media(client: ApiClient, application_no: str, kind: str,
                    path: str, fallback_ext: str) -> Dict[str, Any]:
    """Download one media item. Returns a status dict for the manifest."""
    if _media_exists(application_no, kind):
        return {"app": application_no, "kind": kind, "status": "skip_exists"}
    try:
        resp = client.get(path, accept="*/*", stream=True)
    except Exception as exc:
        return {"app": application_no, "kind": kind, "status": "error",
                "error": f"{type(exc).__name__}: {exc}"}
    if resp.status_code == 404:
        return {"app": application_no, "kind": kind, "status": "absent"}
    if resp.status_code != 200:
        return {"app": application_no, "kind": kind, "status": "error",
                "error": f"HTTP {resp.status_code}"}
    ext = _media_ext_for_response(resp, fallback_ext)
    out = MEDIA_DIR / _media_filename(application_no, kind, ext)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    try:
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
        tmp.replace(out)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return {"app": application_no, "kind": kind, "status": "error",
                "error": f"OSError: {exc}"}
    return {"app": application_no, "kind": kind, "status": "ok",
            "bytes": out.stat().st_size, "ext": ext}


def _collect_media_targets(record: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """For one search-result trademark, return list of (kind, api_path, fallback_ext).
    Conservative: try image+thumbnail for all non-WORD marks (404 is harmless);
    try sound/video/model only when feature suggests it.
    """
    app_no = record.get("applicationNumber")
    if not app_no:
        return []
    feature = record.get("markFeature", "")
    targets: List[Tuple[str, str, str]] = []
    if feature not in NO_IMAGE_FEATURES:
        targets.append(("image", f"/trademarks/{app_no}/image", ".jpg"))
        targets.append(("thumb", f"/trademarks/{app_no}/image/thumbnail", ".jpg"))
    if feature in SOUND_FEATURES:
        targets.append(("sound", f"/trademarks/{app_no}/sound", ".mp3"))
    if feature in VIDEO_FEATURES:
        targets.append(("video", f"/trademarks/{app_no}/video", ".mp4"))
    if feature in MODEL_FEATURES:
        targets.append(("model", f"/trademarks/{app_no}/model", ".obj"))
    return targets


# --------------------------------------------------------------------------
# Harvest one window
# --------------------------------------------------------------------------


@dataclass
class HarvestStats:
    pages_fetched: int = 0
    records_total: int = 0
    media_ok: int = 0
    media_skipped: int = 0
    media_absent: int = 0
    media_errors: int = 0


def _harvest_window(client: ApiClient, window: str, kind: str, query: str,
                    download_media: bool, limit_pages: Optional[int]) -> HarvestStats:
    """Paginate /trademarks for one window, save pages, optionally download media."""
    window_dir = _window_dir(window, kind)
    window_dir.mkdir(parents=True, exist_ok=True)

    if _window_is_complete(window_dir):
        logger.info("[%s] window already complete, skipping", window)
        return HarvestStats()

    existing_manifest = _read_manifest(window_dir) or {}
    started_at = existing_manifest.get("started_at") or datetime.now(timezone.utc).isoformat()
    media_executor = ThreadPoolExecutor(max_workers=MEDIA_WORKERS) if download_media else None
    stats = HarvestStats()
    media_futures: List = []
    total_pages_seen: Optional[int] = None

    resume_from_page = _existing_page_count(window_dir)
    if resume_from_page:
        logger.info("[%s] resuming from page %d", window, resume_from_page + 1)

    page = resume_from_page
    drained = False
    hit_page_cap = False
    while True:
        if limit_pages is not None and stats.pages_fetched >= limit_pages:
            logger.info("[%s] --limit reached (%d pages)", window, limit_pages)
            break

        params = {
            "query": query,
            "size": str(PAGE_SIZE),
            "page": str(page),
            "sort": "applicationNumber:asc",
        }
        resp = client.get("/trademarks", params=params)
        if resp.status_code != 200:
            logger.error("[%s] page %d failed: HTTP %d body=%s",
                         window, page, resp.status_code, resp.text[:300])
            break

        try:
            body = resp.json()
        except ValueError:
            logger.error("[%s] page %d non-JSON response", window, page)
            break

        page_path = _page_path(window_dir, page)
        page_path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        stats.pages_fetched += 1

        records = body.get("trademarks") or []
        stats.records_total += len(records)
        total_pages_seen = body.get("totalPages", total_pages_seen)
        logger.info("[%s] page %d/%s saved (%d records; total=%s)",
                    window, page + 1,
                    total_pages_seen if total_pages_seen is not None else "?",
                    len(records),
                    body.get("totalElements"))

        if download_media and media_executor is not None:
            for record in records:
                for kind_, api_path, ext in _collect_media_targets(record):
                    fut = media_executor.submit(
                        _download_media, client, record["applicationNumber"],
                        kind_, api_path, ext,
                    )
                    media_futures.append(fut)

        time.sleep(INTER_REQUEST_SLEEP)

        # End of pagination?
        if total_pages_seen is not None and page + 1 >= total_pages_seen:
            drained = True
            break
        page += 1
        # Defensive cap: never let one window exceed 5000 pages (would mean
        # we picked a too-wide query).
        if page > 5000:
            logger.error("[%s] aborted: page cap exceeded", window)
            hit_page_cap = True
            break

    # Drain media futures
    media_results: List[Dict[str, Any]] = []
    if media_executor is not None:
        logger.info("[%s] awaiting %d media downloads...", window, len(media_futures))
        for fut in as_completed(media_futures):
            r = fut.result()
            media_results.append(r)
            s = r["status"]
            if s == "ok":
                stats.media_ok += 1
            elif s == "skip_exists":
                stats.media_skipped += 1
            elif s == "absent":
                stats.media_absent += 1
            else:
                stats.media_errors += 1
        media_executor.shutdown(wait=True)

    completed_at = datetime.now(timezone.utc).isoformat()
    # A window is complete only if the loop fully drained the API's pagination.
    # Any other exit (HTTP error, --limit, defensive page cap) leaves the window
    # partial so a re-run resumes from the next missing page.
    partial = not drained
    manifest = {
        "window": window,
        "window_type": kind,
        "query": query,
        "started_at": started_at,
        "completed_at": completed_at,
        "partial": partial,
        "pages_on_disk": stats.pages_fetched + resume_from_page,
        "expected_pages": total_pages_seen,
        "total_records_seen_this_run": stats.records_total,
        "page_size": PAGE_SIZE,
        "media_downloaded": stats.media_ok,
        "media_skipped": stats.media_skipped,
        "media_absent": stats.media_absent,
        "media_errors": stats.media_errors,
        "media_results_sample": media_results[:50] if media_results else [],
    }
    _write_manifest(window_dir, manifest)
    logger.info("[%s] done: pages=%d records=%d media_ok=%d errors=%d",
                window, stats.pages_fetched, stats.records_total,
                stats.media_ok, stats.media_errors)
    return stats


# --------------------------------------------------------------------------
# Top-level orchestration
# --------------------------------------------------------------------------


def _query_for_backfill_month(window: str) -> str:
    start, end = _month_bounds(window)
    return f"applicationDate>={start.isoformat()} and applicationDate<{end.isoformat()}"


def _query_for_delta_day(day: date) -> str:
    nxt = day + timedelta(days=1)
    return f"updateDate>={day.isoformat()} and updateDate<{nxt.isoformat()}"


def run_backfill(client: ApiClient, since: date, until: date,
                 download_media: bool, limit_pages: Optional[int],
                 limit_windows: Optional[int]) -> None:
    windows = _enumerate_year_month_windows(since, until)
    logger.info("Backfill: %d windows %s..%s",
                len(windows), windows[0], windows[-1])
    for idx, window in enumerate(windows, 1):
        if limit_windows is not None and idx > limit_windows:
            logger.info("--limit-windows reached (%d)", limit_windows)
            return
        logger.info("=== window %d/%d: %s ===", idx, len(windows), window)
        try:
            _harvest_window(client, window, "BACKFILL",
                            _query_for_backfill_month(window),
                            download_media, limit_pages)
        except Exception as exc:
            logger.exception("[%s] window failed: %s", window, exc)


def run_delta(client: ApiClient, day: date,
              download_media: bool, limit_pages: Optional[int]) -> None:
    window = day.isoformat()
    logger.info("Delta: %s", window)
    _harvest_window(client, window, "DELTA",
                    _query_for_delta_day(day),
                    download_media, limit_pages)


def run_media_only(client: ApiClient, only_windows: Optional[List[str]]) -> None:
    """Walk existing window folders and download any missing media referenced
    in their saved pages."""
    BULLETINS_ROOT.mkdir(parents=True, exist_ok=True)
    target_dirs: List[Path] = []
    for d in sorted(BULLETINS_ROOT.iterdir()):
        if not d.is_dir() or d.name == "_media":
            continue
        if only_windows and not any(w in d.name for w in only_windows):
            continue
        target_dirs.append(d)
    logger.info("Media-only: scanning %d window folders", len(target_dirs))
    executor = ThreadPoolExecutor(max_workers=MEDIA_WORKERS)
    futures = []
    for window_dir in target_dirs:
        for page_file in sorted(window_dir.glob("page_*.json")):
            try:
                body = json.loads(page_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for record in body.get("trademarks") or []:
                for kind, api_path, ext in _collect_media_targets(record):
                    futures.append(executor.submit(
                        _download_media, client, record["applicationNumber"],
                        kind, api_path, ext,
                    ))
    logger.info("Media-only: %d media targets queued", len(futures))
    ok = absent = err = skip = 0
    for fut in as_completed(futures):
        r = fut.result()
        if r["status"] == "ok":
            ok += 1
        elif r["status"] == "skip_exists":
            skip += 1
        elif r["status"] == "absent":
            absent += 1
        else:
            err += 1
    executor.shutdown(wait=True)
    logger.info("Media-only done: ok=%d skip=%d absent=%d err=%d",
                ok, skip, absent, err)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_client(use_production: bool) -> ApiClient:
    key = os.environ.get("EUIPO_API_KEY")
    secret = os.environ.get("EUIPO_API_SECRET")
    if not key or not secret:
        raise SystemExit("EUIPO_API_KEY and EUIPO_API_SECRET must be set in .env")
    token_url = PROD_TOKEN_URL if use_production else SANDBOX_TOKEN_URL
    api_base = PROD_API_BASE if use_production else SANDBOX_API_BASE
    return ApiClient(api_key=key, api_secret=secret,
                     token_url=token_url, api_base=api_base)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true",
                      help="Full historical backfill by year-month windows")
    mode.add_argument("--window", metavar="YYYY-MM",
                      help="Backfill a single month")
    mode.add_argument("--delta", metavar="YYYY-MM-DD", nargs="?", const="yesterday",
                      help="Delta by updateDate for one day (default: yesterday)")
    mode.add_argument("--media-only", action="store_true",
                      help="Don't fetch metadata; download missing media for existing windows")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Backfill start date (default: 1996-01-01)")
    parser.add_argument("--until", metavar="YYYY-MM-DD",
                        help="Backfill end date (default: today's first of month - 1 day)")
    parser.add_argument("--no-media", action="store_true",
                        help="Skip media downloads (faster)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Stop after N pages per window (testing)")
    parser.add_argument("--limit-windows", type=int, metavar="N",
                        help="Stop after N windows in backfill mode (testing)")
    parser.add_argument("--only-window", action="append", metavar="YYYY-MM",
                        help="Media-only mode: restrict to these window names")
    parser.add_argument("--production", action="store_true",
                        help="Use production API endpoints (default: sandbox)")
    args = parser.parse_args()

    BULLETINS_ROOT.mkdir(parents=True, exist_ok=True)
    client = _build_client(args.production)
    download_media = not args.no_media

    if args.media_only:
        run_media_only(client, args.only_window)
        return 0

    if args.window:
        try:
            _month_bounds(args.window)
        except (ValueError, IndexError):
            raise SystemExit(f"--window must be YYYY-MM, got {args.window!r}")
        _harvest_window(client, args.window, "BACKFILL",
                        _query_for_backfill_month(args.window),
                        download_media, args.limit)
        return 0

    if args.backfill:
        since = date(1996, 1, 1)
        if args.since:
            since = date.fromisoformat(args.since)
        today = date.today()
        # Exclude current month from backfill — let --delta handle today.
        if today.month == 1:
            until = date(today.year - 1, 12, 1)
        else:
            until = date(today.year, today.month - 1, 1)
        if args.until:
            until = date.fromisoformat(args.until)
        run_backfill(client, since, until, download_media,
                     args.limit, args.limit_windows)
        return 0

    # Default = delta for yesterday.
    if args.delta == "yesterday" or args.delta is None:
        day = date.today() - timedelta(days=1)
    else:
        day = date.fromisoformat(args.delta)
    run_delta(client, day, download_media, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
