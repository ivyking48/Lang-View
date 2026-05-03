"""Action-handler tests for the subtitle-watch overlay glue.

The watch loop itself is integration-shaped (real osascript, real
Chrome) but the per-action side-effect logic in
``_handle_overlay_action`` is pure Python and worth pinning down.
"""

import io
import json

import pytest

from lang_view.__main__ import _handle_overlay_action
from lang_view.enrich.dictionary import DictionaryEnricher, JMdictIndex
from lang_view.pause import FilePauseFlag


SAMPLE_JMDICT = [
    {
        "id": "1",
        "kanji": [{"text": "猫"}],
        "kana": [{"text": "ねこ"}],
        "sense": [{"gloss": [{"text": "cat"}]}],
    },
]


def _record(text="今日"):
    return {
        "timestamp": "2026-05-03T00:00:00Z",
        "lang": "ja",
        "text": text,
        "confidence": 1.0,
        "bbox": [0, 0, 0, 0],
        "app": "Chrome",
    }


class _MemoryFile:
    """Stand-in for a file handle so we can assert what gets written."""

    def __init__(self):
        self.buf = io.StringIO()
        self.flushed = 0

    def write(self, s):
        self.buf.write(s)

    def flush(self):
        self.flushed += 1

    def getvalue(self):
        return self.buf.getvalue()


def test_toggle_pause_flips_flag(tmp_path):
    flag = FilePauseFlag(tmp_path / "paused")
    assert not flag.is_paused()
    out = _handle_overlay_action(
        {"type": "toggle_pause"}, _record(), "ja",
        file_pause=flag, dict_enricher=None, bookmarks_file=None,
    )
    assert flag.is_paused()
    assert out is None  # pause has no follow-up render


def test_toggle_pause_unpauses_when_set(tmp_path):
    flag = FilePauseFlag(tmp_path / "paused")
    flag.set_paused(True)
    _handle_overlay_action(
        {"type": "toggle_pause"}, _record(), "ja",
        file_pause=flag, dict_enricher=None, bookmarks_file=None,
    )
    assert not flag.is_paused()


def test_toggle_pause_without_flag_is_a_noop():
    out = _handle_overlay_action(
        {"type": "toggle_pause"}, _record(), "ja",
        file_pause=None, dict_enricher=None, bookmarks_file=None,
    )
    assert out is None  # logged but doesn't blow up


def test_save_appends_to_bookmarks_file():
    bookmarks = _MemoryFile()
    rec = _record("こんにちは")
    _handle_overlay_action(
        {"type": "save"}, rec, "ja",
        file_pause=None, dict_enricher=None, bookmarks_file=bookmarks,
    )
    line = bookmarks.getvalue().strip()
    assert json.loads(line)["text"] == "こんにちは"
    assert bookmarks.flushed == 1


def test_save_without_bookmarks_file_is_a_noop():
    # No file → action is dropped (we logged, but don't error).
    out = _handle_overlay_action(
        {"type": "save"}, _record(), "ja",
        file_pause=None, dict_enricher=None, bookmarks_file=None,
    )
    assert out is None


def test_save_with_no_record_is_a_noop():
    # User can mash Save before any subtitle has appeared.
    bookmarks = _MemoryFile()
    _handle_overlay_action(
        {"type": "save"}, None, "ja",
        file_pause=None, dict_enricher=None, bookmarks_file=bookmarks,
    )
    assert bookmarks.getvalue() == ""


def test_lookup_returns_dict_hit_for_next_state():
    enricher = DictionaryEnricher(ja_index=JMdictIndex(SAMPLE_JMDICT))
    out = _handle_overlay_action(
        {"type": "lookup", "word": "猫"}, _record(), "ja",
        file_pause=None, dict_enricher=enricher, bookmarks_file=None,
    )
    assert out["word"] == "猫"
    assert out["hits"] and "cat" in out["hits"][0]["meanings"]


def test_lookup_with_blank_word_is_dropped():
    enricher = DictionaryEnricher(ja_index=JMdictIndex(SAMPLE_JMDICT))
    out = _handle_overlay_action(
        {"type": "lookup", "word": ""}, _record(), "ja",
        file_pause=None, dict_enricher=enricher, bookmarks_file=None,
    )
    assert out is None


def test_lookup_without_enricher_is_dropped():
    out = _handle_overlay_action(
        {"type": "lookup", "word": "猫"}, _record(), "ja",
        file_pause=None, dict_enricher=None, bookmarks_file=None,
    )
    assert out is None


def test_lookup_for_unknown_word_returns_empty_hits():
    enricher = DictionaryEnricher(ja_index=JMdictIndex(SAMPLE_JMDICT))
    out = _handle_overlay_action(
        {"type": "lookup", "word": "xyz"}, _record(), "ja",
        file_pause=None, dict_enricher=enricher, bookmarks_file=None,
    )
    # Still want a render — overlay needs to know "we tried, no hits".
    assert out == {"word": "xyz", "hits": []}


def test_unknown_action_is_dropped():
    out = _handle_overlay_action(
        {"type": "fly_to_moon"}, _record(), "ja",
        file_pause=None, dict_enricher=None, bookmarks_file=None,
    )
    assert out is None
