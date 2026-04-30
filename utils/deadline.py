"""
Turkish Trademark Opposition Deadline Calculator
=================================================
Single source of truth for appeal/opposition deadline calculation.

Per Turkish IP law (KHK m.42 / 6769 SMK m.18):
    Opposition period = 2 calendar months from bulletin publication date.

This function MUST be the ONLY place in the codebase that computes
appeal/opposition deadlines. All other modules import from here.
"""

from datetime import date, datetime
from dateutil.relativedelta import relativedelta


def calculate_appeal_deadline(bulletin_date) -> date | None:
    """
    Calculate Turkish trademark opposition deadline.

    Per KHK m.42: 2 calendar months from bulletin publication date.

    Examples:
        2025-01-15 → 2025-03-15
        2025-12-31 → 2026-02-28 (end-of-month clamping)
        2025-01-31 → 2025-03-31

    Args:
        bulletin_date: The date the trademark was published in the official
                      bulletin. Accepts date, datetime, or ISO-format string.
                      Returns None if input is None or invalid.

    Returns:
        date: The opposition deadline (last day to file), or None.
    """
    if bulletin_date is None:
        return None

    # Convert string to date
    if isinstance(bulletin_date, str):
        bulletin_date = bulletin_date.strip()
        if not bulletin_date:
            return None
        try:
            bulletin_date = date.fromisoformat(bulletin_date)
        except (ValueError, TypeError):
            return None

    # Convert datetime to date
    if isinstance(bulletin_date, datetime):
        bulletin_date = bulletin_date.date()

    if not isinstance(bulletin_date, date):
        return None

    return bulletin_date + relativedelta(months=2)


def _repair_mojibake(text):
    if not isinstance(text, str):
        return text

    repaired = text
    for _ in range(3):
        if not any(ch in repaired for ch in ("Ã", "Ä", "Å", "Â")):
            break
        candidate = repaired
        for source_encoding in ("latin1", "cp1252"):
            try:
                candidate = repaired.encode(source_encoding).decode("utf-8")
                break
            except UnicodeError:
                continue
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def _canonical_status(final_status: str) -> str:
    final_status = _repair_mojibake(final_status or "")
    status_aliases = {
        "Refused": "Reddedildi",
        "Withdrawn": "Geri Çekildi",
        "Opposed": "İtiraz Edildi",
        "Registered": "Tescil Edildi",
        "Renewed": "Yenilendi",
        "Expired": "Süresi Doldu",
        "Applied": "Başvuruldu",
        "Published": "Yayında",
    }
    return status_aliases.get(final_status, final_status)


def classify_deadline_status(final_status: str, bulletin_date, appeal_deadline) -> dict:
    """
    Classify a trademark conflict's deadline status for UI display.
    Accepts clean Turkish, English canonical statuses, and legacy mojibake input.
    """
    today = date.today()
    final_status = _canonical_status(final_status)

    resolved_statuses = {"Reddedildi", "Geri Çekildi"}
    opposed_statuses = {"İtiraz Edildi"}
    registered_statuses = {"Tescil Edildi", "Yenilendi"}
    expired_statuses = {"Süresi Doldu"}
    applied_statuses = {"Başvuruldu"}

    if final_status in resolved_statuses:
        return {
            "status": "resolved",
            "days_remaining": None,
            "label_tr": "Tehdit kalkt\u0131",
            "urgency": "none",
        }

    if final_status in opposed_statuses:
        return {
            "status": "opposed",
            "days_remaining": None,
            "label_tr": "\u0130tiraz edilmi\u015f",
            "urgency": "info",
        }

    if final_status in registered_statuses:
        return {
            "status": "registered",
            "days_remaining": None,
            "label_tr": "Tescil edildi",
            "urgency": "low",
        }

    if final_status in expired_statuses:
        return {
            "status": "expired",
            "days_remaining": None,
            "label_tr": "Marka s\u00fcresi doldu",
            "urgency": "none",
        }

    if not bulletin_date or final_status in applied_statuses:
        if not bulletin_date:
            return {
                "status": "pre_publication",
                "days_remaining": None,
                "label_tr": "Erken Uyarı - Henüz yayınlanmadı",
                "urgency": "info",
            }

    if appeal_deadline:
        if isinstance(appeal_deadline, str):
            try:
                appeal_deadline = date.fromisoformat(appeal_deadline)
            except (ValueError, TypeError):
                appeal_deadline = None

        if isinstance(appeal_deadline, datetime):
            appeal_deadline = appeal_deadline.date()

        if appeal_deadline:
            days_remaining = (appeal_deadline - today).days

            if days_remaining < 0:
                return {
                    "status": "expired",
                    "days_remaining": days_remaining,
                    "label_tr": "\u0130tiraz s\u00fcresi doldu",
                    "urgency": "none",
                }
            if days_remaining <= 7:
                return {
                    "status": "active_critical",
                    "days_remaining": days_remaining,
                    "label_tr": f"\u0130tiraz s\u00fcresi: {days_remaining} g\u00fcn kald\u0131",
                    "urgency": "critical",
                }
            if days_remaining <= 30:
                return {
                    "status": "active_urgent",
                    "days_remaining": days_remaining,
                    "label_tr": f"\u0130tiraz s\u00fcresi: {days_remaining} g\u00fcn kald\u0131",
                    "urgency": "urgent",
                }
            return {
                "status": "active",
                "days_remaining": days_remaining,
                "label_tr": f"\u0130tiraz s\u00fcresi: {days_remaining} g\u00fcn kald\u0131",
                "urgency": "normal",
            }

    return {
        "status": "unknown",
        "days_remaining": None,
        "label_tr": "Durum belirsiz",
        "urgency": "none",
    }
