import asyncio

from app_nice_class_routes import get_class_name, get_nice_classes


MOJIBAKE_SENTINELS = ("\u00c3", "\u00c4", "\u00c5")


def test_turkish_nice_class_names_are_utf8_clean():
    assert get_class_name(4, "tr") == "Yağlar ve Yakıtlar"
    assert get_class_name(5, "tr") == "Eczacılık Ürünleri"
    assert get_class_name(10, "tr") == "Tıbbi Cihazlar"
    assert get_class_name(99, "tr") == "Global Marka (Tüm Sınıflar)"


def test_nice_classes_endpoint_payload_has_clean_turkish_labels():
    payload = asyncio.run(get_nice_classes("tr"))
    class_names = {item["number"]: item["name"] for item in payload["classes"]}
    special_names = {item["number"]: item["name"] for item in payload["special_classes"]}

    assert class_names[4] == "Yağlar ve Yakıtlar"
    assert class_names[11] == "Aydınlatma ve Isıtma"
    assert special_names[99] == "Global Marka (Tüm Sınıflar)"

    rendered = " ".join(class_names.values()) + " " + " ".join(special_names.values())
    for sentinel in MOJIBAKE_SENTINELS:
        assert sentinel not in rendered
