"""Test mixed-language name handling."""
import sys
sys.path.insert(0, '/app')

from utils.translation import (
    detect_language_fasttext, translate, turkish_lower,
    batch_translate_to_turkish, _load_fasttext_langid
)

# Mixed language examples (Turkish chars + English/other words)
mixed = [
    "DOĞAN electronics",
    "şişçi burger house",
    "İstanbul fashion week",
    "güneş energy systems",
    "öztürk international trading",
    "çelik steel works",
    "KARDEŞLER auto parts",
    "yıldız star hotel",
    "paşa coffee roasters",
    "gümüş silver jewelry",
]

print("=== 1. Current detection (Turkish char check in Step 1) ===")
for name in mixed:
    iso, nllb, conf = detect_language_fasttext(name)
    print(f"  {name:45s} -> lang={iso:5s} conf={conf:.3f}")

print()
print("=== 2. What FastText alone thinks (bypassing char check) ===")
model = _load_fasttext_langid()
if model:
    from utils.translation import _NLLB_TO_ISO
    for name in mixed:
        clean = name.replace('\n', ' ').strip()
        labels, scores = model.predict(clean)
        nllb_code = labels[0].replace('__label__', '')
        conf = float(scores[0])
        iso = _NLLB_TO_ISO.get(nllb_code, 'en')
        print(f"  {name:45s} -> lang={iso:5s} ({nllb_code:15s}) conf={conf:.3f}")

print()
print("=== 3. What NLLB produces if we translate from English ===")
for name in mixed:
    result = translate(name, 'en', 'tr')
    r = result if result else "(None - echo)"
    print(f"  {name:45s} -> {r}")

print()
print("=== 4. Current pipeline output (batch_translate_to_turkish) ===")
results = batch_translate_to_turkish(mixed)
for name, (name_tr, lang) in zip(mixed, results):
    changed = turkish_lower(name_tr) != turkish_lower(name)
    mark = "TRANSLATED" if changed else "kept"
    print(f"  {name:45s} -> {name_tr:40s} lang={lang:5s} {mark}")
