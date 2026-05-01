import json

from lang_view.enrich.dictionary import (
    DictionaryEnricher,
    JMdictIndex,
    TsvDictIndex,
    _longest_prefix_matches,
    _runs,
)


SAMPLE_JMDICT = {
    "words": [
        {
            "id": "1",
            "kanji": [{"text": "今日"}],
            "kana": [{"text": "きょう"}],
            "sense": [{"gloss": [{"text": "today"}, {"text": "this day"}]}],
        },
        {
            "id": "2",
            "kanji": [{"text": "天気"}],
            "kana": [{"text": "てんき"}],
            "sense": [{"gloss": [{"text": "weather"}]}],
        },
        {
            "id": "3",
            "kanji": [],
            "kana": [{"text": "です"}],
            "sense": [{"gloss": [{"text": "be (copula)"}]}],
        },
    ]
}


def test_runs_japanese_collapses_consecutive_cjk():
    # The Japanese run regex covers kanji + kana, so "今日は天気です"
    # is one continuous run that the dictionary walker will tokenize.
    runs = _runs("今日は天気です", "ja")
    assert runs == ["今日は天気です"]


def test_runs_korean_splits_on_whitespace():
    runs = _runs("안녕하세요 세계", "ko")
    assert "안녕하세요" in runs
    assert "세계" in runs


def test_longest_prefix_matches_finds_known_tokens():
    idx = JMdictIndex(SAMPLE_JMDICT["words"])
    matches = _longest_prefix_matches("今日は天気です", idx)
    found = {tok for tok, _ in matches}
    assert "今日" in found
    assert "天気" in found
    assert "です" in found


def test_longest_prefix_matches_skips_unknown_chars():
    idx = JMdictIndex(SAMPLE_JMDICT["words"])
    # "を" is not in the sample dict — walker should advance past it.
    matches = _longest_prefix_matches("今日を天気", idx)
    found = {tok for tok, _ in matches}
    assert found == {"今日", "天気"}


def test_jmdict_index_lookup():
    idx = JMdictIndex(SAMPLE_JMDICT["words"])
    hits = idx.lookup("今日")
    assert hits and "today" in hits[0]["meanings"]
    assert idx.lookup("nonexistent") == []


def test_jmdict_index_indexes_kana_too():
    idx = JMdictIndex(SAMPLE_JMDICT["words"])
    assert idx.lookup("きょう")


def test_jmdict_index_load_from_file(tmp_path):
    path = tmp_path / "jmdict-eng.json"
    path.write_text(json.dumps(SAMPLE_JMDICT), encoding="utf-8")
    idx = JMdictIndex.load(path)
    assert len(idx) > 0
    assert idx.lookup("天気")


def test_tsv_dict_index(tmp_path):
    path = tmp_path / "kodict.tsv"
    path.write_text("안녕\tannyeong\thello; hi\n세계\tsegye\tworld\n", encoding="utf-8")
    idx = TsvDictIndex.load(path)
    hits = idx.lookup("안녕")
    assert hits and "hello" in hits[0]["meanings"]


def test_tsv_dict_skips_blank_and_comment_lines(tmp_path):
    path = tmp_path / "kodict.tsv"
    path.write_text(
        "# this is a comment\n\n안녕\tannyeong\thello\n",
        encoding="utf-8",
    )
    idx = TsvDictIndex.load(path)
    assert len(idx) == 1


def test_dictionary_enricher_with_injected_indexes():
    ja = JMdictIndex(SAMPLE_JMDICT["words"])
    enricher = DictionaryEnricher(ja_index=ja)
    out = enricher.enrich("今日は天気です", "ja")
    tokens = {entry["token"] for entry in out["dictionary"]}
    assert "今日" in tokens and "天気" in tokens


def test_dictionary_enricher_returns_empty_for_unknown_tokens():
    ja = JMdictIndex(SAMPLE_JMDICT["words"])
    enricher = DictionaryEnricher(ja_index=ja)
    assert enricher.enrich("xyz", "ja") == {}


def test_dictionary_enricher_applies_to_ja_and_ko():
    enricher = DictionaryEnricher(ja_index=JMdictIndex([]))
    assert enricher.applies_to("ja")
    assert enricher.applies_to("ko")
    assert not enricher.applies_to("en")


def test_dictionary_enricher_no_index_returns_empty():
    enricher = DictionaryEnricher(dict_dir="/tmp/nonexistent_lang_view_xyz_test")
    assert enricher.enrich("今日", "ja") == {}
