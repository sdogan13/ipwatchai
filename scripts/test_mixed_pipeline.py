"""Test mixed-language name handling through batch_translate_to_turkish."""
import sys
sys.path.insert(0, '/app')

from utils.translation import batch_translate_to_turkish, detect_language_fasttext, turkish_lower

# Mixed language examples (Turkish + English/other)
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
    # Pure Turkish
    "ŞEKER",
    "DOĞAN",
    "kırmızı elma",
    # Pure English
    "APPLE",
    "GOLDEN STAR",
    "bigfoot",
    # Other languages
    "La belle vie",
    "Яблоко",
    "Äpfel",
]

print("=" * 90)
print("1. FastText Detection")
print("=" * 90)
print(f"{'Name':<40s} | {'Lang':>5s} | {'Conf':>6s}")
print("-" * 60)
for name in mixed:
    iso, _, conf = detect_language_fasttext(name)
    print(f"{name:<40s} | {iso:>5s} | {conf:>6.3f}")

print()
print("=" * 90)
print("2. batch_translate_to_turkish (full pipeline)")
print("=" * 90)

results = batch_translate_to_turkish(mixed)

print(f"{'Name':<40s} | {'name_tr':<35s} | {'Lang':>5s} | Changed?")
print("-" * 90)
for name, (name_tr, lang) in zip(mixed, results):
    changed = turkish_lower(name_tr) != turkish_lower(name)
    mark = "TRANSLATED" if changed else "kept"
    print(f"{name:<40s} | {name_tr:<35s} | {lang:>5s} | {mark}")
