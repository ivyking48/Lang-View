import pytest

pytest.importorskip("cryptography")

from lang_view.crypto import (
    RecordCipher,
    generate_key,
    load_key_from_env,
    maybe_cipher,
)


def test_roundtrip():
    key = generate_key()
    cipher = RecordCipher(key)
    record = {"timestamp": "2026-05-01", "lang": "ko", "text": "안녕"}
    envelope = cipher.encrypt(record)
    assert envelope["v"] == 1
    assert "ct" in envelope
    assert cipher.decrypt(envelope) == record


def test_envelope_does_not_leak_plaintext():
    cipher = RecordCipher(generate_key())
    secret = "very secret 한국어 text"
    envelope = cipher.encrypt({"text": secret})
    assert secret not in envelope["ct"]


def test_decrypt_rejects_garbage():
    cipher = RecordCipher(generate_key())
    with pytest.raises(ValueError):
        cipher.decrypt({"not": "an envelope"})


def test_decrypt_rejects_wrong_key():
    a = RecordCipher(generate_key())
    b = RecordCipher(generate_key())
    envelope = a.encrypt({"x": 1})
    with pytest.raises(Exception):
        b.decrypt(envelope)


def test_load_key_from_env_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("LV_TEST_KEY", raising=False)
    assert load_key_from_env("LV_TEST_KEY") is None
    assert load_key_from_env(None) is None


def test_load_key_from_env_returns_bytes(monkeypatch):
    key = generate_key()
    monkeypatch.setenv("LV_TEST_KEY", key)
    loaded = load_key_from_env("LV_TEST_KEY")
    assert loaded == key.encode("ascii")


def test_maybe_cipher_disabled_when_env_missing(monkeypatch):
    monkeypatch.delenv("LV_TEST_KEY", raising=False)
    assert maybe_cipher("LV_TEST_KEY") is None


def test_maybe_cipher_enabled_when_env_present(monkeypatch):
    monkeypatch.setenv("LV_TEST_KEY", generate_key())
    cipher = maybe_cipher("LV_TEST_KEY")
    assert cipher is not None
    envelope = cipher.encrypt({"x": "hi"})
    assert cipher.decrypt(envelope) == {"x": "hi"}
