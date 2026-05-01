import io
import json
import tarfile
from contextlib import contextmanager

from lang_view.enrich.fetch import fetch_jmdict


def _make_tgz_payload(json_obj, member_name="jmdict-eng.json"):
    raw = json.dumps(json_obj).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(member_name)
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def test_fetch_jmdict_extracts_and_writes(tmp_path):
    payload = _make_tgz_payload({"words": []})

    @contextmanager
    def fake_opener(url):
        yield io.BytesIO(payload)

    dest = fetch_jmdict(dest_dir=tmp_path, url="http://fake/", opener=fake_opener)
    assert dest.exists()
    assert dest.read_bytes() == json.dumps({"words": []}).encode("utf-8")


def test_fetch_jmdict_rejects_invalid_json(tmp_path):
    raw = b"not valid json"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("garbage.json")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))

    @contextmanager
    def fake_opener(url):
        yield io.BytesIO(buf.getvalue())

    try:
        fetch_jmdict(dest_dir=tmp_path, url="http://fake/", opener=fake_opener)
    except Exception:
        return
    raise AssertionError("expected fetch_jmdict to raise on invalid JSON")
