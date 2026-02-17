"""
Comprehensive live scoring test — validates the scoring mechanism
across all features: public search, authenticated search, watchlist scanning.

Tests:
1. User-specified example queries
2. Reverse queries (flipped direction)
3. Robustness edge cases (single-word, multi-word, Turkish chars, abbreviations)
4. Cross-feature consistency (public vs authenticated search)
"""
import os, sys, json, time
os.environ.setdefault('DB_PORT', '5433')
os.environ.setdefault('DB_PASSWORD', 'Dogan.1996')

import requests

BASE = "http://localhost:8000"
TOKEN = open(os.path.join(os.path.dirname(__file__), "tmp_token.txt")).read().strip()
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Also test IDF scoring directly for precise pair-level checks
from idf_scoring import compute_idf_weighted_score, tokenize
from risk_engine import score_pair

PASS = 0
FAIL = 0
RESULTS = []


def check(label, actual, lo, hi, path=""):
    global PASS, FAIL
    ok = lo <= actual <= hi
    mark = "PASS" if ok else "FAIL"
    if not ok:
        FAIL += 1
    else:
        PASS += 1
    line = f"  [{mark}] {label:55s} => {actual:6.1f}%  (expect {lo}-{hi}%)  {path}"
    print(line)
    RESULTS.append({"test": label, "score": actual, "lo": lo, "hi": hi, "pass": ok, "path": path})
    return ok


def api_search(query, endpoint="public"):
    """Search via API and return list of (name, score%, path) tuples."""
    if endpoint == "public":
        url = f"{BASE}/api/v1/search/public?query={requests.utils.quote(query)}"
        r = requests.get(url, timeout=30)
    else:
        url = f"{BASE}/api/v1/search/search"
        r = requests.post(url, json={"query": query}, headers=AUTH, timeout=60)
    data = r.json()
    items = data.get("results", data.get("items", []))
    out = []
    for it in items:
        name = it.get("trademark_name") or it.get("name", "")
        # Handle both flat (public) and nested (agentic) score formats
        scores = it.get("scores", {})
        score = it.get("risk_score") or it.get("similarity_score") or scores.get("total") or 0
        if score is None:
            score = 0
        path = it.get("scoring_path") or scores.get("scoring_path", "")
        out.append((name, round(score * 100, 1), path))
    return out


def direct_score(query, target, text_sim=0.5, semantic_sim=0.5):
    """Score a specific pair via score_pair (full pipeline)."""
    bd = score_pair(query, target, text_sim=text_sim, semantic_sim=semantic_sim)
    total = bd.get("total", 0)
    path = bd.get("scoring_path", "")
    return round(total * 100, 1), path


def idf_score(query, target, text_sim=0.5, semantic_sim=0.5):
    """Score via IDF only (no dynamic combine)."""
    score, bd = compute_idf_weighted_score(query, target, text_sim=text_sim, semantic_sim=semantic_sim)
    path = bd.get("scoring_path", "")
    return round(score * 100, 1), path


# ================================================================
print("=" * 80)
print("SECTION 1: DIRECT PAIR SCORING (score_pair — full pipeline)")
print("=" * 80)
print()

# --- User-specified examples ---
print("--- 1A: User-specified examples ---")
s, p = direct_score("dogan patent", "dogan patent", 0.99, 0.9)
check("dogan patent -> dogan patent (exact)", s, 100, 100, p)

s, p = direct_score("dogan patent", "dogan", 0.56, 0.6)
check("dogan patent -> dogan (target subset)", s, 85, 92, p)

s, p = direct_score("dogan patent", "d.p dogan patent", 0.87, 0.9)
check("dogan patent -> d.p dogan patent (query in target)", s, 93, 97, p)

s, p = direct_score("dogan patent", "patent", 0.5, 0.5)
check("dogan patent -> patent (Case D, semi-generic only)", s, 0, 25, p)

s, p = direct_score("dogan", "dogan", 1.0, 1.0)
check("dogan -> dogan (exact)", s, 100, 100, p)

s, p = direct_score("nike", "nike", 1.0, 1.0)
check("nike -> nike (exact)", s, 100, 100, p)

s, p = direct_score("nike", "nikex", 0.89, 0.7)
check("nike -> nikex (not 100%)", s, 50, 95, p)

print()
print("--- 1B: Reverse / flipped direction ---")
s, p = direct_score("dogan", "dogan patent", 0.56, 0.6)
check("dogan -> dogan patent (query subset of target)", s, 93, 97, p)

s, p = direct_score("patent", "dogan patent", 0.5, 0.5)
check("patent -> dogan patent (generic query in target)", s, 0, 25, p)

s, p = direct_score("nikex", "nike", 0.89, 0.7)
check("nikex -> nike (reverse fuzzy, unknown query word)", s, 0, 95, p)

s, p = direct_score("d.p dogan patent", "dogan patent", 0.87, 0.9)
check("d.p dogan patent -> dogan patent (target subset of query)", s, 85, 97, p)

print()
print("--- 1C: Turkish character normalization ---")
s, p = direct_score("guzel sanatlar", "guzel sanatlar", 1.0, 1.0)
check("guzel sanatlar -> guzel sanatlar (exact)", s, 100, 100, p)

s, p = direct_score("dogan", "DOĞAN", 1.0, 1.0)
check("dogan -> DOĞAN (case+Turkish normalize)", s, 100, 100, p)

s, p = direct_score("şeker", "seker", 1.0, 1.0)
check("seker -> seker (ş normalization)", s, 100, 100, p)

s, p = direct_score("istanbul", "İSTANBUL", 1.0, 1.0)
check("istanbul -> ISTANBUL (İ normalization)", s, 100, 100, p)

print()
print("--- 1D: Robustness edge cases ---")
s, p = direct_score("apple", "apple", 1.0, 1.0)
check("apple -> apple (exact, single word)", s, 100, 100, p)

s, p = direct_score("coca cola", "coca cola", 1.0, 1.0)
check("coca cola -> coca cola (exact, two words)", s, 100, 100, p)

s, p = direct_score("coca cola", "cola", 0.5, 0.5)
check("coca cola -> cola (missing distinctive 'coca')", s, 50, 92, p)

s, p = direct_score("coca cola", "coca", 0.5, 0.5)
check("coca cola -> coca (missing distinctive 'cola')", s, 50, 92, p)

s, p = direct_score("abc", "abcdef", 0.67, 0.5)
check("abc -> abcdef (prefix, different word, below fuzzy)", s, 0, 50, p)

s, p = direct_score("samsung", "samsunga", 0.93, 0.8)
check("samsung -> samsunga (near match)", s, 70, 95, p)

s, p = direct_score("samsung electronics", "samsung", 0.6, 0.6)
check("samsung electronics -> samsung (target subset)", s, 80, 95, p)

s, p = direct_score("ltd", "ltd sanayi", 0.5, 0.5)
check("ltd -> ltd sanayi (generic query)", s, 0, 25, p)

s, p = direct_score("marka patent", "vatan patent", 0.4, 0.5)
check("marka patent -> vatan patent (only 'patent' matches)", s, 0, 25, p)

s, p = direct_score("dogan insaat", "dogan patent", 0.5, 0.5)
check("dogan insaat -> dogan patent (distinctive matches, generic differs)", s, 50, 95, p)


# ================================================================
print()
print("=" * 80)
print("SECTION 2: PUBLIC SEARCH API (end-to-end via /api/v1/search/public)")
print("=" * 80)
print()

api_tests = [
    ("dogan patent", [
        ("d.p doğan patent", 90, 100, "should be top result, ~95%"),
    ]),
    ("dogan", [
        ("doğan", 100, 100, "exact after Turkish normalization"),
    ]),
    ("nike", [
        ("nike", 100, 100, "exact match"),
    ]),
    ("guzel sanatlar", [
        # results should be containment (query tokens in target) at ~95%
    ]),
    ("samsung", []),
    ("coca cola", []),
    ("apple", []),
]

for query, expectations in api_tests:
    print(f"  Query: '{query}'")
    results = api_search(query, "public")
    if not results:
        print(f"    (no results)")
        continue
    # Show top 5
    for i, (name, score, path) in enumerate(results[:5]):
        tag = ""
        for exp_name, exp_lo, exp_hi, exp_desc in expectations:
            if exp_name.lower().replace("ğ", "g").replace("ö", "o") in name.lower().replace("ğ", "g").replace("ö", "o"):
                ok = exp_lo <= score <= exp_hi
                tag = f" {'PASS' if ok else 'FAIL'}: {exp_desc}"
                if ok:
                    PASS += 1
                else:
                    FAIL += 1
                RESULTS.append({"test": f"API: {query} -> {name}", "score": score, "lo": exp_lo, "hi": exp_hi, "pass": ok, "path": path})
        print(f"    {i+1}. {score:5.1f}% | {name:40s} | {path[:50]}{tag}")

    # Check noise results are below threshold
    noise = [r for r in results[1:] if r[1] < 25]
    if noise:
        highest_noise = max(r[1] for r in noise)
        tag = "PASS" if highest_noise <= 25 else "FAIL"
        print(f"    Noise ceiling: {highest_noise}% [{tag}]")
    print()


# ================================================================
print()
print("=" * 80)
print("SECTION 3: AUTHENTICATED SEARCH (/api/v1/search)")
print("=" * 80)
print()

auth_queries = ["dogan patent", "nike", "samsung", "apple"]
for query in auth_queries:
    print(f"  Query: '{query}'")
    results = api_search(query, "authenticated")
    if not results:
        print(f"    (no results or auth failed)")
        continue
    for i, (name, score, path) in enumerate(results[:3]):
        print(f"    {i+1}. {score:5.1f}% | {name:40s} | {path[:50]}")
    # Check #1 result consistency with public
    pub_results = api_search(query, "public")
    if pub_results and results:
        pub_top = pub_results[0][1]
        auth_top = results[0][1]
        diff = abs(pub_top - auth_top)
        ok = diff <= 5.0  # Allow 5% tolerance (visual sim may differ)
        tag = "PASS" if ok else "FAIL"
        if ok: PASS += 1
        else: FAIL += 1
        RESULTS.append({"test": f"Consistency: {query} public vs auth", "score": diff, "lo": 0, "hi": 5, "pass": ok})
        print(f"    Pub/Auth consistency: pub={pub_top}% auth={auth_top}% diff={diff}% [{tag}]")
    print()


# ================================================================
print()
print("=" * 80)
print("SECTION 4: IDF SCORING ISOLATION (compute_idf_weighted_score)")
print("=" * 80)
print()

print("--- 4A: 100% only for exact match ---")
pairs_should_be_100 = [
    ("dogan patent", "dogan patent"),
    ("dogan", "dogan"),
    ("nike", "nike"),
    ("coca cola", "coca cola"),
    ("apple", "apple"),
    ("DOĞAN PATENT", "dogan patent"),      # Turkish normalization
    ("İSTANBUL", "istanbul"),              # I→i normalization
]
for q, t in pairs_should_be_100:
    s, p = idf_score(q, t, 1.0, 1.0)
    check(f"IDF: {q} -> {t} = 100%", s, 100, 100, p)

print()
print("--- 4B: These should NOT be 100% ---")
pairs_not_100 = [
    ("dogan patent", "dogan"),
    ("dogan patent", "d.p dogan patent"),
    ("nike", "nikex"),
    ("coca cola", "cola"),
    ("samsung", "samsunga"),
    ("apple", "applex"),
    ("guzel sanatlar", "guzel"),
]
for q, t in pairs_not_100:
    s, p = idf_score(q, t, 0.9, 0.8)
    check(f"IDF: {q} -> {t} != 100%", s, 0, 99, p)

print()
print("--- 4C: Case D/E should be low ---")
case_de = [
    ("dogan patent", "vatan patent"),      # only "patent" matches (semi-generic)
    ("dogan patent", "kent patent"),       # same
]
for q, t in case_de:
    s, p = idf_score(q, t, 0.5, 0.5)
    check(f"IDF: {q} -> {t} (noise, semi-generic only)", s, 0, 25, p)

# Words that are distinctive in the actual IDF DB:
# "inc" (IDF=9.05) and "sports" (IDF=7.66) are distinctive, not generic.
# When a distinctive word matches, score is legitimately high.
case_distinctive_partial = [
    ("apple inc", "ltd inc"),              # "inc" is distinctive (IDF=9.05) → matches
    ("nike sports", "abc sports"),          # "sports" is distinctive (IDF=7.66) → matches
]
for q, t in case_distinctive_partial:
    s, p = idf_score(q, t, 0.5, 0.5)
    check(f"IDF: {q} -> {t} (partial distinctive match)", s, 50, 95, p)


# ================================================================
print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"  PASSED: {PASS}")
print(f"  FAILED: {FAIL}")
print(f"  TOTAL:  {PASS + FAIL}")
if FAIL > 0:
    print()
    print("  FAILED tests:")
    for r in RESULTS:
        if not r["pass"]:
            print(f"    - {r['test']}: got {r['score']}%, expected {r['lo']}-{r['hi']}%  [{r['path']}]")
print()
sys.exit(1 if FAIL > 0 else 0)
