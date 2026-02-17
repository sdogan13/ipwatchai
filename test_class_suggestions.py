"""Test class suggestion quality after re-embedding."""
import requests

tests = [
    # Turkish queries (primary use case)
    ("yazilim gelistirme ve mobil uygulama", "TR", 42),
    ("bilgisayar programlama", "TR", 42),
    ("web sitesi tasarimi", "TR", 42),
    ("giyim ve moda", "TR", 25),
    ("ayakkabi ve terlik", "TR", 25),
    ("restoran ve lokanta", "TR", 43),
    ("otel konaklama", "TR", 43),
    ("ilac ve eczacilik", "TR", 5),
    ("dis macunu ve kozmetik", "TR", 3),
    ("sigorta ve bankacilik", "TR", 36),
    ("insaat ve yapi malzemeleri", "TR", [19, 37]),
    ("avukatlik ve hukuk", "TR", 45),
    ("egitim ve ogretim", "TR", 41),
    ("reklamcilik ve pazarlama", "TR", 35),
    ("et ve sut urunleri", "TR", 29),
    ("cay kahve cikolata", "TR", 30),
    ("bira ve alkolsuz icecekler", "TR", 32),
    ("sarap ve raki", "TR", 33),
    ("oyuncak ve spor malzemeleri", "TR", 28),
    ("kargo ve nakliye", "TR", 39),
    ("mobilya ve dekorasyon", "TR", 20),
    ("mucevherat ve saat", "TR", 14),
    ("elektronik ve bilgisayar", "TR", 9),
    ("hastane ve saglik", "TR", 44),
    # English queries
    ("software development", "EN", 42),
    ("clothing and fashion", "EN", 25),
    ("restaurant and hotel", "EN", 43),
    ("pharmaceutical drugs", "EN", 5),
    ("insurance and banking", "EN", 36),
    ("toys and games", "EN", 28),
]

correct = 0
total = len(tests)

for desc, lang, expected in tests:
    r = requests.post(
        "http://localhost:8000/api/suggest-classes",
        json={"description": desc, "top_k": 5},
    )
    d = r.json()
    top = d["suggestions"][0] if d["suggestions"] else None
    top_cn = top["class_number"] if top else -1
    top_sim = top["similarity"] * 100 if top else 0

    if isinstance(expected, list):
        hit = top_cn in expected
    else:
        hit = top_cn == expected

    mark = "OK" if hit else "MISS"
    if hit:
        correct += 1

    top5 = ", ".join(
        f'{s["class_number"]}({s["similarity"]*100:.0f}%)' for s in d["suggestions"]
    )
    exp_str = str(expected)
    print(f"[{mark:4s}] [{lang}] {desc:<40s} -> #{top_cn}({top_sim:.0f}%) expect={exp_str:<6s} | {top5}")

print(f"\nAccuracy: {correct}/{total} ({correct/total*100:.0f}%)")
