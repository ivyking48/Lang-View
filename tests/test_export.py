import csv

from lang_view.__main__ import _write_anki_tsv, _write_csv


def _record(text, lang="ja", enrichment=None, **extra):
    r = {
        "timestamp": "2026-05-01T12:00:00+00:00",
        "lang": lang,
        "text": text,
        "confidence": 0.9,
    }
    if enrichment is not None:
        r["enrichment"] = enrichment
    r.update(extra)
    return r


def test_csv_export_includes_enrichment_columns(tmp_path):
    out = tmp_path / "x.csv"
    _write_csv(out, [
        _record("今日", enrichment={"furigana": "きょう", "romaji": "kyō",
                                  "translation": {"en": "today"}}),
        _record("안녕", lang="ko", enrichment={"romaja": "annyeong"}),
    ])
    rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    assert rows[0]["text"] == "今日"
    assert rows[0]["furigana"] == "きょう"
    assert rows[0]["translation"] == "today"
    assert rows[1]["romaja"] == "annyeong"


def test_csv_export_handles_records_without_enrichment(tmp_path):
    out = tmp_path / "x.csv"
    _write_csv(out, [_record("hi")])
    rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    assert rows[0]["furigana"] == ""
    assert rows[0]["translation"] == ""


def test_anki_tsv_dedups_repeated_text(tmp_path):
    out = tmp_path / "x.tsv"
    _write_anki_tsv(out, [
        _record("今日", enrichment={"furigana": "きょう"}),
        _record("今日", enrichment={"furigana": "きょう"}),
        _record("天気", enrichment={"furigana": "てんき",
                                    "translation": {"en": "weather"}}),
    ])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_anki_tsv_uses_dictionary_glosses_when_no_translation(tmp_path):
    out = tmp_path / "x.tsv"
    _write_anki_tsv(out, [_record("天気", enrichment={
        "furigana": "てんき",
        "dictionary": [{"token": "天気", "matches": [{"meanings": ["weather", "climate"]}]}],
    })])
    line = out.read_text(encoding="utf-8").strip()
    front, back, lang = line.split("\t")
    assert "天気" in front
    assert "てんき" in front
    assert "weather" in back


def test_anki_tsv_handles_records_without_enrichment(tmp_path):
    out = tmp_path / "x.tsv"
    _write_anki_tsv(out, [_record("hi")])
    line = out.read_text(encoding="utf-8").strip()
    front, back, lang = line.split("\t")
    assert front == "hi"
    assert back == ""
    assert lang == "ja"
