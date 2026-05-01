import pytest

pykakasi = pytest.importorskip("pykakasi")

from lang_view.enrich.kana import KanaEnricher


@pytest.fixture(scope="module")
def enricher():
    return KanaEnricher()


def test_applies_only_to_japanese(enricher):
    assert enricher.applies_to("ja")
    assert not enricher.applies_to("ko")


def test_furigana_is_hiragana(enricher):
    out = enricher.enrich("今日", "ja")
    assert out["furigana"] == "きょう"


def test_romaji_uses_hepburn(enricher):
    out = enricher.enrich("天気", "ja")
    assert "tenki" in out["romaji"]


def test_can_disable_furigana():
    e = KanaEnricher(include_furigana=False, include_romaji=True)
    out = e.enrich("天気", "ja")
    assert "furigana" not in out
    assert "romaji" in out


def test_can_disable_romaji():
    e = KanaEnricher(include_furigana=True, include_romaji=False)
    out = e.enrich("天気", "ja")
    assert "romaji" not in out
    assert "furigana" in out


def test_pure_kana_passes_through(enricher):
    out = enricher.enrich("ひらがな", "ja")
    assert out["furigana"] == "ひらがな"
