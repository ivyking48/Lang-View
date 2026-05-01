from lang_view.storage import Storage


def _record(text="안녕", lang="ko", **extra):
    base = {
        "timestamp": "2026-05-01T12:00:00+00:00",
        "lang": lang,
        "text": text,
        "confidence": 0.9,
        "bbox": [10, 20, 100, 30],
    }
    base.update(extra)
    return base


def test_write_and_read_back(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record())
        rows = s.all()
    assert len(rows) == 1
    assert rows[0]["text"] == "안녕"
    assert rows[0]["bbox"] == [10, 20, 100, 30]


def test_enrichment_roundtrip(tmp_path):
    enrichment = {"romaja": "annyeong", "dictionary": [{"token": "안녕"}]}
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(enrichment=enrichment))
        rows = s.all()
    assert rows[0]["enrichment"] == enrichment


def test_optional_fields_omitted_when_absent(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record())
        rows = s.all()
    assert "app" not in rows[0]
    assert "snippet" not in rows[0]
    assert "enrichment" not in rows[0]


def test_optional_fields_persisted(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(app="Safari", snippet="/tmp/x.png"))
        rows = s.all()
    assert rows[0]["app"] == "Safari"
    assert rows[0]["snippet"] == "/tmp/x.png"


def test_search_returns_matching_records(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(text="안녕하세요", lang="ko"))
        s.write(_record(text="今日は", lang="ja"))
        s.write(_record(text="hello", lang="ko"))
        # FTS5 needs at least 3 chars; "안녕" is short but FTS5 still indexes it.
        results = s.search("안녕하세요")
    assert len(results) == 1
    assert results[0]["text"] == "안녕하세요"


def test_search_can_filter_by_lang(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(text="apple", lang="ko"))
        s.write(_record(text="apple", lang="ja"))
        ja_only = s.search("apple", lang="ja")
    assert len(ja_only) == 1
    assert ja_only[0]["lang"] == "ja"


def test_frequencies(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        for _ in range(3):
            s.write(_record(text="안녕"))
        s.write(_record(text="bye"))
        freqs = s.frequencies()
    by_text = {f["text"]: f["n"] for f in freqs}
    assert by_text["안녕"] == 3
    assert by_text["bye"] == 1


def test_all_filters_by_lang(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(lang="ko"))
        s.write(_record(lang="ja"))
        ko_only = s.all(lang="ko")
    assert len(ko_only) == 1
    assert ko_only[0]["lang"] == "ko"


def test_search_finds_cjk_substrings(tmp_path):
    """CJK has no whitespace, so substring search must work without it."""
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(text="안녕하세요", lang="ko"))
        s.write(_record(text="今日は良い天気です", lang="ja"))
        ko = s.search("안녕")
        ja = s.search("天気")
    assert len(ko) == 1 and ko[0]["text"] == "안녕하세요"
    assert len(ja) == 1 and "天気" in ja[0]["text"]


def test_search_handles_special_chars_safely(tmp_path):
    """LIKE wildcards from user input must not match unrelated rows."""
    with Storage(tmp_path / "db.sqlite") as s:
        s.write(_record(text="100% safe", lang="ko"))
        s.write(_record(text="totally unrelated", lang="ko"))
        # The literal '%' should match only the first row, not act as wildcard.
        results = s.search("100% safe")
    assert len(results) == 1
    assert results[0]["text"] == "100% safe"
