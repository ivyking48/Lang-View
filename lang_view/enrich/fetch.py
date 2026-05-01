import json
import logging
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .dictionary import default_dict_dir

log = logging.getLogger(__name__)

JMDICT_URL = (
    "https://github.com/scriptin/jmdict-simplified/releases/latest/download/"
    "jmdict-eng-3.6.1.json.tgz"
)


def fetch_jmdict(dest_dir=None, url=JMDICT_URL, opener=urllib.request.urlopen):
    """Download and extract the JMdict-simplified English JSON file.

    `opener` is injectable for tests; it must return a file-like object
    that yields the tarball bytes on `.read()`.
    """
    dest_dir = Path(dest_dir) if dest_dir else default_dict_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "jmdict-eng.json"

    log.info("Downloading JMdict from %s", url)
    with opener(url) as response, tempfile.NamedTemporaryFile(suffix=".tgz") as tmp:
        tmp.write(response.read())
        tmp.flush()
        with tarfile.open(tmp.name, mode="r:gz") as tf:
            json_member = next(
                (m for m in tf.getmembers() if m.name.endswith(".json")), None
            )
            if json_member is None:
                raise RuntimeError("No .json file found in JMdict archive")
            extracted = tf.extractfile(json_member)
            if extracted is None:
                raise RuntimeError("Could not extract JMdict json from archive")
            payload = extracted.read()

    # Validate that what we extracted is parseable JSON before writing.
    json.loads(payload.decode("utf-8"))
    dest.write_bytes(payload)
    log.info("Wrote %s (%d bytes)", dest, dest.stat().st_size)
    return dest
