"""Post-ingest status repair for rows with publication evidence."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import RealDictCursor, execute_values

from db.pool import close_pool, get_connection, release_connection
from pipeline import ingest_bootstrap as _bootstrap
from pipeline.ingest_rules import (
    DB_STATUS_APPLIED,
    DB_STATUS_PUBLISHED,
    DB_STATUS_REGISTERED,
    _explicit_db_status_from_text,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepairCandidate:
    id: str
    application_no: str
    current_status: str | None = None
    status_source: str | None = None
    bulletin_no: str | None = None
    bulletin_date: Any = None
    gazette_no: str | None = None
    gazette_date: Any = None
    registration_no: str | None = None
    registration_date: Any = None


@dataclass(frozen=True)
class AppStatusEvidence:
    status_text: str
    source_file: str
    resolved_status: str


@dataclass(frozen=True)
class RepairDecision:
    candidate: RepairCandidate
    target_status: str
    target_source: str
    reason: str
    evidence: AppStatusEvidence | None = None

    @property
    def evidence_date(self):
        return (
            self.candidate.registration_date
            or self.candidate.gazette_date
            or self.candidate.bulletin_date
            or date.today()
        )


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _candidate_from_row(row: Any) -> RepairCandidate:
    getter = row.get if hasattr(row, "get") else lambda key, default=None: row[key]
    return RepairCandidate(
        id=str(getter("id")),
        application_no=getter("application_no"),
        current_status=getter("current_status"),
        status_source=getter("status_source"),
        bulletin_no=getter("bulletin_no"),
        bulletin_date=getter("bulletin_date"),
        gazette_no=getter("gazette_no"),
        gazette_date=getter("gazette_date"),
        registration_no=getter("registration_no"),
        registration_date=getter("registration_date"),
    )


def _iter_json_records(payload: Any) -> Iterable[dict]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(payload, dict):
        return

    if payload.get("APPLICATIONNO") or payload.get("TRADEMARK"):
        yield payload

    for key in ("records", "data", "trademarks", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item


def _is_app_metadata_path(path: Path, root_dir: Path | None = None) -> bool:
    try:
        parts = path.relative_to(root_dir).parts if root_dir else path.parts
    except ValueError:
        parts = path.parts
    return any(part.upper().startswith("APP") for part in parts[:-1])


def _record_application_no(record: dict) -> str | None:
    trademark = record.get("TRADEMARK") if isinstance(record.get("TRADEMARK"), dict) else {}
    return record.get("APPLICATIONNO") or trademark.get("APPLICATIONNO")


def _record_status_text(record: dict) -> str:
    value = record.get("STATUS") or record.get("STATUS_TEXT") or record.get("status") or ""
    return str(value).strip()


def build_app_status_lookup(root_dir: Path, app_nos: Iterable[str]) -> dict[str, AppStatusEvidence]:
    """Return latest explicit non-applied APP status evidence by application number."""
    wanted = {app_no for app_no in app_nos if app_no}
    if not wanted:
        return {}

    matches: dict[str, tuple[float, AppStatusEvidence]] = {}
    for path in root_dir.rglob("*.json"):
        if not _is_app_metadata_path(path, root_dir):
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        for record in _iter_json_records(payload):
            app_no = _record_application_no(record)
            if app_no not in wanted:
                continue

            status_text = _record_status_text(record)
            resolved = _explicit_db_status_from_text(status_text)
            if not resolved or resolved == DB_STATUS_APPLIED:
                continue

            evidence = AppStatusEvidence(
                status_text=status_text,
                source_file=str(path),
                resolved_status=resolved,
            )
            previous = matches.get(app_no)
            if previous is None or mtime >= previous[0]:
                matches[app_no] = (mtime, evidence)

    return {app_no: evidence for app_no, (_, evidence) in matches.items()}


def decide_repair(candidate: RepairCandidate, app_evidence: AppStatusEvidence | None = None) -> RepairDecision | None:
    if app_evidence and app_evidence.resolved_status != DB_STATUS_APPLIED:
        return RepairDecision(
            candidate=candidate,
            target_status=app_evidence.resolved_status,
            target_source="APP",
            reason="app_explicit_status",
            evidence=app_evidence,
        )

    if _has_value(candidate.gazette_no) or _has_value(candidate.registration_no) or candidate.registration_date:
        return RepairDecision(
            candidate=candidate,
            target_status=DB_STATUS_REGISTERED,
            target_source="GZ",
            reason="gazette_or_registration_evidence",
        )

    if _has_value(candidate.bulletin_no):
        return RepairDecision(
            candidate=candidate,
            target_status=DB_STATUS_PUBLISHED,
            target_source="BLT",
            reason="bulletin_evidence",
        )

    return None


def fetch_repair_candidates(conn, *, app_no: str | None = None, limit: int | None = None) -> list[RepairCandidate]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = [DB_STATUS_APPLIED]
    filters = [
        "tm.current_status = %s::tm_status",
        """
        (
            (tm.bulletin_no IS NOT NULL AND btrim(tm.bulletin_no::text) <> '')
            OR (tm.gazette_no IS NOT NULL AND btrim(tm.gazette_no::text) <> '')
            OR (tm.registration_no IS NOT NULL AND btrim(tm.registration_no::text) <> '')
            OR tm.registration_date IS NOT NULL
        )
        """,
    ]
    if app_no:
        filters.append("tm.application_no = %s")
        params.append(app_no)

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    cur.execute(
        f"""
        SELECT
            tm.id,
            tm.application_no,
            tm.current_status::text AS current_status,
            tm.status_source,
            tm.bulletin_no,
            tm.bulletin_date,
            tm.gazette_no,
            tm.gazette_date,
            tm.registration_no,
            tm.registration_date
        FROM trademarks tm
        WHERE {" AND ".join(filters)}
        ORDER BY tm.application_no NULLS LAST
        {limit_clause}
        """,
        params,
    )
    return [_candidate_from_row(row) for row in cur.fetchall()]


def apply_repair_decisions(conn, decisions: list[RepairDecision], *, dry_run: bool = False) -> int:
    if dry_run or not decisions:
        return 0

    cur = conn.cursor()
    update_rows = [
        (decision.target_status, decision.target_source, decision.candidate.application_no)
        for decision in decisions
    ]
    execute_values(
        cur,
        """
        UPDATE trademarks AS tm
        SET current_status = v.status::tm_status,
            status_source = v.status_source,
            updated_at = NOW()
        FROM (VALUES %s) AS v(status, status_source, application_no)
        WHERE tm.application_no = v.application_no
        """,
        update_rows,
    )

    history_rows = [
        (
            decision.candidate.id,
            decision.evidence_date,
            "STATUS_REPAIR",
            decision.evidence.source_file if decision.evidence else "status_repair",
            (
                f"{decision.candidate.current_status} -> {decision.target_status}; "
                f"reason={decision.reason}"
            ),
        )
        for decision in decisions
    ]
    if history_rows:
        try:
            cur.execute("SAVEPOINT before_status_repair_history")
            execute_values(
                cur,
                """
                INSERT INTO trademark_history (trademark_id, event_date, event_type, source_file, description)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                history_rows,
            )
            cur.execute("RELEASE SAVEPOINT before_status_repair_history")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT before_status_repair_history")
            logger.warning("Status repair history insert skipped", exc_info=True)

    conn.commit()

    try:
        from utils.status_reconciler import update_final_status_batch

        update_final_status_batch(
            conn,
            app_nos=[decision.candidate.application_no for decision in decisions],
        )
    except Exception:
        logger.warning("Final status recompute skipped after status repair", exc_info=True)

    return len(decisions)


def run_status_repair(
    *,
    conn=None,
    root_dir: Path | str | None = None,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    owns_conn = conn is None
    if root_dir is None:
        root_dir = _bootstrap.default_ingest_root()
    root_dir = Path(root_dir)

    if owns_conn:
        conn = get_connection()

    try:
        candidates = fetch_repair_candidates(conn, app_no=app_no, limit=limit)
        app_lookup = build_app_status_lookup(root_dir, [candidate.application_no for candidate in candidates])
        decisions = [
            decision
            for candidate in candidates
            if (decision := decide_repair(candidate, app_lookup.get(candidate.application_no)))
        ]

        repaired = apply_repair_decisions(conn, decisions, dry_run=dry_run)
        summary = {
            "status": "success",
            "dry_run": dry_run,
            "candidates": len(candidates),
            "decisions": len(decisions),
            "repaired": 0 if dry_run else repaired,
            "would_repair": len(decisions) if dry_run else 0,
            "app_explicit": sum(1 for decision in decisions if decision.reason == "app_explicit_status"),
            "default_published": sum(1 for decision in decisions if decision.reason == "bulletin_evidence"),
            "default_registered": sum(1 for decision in decisions if decision.reason == "gazette_or_registration_evidence"),
            "samples": [
                {
                    "application_no": decision.candidate.application_no,
                    "from": decision.candidate.current_status,
                    "to": decision.target_status,
                    "source": decision.target_source,
                    "reason": decision.reason,
                    "app_status_text": decision.evidence.status_text if decision.evidence else None,
                }
                for decision in decisions[:20]
            ],
        }
        return summary
    finally:
        if owns_conn and conn is not None:
            release_connection(conn)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair applied statuses with publication evidence.")
    parser.add_argument("--dry-run", action="store_true", help="Report decisions without changing the database.")
    parser.add_argument("--app-no", type=str, default=None, help="Repair only one application number.")
    parser.add_argument("--limit", type=int, default=None, help="Limit suspicious rows considered.")
    parser.add_argument("--root-dir", type=str, default=None, help="Root directory containing APP metadata.")
    args = parser.parse_args()

    try:
        summary = run_status_repair(
            root_dir=args.root_dir,
            dry_run=args.dry_run,
            app_no=args.app_no,
            limit=args.limit,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
