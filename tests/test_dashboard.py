import pytest

pytest.importorskip("flask")

from lang_view.dashboard import create_app
from lang_view.storage import Storage


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "x.sqlite"
    with Storage(path) as s:
        s.write({"timestamp": "2026-05-01T12:00:00+00:00", "lang": "ko",
                 "text": "안녕", "confidence": 0.9, "bbox": [0, 0, 10, 10]})
        s.write({"timestamp": "2026-05-01T12:00:01+00:00", "lang": "ja",
                 "text": "今日", "confidence": 0.9, "bbox": [0, 0, 10, 10],
                 "enrichment": {"furigana": "きょう",
                                "translation": {"en": "today"}}})
        s.write({"timestamp": "2026-05-01T12:00:02+00:00", "lang": "ja",
                 "text": "今日", "confidence": 0.9, "bbox": [0, 0, 10, 10]})
    return path


@pytest.fixture
def client(db):
    app = create_app(db)
    app.config["TESTING"] = True
    return app.test_client()


def test_index_renders_search_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Lang-View" in response.data


def test_index_with_query_returns_matching_rows(client):
    response = client.get("/?q=%EC%95%88%EB%85%95")  # 안녕
    assert response.status_code == 200
    assert "안녕".encode("utf-8") in response.data


def test_index_with_no_matches_says_so(client):
    response = client.get("/?q=zzzzznomatch")
    assert response.status_code == 200
    assert b"No matches" in response.data


def test_api_search_returns_json(client):
    response = client.get("/api/search?q=%E4%BB%8A%E6%97%A5")  # 今日
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)
    assert all(r["text"] == "今日" for r in payload)


def test_api_search_filters_by_lang(client):
    response = client.get("/api/search?q=%EC%95%88%EB%85%95&lang=ja")
    payload = response.get_json()
    assert payload == []


def test_api_search_requires_q(client):
    response = client.get("/api/search")
    assert response.status_code == 400


def test_api_all_paginates(client):
    response = client.get("/api/all?limit=2")
    payload = response.get_json()
    assert len(payload) == 2


def test_api_stats(client):
    response = client.get("/api/stats")
    payload = response.get_json()
    by_text = {r["text"]: r["n"] for r in payload}
    assert by_text["今日"] == 2
    assert by_text["안녕"] == 1


def test_stats_html_renders(client):
    response = client.get("/stats")
    assert response.status_code == 200
    assert "今日".encode("utf-8") in response.data


def test_index_escapes_html_in_query(client):
    response = client.get("/?q=%3Cscript%3E")
    # The literal <script> should not survive into the HTML.
    assert b"<script>" not in response.data
    assert b"&lt;script&gt;" in response.data


def test_enrichment_is_visible_on_index(client):
    response = client.get("/?q=%E4%BB%8A%E6%97%A5")
    assert "きょう".encode("utf-8") in response.data
    assert b"today" in response.data
