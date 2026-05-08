"""Sanity audit of TS_483 events.json."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

m = json.load(open("bulletins/Tasarim/TS_483_2026-04-24/events.json", encoding="utf-8"))
events = m["events"]
print(f"event_count: {len(events)}")
print(f"bulletin_no: {m['bulletin_no']}, bulletin_date: {m['bulletin_date']}")
print()

# Spot-check one of each major type
def show(t):
    e = next((e for e in events if e["event_type"] == t), None)
    if not e:
        print(f"--- {t}: NONE FOUND ---")
        return
    print(f"--- sample {t} ---")
    for k, v in e.items():
        if k in ("free_text", "fingerprint"):
            v = (str(v)[:80] + "...") if v else None
        print(f"  {k}: {v}")
    print()

for t in ("transfer", "seizure", "provisional_seizure", "renewal", "partial_renewal",
          "partial_cancellation_owner", "full_cancellation_board",
          "partial_cancellation_board", "full_cancellation_applicant"):
    show(t)

# Check all events have required fields
missing_regno = sum(1 for e in events if not e.get("registration_no"))
missing_date = sum(1 for e in events if not e.get("event_date"))
missing_holder = sum(1 for e in events if not e.get("holder"))
missing_fp = sum(1 for e in events if not e.get("fingerprint"))
print("=== integrity ===")
print(f"  missing registration_no: {missing_regno}")
print(f"  missing event_date:      {missing_date}")
print(f"  missing holder:          {missing_holder}")
print(f"  missing fingerprint:     {missing_fp}")
print(f"  duplicate fingerprints:  {len(events) - len({e['fingerprint'] for e in events})}")
