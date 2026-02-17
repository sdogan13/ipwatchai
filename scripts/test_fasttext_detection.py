"""Test FastText-only language detection on various inputs."""
import sys
sys.path.insert(0, '/app')

from utils.translation import detect_language_fasttext

tests = [
    'DOĞAN electronics',
    'ŞEKER',
    'APPLE',
    'samsung',
    'DOĞAN',
    'İstanbul fashion week',
    'cicek masali',
    'bigfoot',
    'La belle vie',
    'GOLDEN STAR',
    'ELMA',
    'güneş energy systems',
    'çelik steel works',
    'KARDEŞLER auto parts',
    'yıldız star hotel',
    'paşa coffee roasters',
    'gümüş silver jewelry',
    'Äpfel',
    'Яблоко',
    'تفاح',
    'پنیر',
    '苹果',
]

print(f"{'Name':<40s} | {'Lang':>5s} | {'Conf':>6s}")
print('-' * 60)
for name in tests:
    iso, nllb, conf = detect_language_fasttext(name)
    print(f"{name:<40s} | {iso:>5s} | {conf:>6.3f}")
