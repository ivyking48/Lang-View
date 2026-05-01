import pytest

from lang_view.enrich.translation import (
    NoopTranslator,
    TranslationEnricher,
    build_translator,
)


class FakeTranslator:
    name = "fake"

    def __init__(self, output="hello"):
        self.output = output
        self.calls = []

    def translate(self, text, source_lang, target_lang):
        self.calls.append((text, source_lang, target_lang))
        return self.output


def test_build_translator_noop_default():
    t = build_translator("none")
    assert isinstance(t, NoopTranslator)
    assert t.translate("x", "ja", "en") == ""


def test_build_translator_empty_string_is_noop():
    assert isinstance(build_translator(""), NoopTranslator)


def test_build_translator_unknown_raises():
    with pytest.raises(ValueError):
        build_translator("not-a-backend")


def test_deepl_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    # We can't import deepl here, so we expect either a ValueError (no key)
    # or a ModuleNotFoundError (deepl not installed). Both are acceptable
    # — the important contract is that the call fails fast.
    with pytest.raises((ValueError, ModuleNotFoundError)):
        build_translator("deepl")


def test_openai_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises((ValueError, ModuleNotFoundError)):
        build_translator("openai")


def test_translation_enricher_skips_for_noop():
    e = TranslationEnricher(NoopTranslator(), target_lang="en")
    assert not e.applies_to("ja")
    assert not e.applies_to("ko")


def test_translation_enricher_skips_when_target_matches_source():
    e = TranslationEnricher(FakeTranslator(), target_lang="ja")
    assert not e.applies_to("ja")
    assert e.applies_to("ko")


def test_translation_enricher_calls_translator():
    fake = FakeTranslator(output="今日は")
    e = TranslationEnricher(fake, target_lang="ja")
    out = e.enrich("Today", "ko")
    assert out == {"translation": {"ja": "今日は"}}
    assert fake.calls == [("Today", "ko", "ja")]


def test_translation_enricher_drops_empty_results():
    e = TranslationEnricher(FakeTranslator(output=""), target_lang="en")
    assert e.enrich("today", "ja") == {}
