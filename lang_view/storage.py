"""SQLite + FTS5 storage for capture records.

Each call to `Storage.write(record)` inserts one row into `captures`
and a corresponding entry into the `captures_fts` virtual table for
full-text search. The two tables are kept in sync via triggers.

The storage layer is intentionally append-only: there is no update
or delete API. Captures are facts.
"""

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    lang        TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    bbox_x      INTEGER NOT NULL,
    bbox_y      INTEGER NOT NULL,
    bbox_w      INTEGER NOT NULL,
    bbox_h      INTEGER NOT NULL,
    app         TEXT,
    snippet     TEXT,
    enrichment  TEXT
);

CREATE INDEX IF NOT EXISTS captures_lang_ts ON captures(lang, timestamp);
CREATE INDEX IF NOT EXISTS captures_text    ON captures(text);
"""


class Storage:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def write(self, record):
        bbox = record.get("bbox") or [0, 0, 0, 0]
        enrichment = record.get("enrichment")
        with self._conn:
            self._conn.execute(
                """INSERT INTO captures
                   (timestamp, lang, text, confidence,
                    bbox_x, bbox_y, bbox_w, bbox_h,
                    app, snippet, enrichment)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["timestamp"], record["lang"], record["text"],
                    float(record.get("confidence", 0.0)),
                    int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]),
                    record.get("app"),
                    record.get("snippet"),
                    json.dumps(enrichment, ensure_ascii=False) if enrichment else None,
                ),
            )

    def search(self, query, *, lang=None, limit=50):
        """Case-insensitive substring search.

        We deliberately use LIKE rather than FTS5: CJK text contains no
        whitespace tokenization boundaries, so the default FTS5
        tokenizer treats whole sentences as single tokens and refuses
        to match prefixes like "안녕" inside "안녕하세요".
        """
        like = f"%{_escape_like(query)}%"
        sql = "SELECT * FROM captures WHERE text LIKE ? ESCAPE '\\'"
        params = [like]
        if lang:
            sql += " AND lang = ?"
            params.append(lang)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(int(limit))
        with closing(self._conn.execute(sql, params)) as cur:
            return [_row_to_record(r) for r in cur.fetchall()]

    def all(self, *, lang=None, limit=None):
        sql = "SELECT * FROM captures"
        params = []
        if lang:
            sql += " WHERE lang = ?"
            params.append(lang)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with closing(self._conn.execute(sql, params)) as cur:
            return [_row_to_record(r) for r in cur.fetchall()]

    def frequencies(self, *, lang=None, limit=100):
        sql = ("SELECT text, lang, COUNT(*) AS n, MIN(timestamp) AS first_seen, "
               "MAX(timestamp) AS last_seen FROM captures")
        params = []
        if lang:
            sql += " WHERE lang = ?"
            params.append(lang)
        sql += " GROUP BY text, lang ORDER BY n DESC, last_seen DESC LIMIT ?"
        params.append(int(limit))
        with closing(self._conn.execute(sql, params)) as cur:
            return [dict(r) for r in cur.fetchall()]

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _escape_like(query):
    """Escape `%`, `_`, and `\\` for use inside a LIKE pattern."""
    return (query.replace("\\", "\\\\")
                  .replace("%", "\\%")
                  .replace("_", "\\_"))


def _row_to_record(row):
    enrichment = json.loads(row["enrichment"]) if row["enrichment"] else None
    record = {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "lang": row["lang"],
        "text": row["text"],
        "confidence": row["confidence"],
        "bbox": [row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]],
    }
    if row["app"] is not None:
        record["app"] = row["app"]
    if row["snippet"] is not None:
        record["snippet"] = row["snippet"]
    if enrichment is not None:
        record["enrichment"] = enrichment
    return record
