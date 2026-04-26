import asyncio
from ui_scrape_collection import (
    _BLT_INPUT_PATTERNS,
    _GZ_INPUT_PATTERNS,
    _input_matches_patterns,
    _normalize_ui_text,
    _try_dom_click,
)


def test_normalize_ui_text_folds_turkish_characters():
    assert _normalize_ui_text("Marka İlan Bülten No") == "marka ilan bulten no"
    assert _normalize_ui_text("Tescil Yayın Bülten No") == "tescil yayin bulten no"


def test_input_matches_patterns_recognizes_mui_blt_placeholder():
    assert _input_matches_patterns(
        placeholder="Marka İlan Bülten No",
        aria_label="",
        name="",
        patterns=_BLT_INPUT_PATTERNS,
    )


def test_input_matches_patterns_recognizes_mui_gz_placeholder():
    assert _input_matches_patterns(
        placeholder="Tescil Yayın Bülten No",
        aria_label="",
        name="",
        patterns=_GZ_INPUT_PATTERNS,
    )


class _FakeDomClickTarget:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.clicked = False

    async def evaluate(self, script: str):
        if self.should_fail:
            raise RuntimeError("boom")
        self.clicked = True
        return None


class _FakeLocator:
    def __init__(self, target: _FakeDomClickTarget):
        self.first = target


def test_try_dom_click_uses_dom_evaluate_fallback():
    target = _FakeDomClickTarget()

    assert asyncio.run(_try_dom_click(_FakeLocator(target))) is True
    assert target.clicked is True


def test_try_dom_click_returns_false_on_failure():
    target = _FakeDomClickTarget(should_fail=True)

    assert asyncio.run(_try_dom_click(_FakeLocator(target))) is False
