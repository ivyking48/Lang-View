import pytest

from lang_view.text_filter import (
    classify,
    has_cjk_ideograph,
    has_hangul,
    has_kana,
)


class TestHasHangul:
    @pytest.mark.parametrize("text", ["안녕", "한국어", "hi 안녕 there", "ᄀᄁᄂ"])
    def test_detects_hangul(self, text):
        assert has_hangul(text)

    @pytest.mark.parametrize("text", ["", "hello", "こんにちは", "漢字", "12345"])
    def test_rejects_non_hangul(self, text):
        assert not has_hangul(text)


class TestHasKana:
    @pytest.mark.parametrize("text", ["こんにちは", "コーヒー", "ひらがな カタカナ"])
    def test_detects_kana(self, text):
        assert has_kana(text)

    @pytest.mark.parametrize("text", ["", "hello", "안녕", "漢字 only", "123"])
    def test_rejects_non_kana(self, text):
        assert not has_kana(text)


class TestHasCjkIdeograph:
    @pytest.mark.parametrize("text", ["漢字", "中文", "一二三"])
    def test_detects_ideographs(self, text):
        assert has_cjk_ideograph(text)

    @pytest.mark.parametrize("text", ["", "hello", "안녕", "ひらがな"])
    def test_rejects_non_ideographs(self, text):
        assert not has_cjk_ideograph(text)


class TestClassify:
    def test_hangul_wins_over_hint(self):
        assert classify("안녕", hint="ja") == "ko"

    def test_kana_wins_over_hint(self):
        assert classify("こんにちは", hint="ko") == "ja"

    def test_mixed_hangul_and_kana_picks_hangul(self):
        # Hangul is checked first; mixed scripts on screen are unusual
        # but we want a deterministic answer.
        assert classify("안녕 こんにちは", hint="ja") == "ko"

    def test_bare_ideographs_use_hint(self):
        assert classify("漢字", hint="ja") == "ja"
        assert classify("漢字", hint="ko") == "ko"

    def test_latin_only_returns_none(self):
        assert classify("hello world", hint="ja") is None

    def test_empty_returns_none(self):
        assert classify("", hint="ko") is None

    def test_punctuation_only_returns_none(self):
        assert classify("!?... 123", hint="ja") is None
