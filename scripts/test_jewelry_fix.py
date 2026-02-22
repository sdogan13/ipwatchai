"""
Quick live API test for the FOREIGN_GENERICS_OVERRIDE fix.
Run inside the container: python3 scripts/test_jewelry_fix.py
"""
import requests
import json

BASE = "http://localhost:8000"

# First, get a token (use the internal health check to confirm API is up)
resp = requests.get(f"{BASE}/health", timeout=10)
print(f"Health: {resp.status_code} {resp.text[:80]}")

# Now test the IDF scoring directly (no auth needed for internal test)
import sys
sys.path.insert(0, "/app")

import utils.idf_scoring as s
s.initialize_idf_scoring_sync()

from idf_scoring import compute_idf_weighted_score

test_cases = [
    ("landra jewelry", "alexandra gold jewelry"),
    ("global tech", "global technology group"),
    ("fashion house", "fashion house paris"),
    ("dogan holding", "dogan holdings international"),
    ("nike", "nikea sports"),   # Distinctive brand — should stay high
]

print("\n" + "="*65)
print(f"{'Query':<25} {'Target':<30} {'Score':>6}")
print("="*65)

for query, target in test_cases:
    score, brk = compute_idf_weighted_score(query, target)
    words = [(m['query_word'], m['word_class']) for m in brk.get('matched_words', [])]
    print(f"{query:<25} {target:<30} {score*100:>5.1f}%")
    for word, cls in words:
        marker = " [GENERIC]" if cls == "generic" else ""
        print(f"   word: {word} ({cls}){marker}")

print("="*65)
print("\nIDF overrides active for key words:")
for word in ["jewelry", "gold", "group", "global", "technology", "fashion", "holding", "international"]:
    entry = s._word_data.get(word, {})
    idf = entry.get("idf", "not in db")
    cls = entry.get("word_class", "not in db")
    print(f"  {word:<15} idf={idf}  class={cls}")
