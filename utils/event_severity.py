"""Single source of truth for trademark event-type to alert severity mapping.

Used by:
- watchlist/scanner.py to set severity at alert insert time
- services/holder_service.py and services/attorney_service.py to attach
  last_event_severity to portfolio responses (drives card color tint)
- services/alert_service.py via classify_event_severity()
"""

EVENT_SEVERITY_MAP = {
    "cancellation": "critical",
    "seizure": "critical",
    "precautionary_seizure": "critical",
    "bankruptcy": "critical",
    "injunction": "high",
    "precautionary_injunction": "high",
    "transfer": "high",
    "merger": "high",
    "partial_transfer": "high",
    "withdrawal": "high",
    "renewal": "medium",
    "license": "medium",
    "seizure_lift": "low",
    "injunction_lift": "low",
    "restriction_lift": "low",
    "correction": "low",
    "address_change": "low",
    "name_change": "low",
}

DEFAULT_EVENT_SEVERITY = "medium"


def classify_event_severity(event_type):
    """Return the alert severity tier for a given event_type.

    Returns one of: 'critical', 'high', 'medium', 'low', or None for empty input.
    Unknown event types fall back to DEFAULT_EVENT_SEVERITY.
    """
    if not event_type:
        return None
    return EVENT_SEVERITY_MAP.get(str(event_type).lower(), DEFAULT_EVENT_SEVERITY)
