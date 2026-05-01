"""Per-record encryption for the JSONL log.

When `--encrypt-key-env LV_KEY` is set, each line written to the JSONL
output is replaced with `{"v":1,"ct":"<base64 token>"}` where the
token is a Fernet ciphertext of the original record. Decryption is
handled by the `lang-view export` and `lang-view search` subcommands
which read the same env var.

The key is a urlsafe-base64 32-byte secret as produced by
`Fernet.generate_key()`. We never write keys to disk.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def generate_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


def load_key_from_env(env_var):
    if not env_var:
        return None
    raw = os.environ.get(env_var)
    if not raw:
        return None
    return raw.encode("ascii")


class RecordCipher:
    """Encrypt/decrypt single JSONL records."""

    def __init__(self, key):
        from cryptography.fernet import Fernet
        if isinstance(key, str):
            key = key.encode("ascii")
        self._fernet = Fernet(key)

    def encrypt(self, record):
        plaintext = json.dumps(record, ensure_ascii=False).encode("utf-8")
        token = self._fernet.encrypt(plaintext).decode("ascii")
        return {"v": 1, "ct": token}

    def decrypt(self, envelope):
        if not isinstance(envelope, dict) or envelope.get("v") != 1 or "ct" not in envelope:
            raise ValueError("not an encrypted record envelope")
        token = envelope["ct"].encode("ascii")
        return json.loads(self._fernet.decrypt(token).decode("utf-8"))


def maybe_cipher(env_var) -> Optional[RecordCipher]:
    key = load_key_from_env(env_var)
    if key is None:
        return None
    return RecordCipher(key)
