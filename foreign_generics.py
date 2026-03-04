"""
foreign_generics.py - Standalone constants, no circular imports.

English and Turkish industry terms that are conceptually generic but
statistically rare in the Turkish trademark DB.  Overriding their IDF
to 2.0 (Generic) prevents the risk engine from treating common words
like 'jewelry', 'group', or 'mücevherleri' as highly distinctive.
"""

FOREIGN_GENERICS_OVERRIDE = frozenset({
    # ── English: Business structures ─────────────────────────────────────
    "group", "global", "international", "holding", "corp", "corporation",
    "ltd", "limited", "company", "inc", "co", "partners", "agency", "academy",
    "institute", "center", "centre", "club", "association", "foundation", "society",

    # ── English: Industry sectors ─────────────────────────────────────────
    "tech", "technology", "technologies", "software", "digital", "cyber", "it",
    "logistics", "transport", "cargo", "express", "shipping", "delivery",
    "health", "healthcare", "medical", "clinic", "hospital", "pharma",
    "finance", "capital", "investment", "investments", "wealth", "bank", "credit",
    "food", "foods", "beverage", "drinks", "catering",
    "energy", "power", "electric", "gas", "oil",
    "construction", "building", "architecture", "engineering", "builders",
    "media", "production", "studios", "entertainment", "music", "art",
    "education", "school", "university", "college", "training",
    "consulting", "consultancy", "advisory", "management",
    "real", "estate", "property", "realty", "properties",
    "travel", "tourism", "tours", "holidays", "vacation",
    "fashion", "style", "wear", "apparel", "clothing", "garment",

    # ── English: Products & materials ────────────────────────────────────
    "jewelry", "jewellery", "jewelery", "jewellry", "jewel", "jewels", "gold", "silver", "diamond", "diamonds",
    "metal", "metals", "steel", "iron", "aluminium",
    "plastic", "plastics", "polymer", "chemical", "chemicals",
    "textile", "textiles", "fabric", "cotton", "silk",
    "footwear", "bags", "leather",
    "parts", "auto", "motor", "motors", "automotive", "car", "cars", "vehicles",
    "home", "house", "furniture", "decor", "design", "living",
    "beauty", "cosmetics", "makeup", "hair", "skin", "care", "spa",

    # ── English: Commerce ─────────────────────────────────────────────────
    "shop", "store", "market", "mart", "boutique", "concept", "studio",
    "online", "retail", "wholesale", "trade", "trading",
    "brand", "brands", "label", "collection", "collections",
    "plus", "pro", "max", "premium", "luxury", "exclusive", "elite", "vip",
    "new", "modern", "classic", "original", "authentic", "vintage",
    "world", "planet", "earth", "universe", "galaxy",
    "star", "sun", "moon", "sky", "ocean", "sea", "water", "nature", "green",
    "and", "or", "of", "the", "for", "to", "in", "on", "at", "by", "with",

    # ── Turkish translations (normalized, no diacritics) ─────────────────
    # Jewelry: mucevher(9.87), mucevherleri(13.37), kuyum(11.17)
    "mucevher", "mucevherleri", "mucevherat", "kuyum", "kuyumculuk",
    # Metals: gumus(7.86)
    "gumus", "platin", "bronz",
    # Comms / Media: iletisim(7.69)
    "iletisim",
    # Industry & business
    "sanayi", "endustri", "ithalat", "ihracat", "uretim", "imalat",
    "hizmet", "hizmetleri", "danismanlik", "yonetim", "yatirim",
    "gayrimenkul", "emlak", "konut", "insaat", "yapi",
    "saglik", "klinik", "hastane", "eczane",
    "egitim", "okul", "universite", "akademi",
    "turizm", "seyahat", "tatil", "otel",
    "lojistik", "nakliye", "kargo", "tasimaci",
    "teknoloji", "yazilim", "dijital", "bilisim",
    "gida", "icecek",
    "enerji", "elektrik",
    "tekstil", "kumas", "giyim", "hazir",
    "guzellik", "kozmetik", "bakim",
    "mobilya", "dekorasyon",
    "medya", "yayin", "eglence",
    "finans", "sermaye", "kredi",
    # ── structural legal & administrative ─────────────────────────────────
    "patent", "patents", "patenti", "patents", "paten", "trademarks",
    "ofis", "ofisi", "ticaret", "sirket", "sirketi",
    "grup", "grubu", "merkez", "merkezi"
})
