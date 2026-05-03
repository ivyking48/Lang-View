import json

import pytest

from lang_view.overlay import (
    OverlayDriver,
    bootstrap_js,
    build_state,
    parse_action,
    pop_action_js,
    teardown_js,
    tokenize_japanese,
    tokenize_korean,
    update_js,
)


def test_bootstrap_js_is_idempotent_self_check():
    js = bootstrap_js()
    # The guard at the top ensures double-injection is safe.
    assert "if (window.__langView" in js
    assert "lang-view-overlay" in js
    # The IIFE wrapping prevents leaking helpers into the page.
    assert js.startswith("(function(){")
    assert js.rstrip().endswith("})();")


def test_bootstrap_js_exposes_required_methods():
    js = bootstrap_js()
    assert "window.__langView = {" in js
    assert "update:" in js and "popAction:" in js and "teardown:" in js


def test_teardown_js_calls_teardown_safely():
    js = teardown_js()
    assert "teardown" in js
    # Must short-circuit if __langView was never installed.
    assert "&&" in js


def test_update_js_embeds_state_as_json_literal():
    js = update_js({"text": "안녕", "words": [], "paused": False})
    assert "window.__langView.update(" in js
    # JSON is a JS subset; the literal should appear verbatim.
    assert '"text": "안녕"' in js or '"text":"안녕"' in js
    assert "false" in js


def test_update_js_preserves_unicode_without_escapes():
    js = update_js({"text": "ありがとう"})
    assert "ありがとう" in js  # ensure_ascii=False


def test_pop_action_js_returns_string():
    js = pop_action_js()
    assert "JSON.stringify" in js
    assert "popAction" in js
    assert "|| null" in js


def test_parse_action_handles_null_string():
    assert parse_action("null") is None


def test_parse_action_handles_empty_input():
    assert parse_action("") is None
    assert parse_action(None) is None


def test_parse_action_returns_dict_with_type():
    a = parse_action('{"type":"save"}')
    assert a == {"type": "save"}


def test_parse_action_rejects_action_without_type():
    assert parse_action('{"word":"x"}') is None


def test_parse_action_rejects_non_object():
    assert parse_action('"hello"') is None
    assert parse_action("[1,2]") is None


def test_parse_action_rejects_invalid_json():
    assert parse_action("{not json") is None


class _FakeKakasi:
    """Stand-in for pykakasi.kakasi() returning a fixed segmentation."""

    def __init__(self, segments):
        self._segments = segments

    def convert(self, text):
        return self._segments


def test_tokenize_japanese_drops_blank_segments():
    kks = _FakeKakasi([
        {"orig": "今日", "hira": "きょう", "hepburn": "kyou"},
        {"orig": " ", "hira": " ", "hepburn": ""},
        {"orig": "は", "hira": "は", "hepburn": "wa"},
    ])
    out = tokenize_japanese("今日は", kks)
    assert [t["orig"] for t in out] == ["今日", "は"]
    assert out[0]["reading"] == "きょう"
    assert out[0]["romaji"] == "kyou"


def test_tokenize_japanese_no_kakasi_returns_single_token():
    out = tokenize_japanese("hello", None)
    assert out == [{"orig": "hello", "reading": "", "romaji": ""}]


def test_tokenize_japanese_empty_text_returns_empty():
    assert tokenize_japanese("", None) == []
    assert tokenize_japanese("", _FakeKakasi([])) == []


class _FakeTransliter:
    def translit(self, word):
        return word.upper()  # cheap deterministic stand-in


def test_tokenize_korean_per_eojeol():
    out = tokenize_korean("안녕 세계", _FakeTransliter())
    assert [t["orig"] for t in out] == ["안녕", "세계"]
    assert [t["romaji"] for t in out] == ["안녕".upper(), "세계".upper()]


def test_tokenize_korean_without_transliter_blank_romaja():
    out = tokenize_korean("안녕", None)
    assert out == [{"orig": "안녕", "reading": "", "romaji": ""}]


def test_tokenize_korean_swallows_transliter_errors():
    class Bad:
        def translit(self, w):
            raise RuntimeError("boom")

    out = tokenize_korean("안녕", Bad())
    assert out == [{"orig": "안녕", "reading": "", "romaji": ""}]


def test_build_state_japanese_includes_words():
    kks = _FakeKakasi([{"orig": "猫", "hira": "ねこ", "hepburn": "neko"}])
    s = build_state("猫", "ja", kakasi=kks)
    assert s["lang"] == "ja"
    assert s["words"][0]["orig"] == "猫"
    assert s["words"][0]["reading"] == "ねこ"
    assert s["paused"] is False
    assert s["translation"] == ""
    assert "lookup" not in s


def test_build_state_translation_extracted_from_enrichment():
    s = build_state("猫", "ja", kakasi=_FakeKakasi([
        {"orig": "猫", "hira": "ねこ", "hepburn": "neko"}
    ]), enrichment={"translation": {"en": "cat"}})
    assert s["translation"] == "cat"


def test_build_state_paused_flag_passes_through():
    s = build_state("hi", "ja", kakasi=_FakeKakasi([]), paused=True)
    assert s["paused"] is True


def test_build_state_lookup_attached_when_provided():
    lookup = {"word": "猫", "reading": "ねこ", "hits": [{"meanings": ["cat"]}]}
    s = build_state("猫", "ja", kakasi=_FakeKakasi([
        {"orig": "猫", "hira": "ねこ", "hepburn": "neko"}
    ]), lookup=lookup)
    assert s["lookup"]["word"] == "猫"


def test_build_state_unknown_lang_passes_text_through():
    s = build_state("plain", "en")
    assert s["words"] == [{"orig": "plain", "reading": "", "romaji": ""}]


def test_build_state_empty_text_yields_no_words():
    s = build_state("", "ja", kakasi=_FakeKakasi([]))
    assert s["words"] == []


class _RecordingRunner:
    """Captures osascript calls and returns scripted responses."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self._responses:
            return self._responses.pop(0)
        return ""


def test_overlay_driver_installs_on_first_tick():
    runner = _RecordingRunner(responses=["", "null", ""])
    drv = OverlayDriver("disney.com", runner=runner)
    drv.tick({"text": "x", "words": [], "paused": False})
    # First tick: install + pop_action + push = 3 osascript calls.
    assert len(runner.calls) == 3
    assert "lang-view-overlay" in runner.calls[0][2]


def test_overlay_driver_skips_install_after_first_tick():
    runner = _RecordingRunner(responses=["", "null", "", "null", ""])
    drv = OverlayDriver("disney.com", runner=runner, rebootstrap_every=999)
    drv.tick({"text": "a"})
    drv.tick({"text": "b"})
    # First tick: install + pop + push (3). Second tick: pop + push (2).
    assert len(runner.calls) == 5


def test_overlay_driver_rebootstraps_periodically():
    runner = _RecordingRunner(responses=[""] * 50)
    drv = OverlayDriver("disney.com", runner=runner, rebootstrap_every=2)
    drv.tick({"text": "1"})  # install + pop + push
    drv.tick({"text": "2"})  # tick_count=2 → re-install + pop + push
    # 3 + 3 = 6 calls; the 4th call (start of 2nd tick) is an install.
    assert "lang-view-overlay" in runner.calls[3][2]


def test_overlay_driver_returns_parsed_action():
    runner = _RecordingRunner(responses=["", '{"type":"save"}', ""])
    drv = OverlayDriver("disney.com", runner=runner)
    action = drv.tick({"text": "x"})
    assert action == {"type": "save"}


def test_overlay_driver_returns_none_when_no_action():
    runner = _RecordingRunner(responses=["", "null", ""])
    drv = OverlayDriver("disney.com", runner=runner)
    assert drv.tick({"text": "x"}) is None


def test_overlay_driver_swallows_runner_exceptions():
    def boom(_cmd):
        raise RuntimeError("Apple Events not allowed")

    drv = OverlayDriver("disney.com", runner=boom)
    # Must not raise — a flaky bridge can't be allowed to crash the watch loop.
    assert drv.tick({"text": "x"}) is None


def test_overlay_driver_push_embeds_state():
    runner = _RecordingRunner(responses=["", "null", ""])
    drv = OverlayDriver("netflix.com/watch", runner=runner)
    drv.tick({"text": "안녕", "words": [{"orig": "안녕"}]})
    push_script = runner.calls[2][2]
    assert "안녕" in push_script
    assert "netflix.com/watch" in push_script
