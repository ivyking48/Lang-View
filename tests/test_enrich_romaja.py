import pytest

pytest.importorskip("hangul_romanize")

from lang_view.enrich.romaja import RomajaEnricher


@pytest.fixture(scope="module")
def enricher():
    return RomajaEnricher()


def test_applies_only_to_korean(enricher):
    assert enricher.applies_to("ko")
    assert not enricher.applies_to("ja")


def test_basic_romanization(enricher):
    out = enricher.enrich("안녕", "ko")
    assert "romaja" in out
    assert "annyeong" in out["romaja"]


def test_strips_whitespace(enricher):
    out = enricher.enrich("  한국어  ", "ko")
    # Result is stripped, but the inner text may still contain spaces.
    assert out["romaja"] == out["romaja"].strip()
    assert out["romaja"]
