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


def classify_deadline_status(final_status: str, bulletin_date, appeal_deadline) -> dict:
    """
    Classify a trademark conflict's deadline status for UI display.
    Returns: { status: str, days_remaining: int|None, label_tr: str, urgency: str }
    """
    today = date.today()
    final_status = final_status or ""
    status_aliases = {
        "Refused": "Reddedildi",
        "Withdrawn": "Geri Ã‡ekildi",
        "Opposed": "Ä°tiraz Edildi",
        "Registered": "Tescil Edildi",
        "Renewed": "Yenilendi",
        "Expired": "SÃ¼resi Doldu",
        "Applied": "BaÅŸvuruldu",
        "Published": "Yayında",
    }
    final_status = status_aliases.get(final_status, final_status)

    # Threat removed — mark was refused or withdrawn
    if final_status in ('Reddedildi', 'Geri Çekildi'):
        return {
            "status": "resolved",
            "days_remaining": None,
            "label_tr": "Tehdit kalkt\u0131",
            "urgency": "none"
        }

    # Already opposed by someone
    if final_status == 'İtiraz Edildi':
        return {
            "status": "opposed",
            "days_remaining": None,
            "label_tr": "\u0130tiraz edilmi\u015f",
            "urgency": "info"
        }

    # Fully registered — opposition not possible
    if final_status in ('Tescil Edildi', 'Yenilendi'):
        return {
            "status": "registered",
            "days_remaining": None,
            "label_tr": "Tescil edildi",
            "urgency": "low"
        }

    # Partial refusal — still partially active, treat like active
    # Transferred — ownership changed, still a threat
    # Expired — scanner typically excludes, but handle defensively
    if final_status == 'Süresi Doldu':
        return {
            "status": "expired",
            "days_remaining": None,
            "label_tr": "Marka s\u00fcresi doldu",
            "urgency": "none"
        }

    # Pre-publication — applied but not yet in bulletin
    if not bulletin_date or final_status == 'Başvuruldu':
        # Check if bulletin_date is actually present for Applied status
        if not bulletin_date:
            return {
                "status": "pre_publication",
                "days_remaining": None,
                "label_tr": "Erken Uyar\u0131 \u2014 Hen\u00fcz yay\u0131nlanmad\u0131",
                "urgency": "info"
            }

    # Has bulletin_date — check appeal deadline
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
                    "urgency": "none"
                }
            elif days_remaining <= 7:
                return {
                    "status": "active_critical",
                    "days_remaining": days_remaining,
                    "label_tr": f"\u0130tiraz s\u00fcresi: {days_remaining} g\u00fcn kald\u0131",
                    "urgency": "critical"
                }
            elif days_remaining <= 30:
                return {
                    "status": "active_urgent",
                    "days_remaining": days_remaining,
                    "label_tr": f"\u0130tiraz s\u00fcresi: {days_remaining} g\u00fcn kald\u0131",
                    "urgency": "urgent"
                }
            else:
                return {
                    "status": "active",
                    "days_remaining": days_remaining,
                    "label_tr": f"\u0130tiraz s\u00fcresi: {days_remaining} g\u00fcn kald\u0131",
                    "urgency": "normal"
                }

    # Fallback — published but no deadline computed (data gap)
    return {
        "status": "unknown",
        "days_remaining": None,
        "label_tr": "Durum belirsiz",
        "urgency": "none"
    }


def classify_deadline_status(final_status: str, bulletin_date, appeal_deadline) -> dict:
    """
    Compatibility redefinition that accepts both clean Turkish strings and
    legacy mojibake variants while preserving the existing UI contract.
    """
    today = date.today()
    final_status = final_status or ""

    resolved_statuses = {
        "Refused",
        "Withdrawn",
        "Reddedildi",
        "Geri \u00c7ekildi",
        "Geri Ã‡ekildi",
        "Geri Ãƒâ€¡ekildi",
    }
    opposed_statuses = {
        "Opposed",
        "\u0130tiraz Edildi",
        "Ä°tiraz Edildi",
        "Ã„Â°tiraz Edildi",
    }
    registered_statuses = {
        "Registered",
        "Renewed",
        "Tescil Edildi",
        "Yenilendi",
    }
    expired_statuses = {
        "Expired",
        "S\u00fcresi Doldu",
        "SÃ¼resi Doldu",
        "SÃƒÂ¼resi Doldu",
    }
    applied_statuses = {
        "Applied",
        "Ba\u015fvuruldu",
        "BaÅŸvuruldu",
        "BaÃ…Å¸vuruldu",
    }

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
                "label_tr": "Erken Uyar\u0131 \u2014 Hen\u00fcz yay\u0131nlanmad\u0131",
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
