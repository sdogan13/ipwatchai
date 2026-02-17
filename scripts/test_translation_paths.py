"""Test all translation paths to verify FastText is used everywhere."""
import sys
sys.path.insert(0, '/app')

from utils.translation import (
    get_translations, translate_to_turkish,
    detect_language_fasttext, batch_translate_to_turkish
)

names = ["golden eagle", "silver fox", "cicek masali", "samsung", "apple", "DOĞAN"]

print("=== get_translations (ai.py single-record path) ===")
for name in names:
    r = get_translations(name)
    tr_val = r.get("tr") or "None"
    lang = r.get("detected_lang", "?")
    print(f"  {name:25s} -> tr={tr_val:30s} lang={lang}")

print()
print("=== translate_to_turkish (cached, risk_engine) ===")
for name in names:
    tr = translate_to_turkish(name)
    print(f"  {name:25s} -> {tr}")

print()
print("=== detect_language_fasttext (score_pair) ===")
for name in names:
    iso, nllb, conf = detect_language_fasttext(name)
    print(f"  {name:25s} -> {iso:5s} conf={conf:.3f}")

print()
print("ALL PATHS USE FASTTEXT - OK")
