import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def default_dict_dir():
    return Path.home() / ".cache" / "lang-view" / "dicts"


_KANJI_TOKEN = re.compile(r"[一-鿿㐀-䶿぀-ゟ゠-ヿ]+")
_HANGUL_TOKEN = re.compile(r"[가-힯]+")


def _runs(text, lang):
    """Return the contiguous CJK runs in `text` for the given language."""
    pattern = _HANGUL_TOKEN if lang == "ko" else _KANJI_TOKEN
    return [m.group(0) for m in pattern.finditer(text)]


def _longest_prefix_matches(run, index, max_len=8):
    """Greedy longest-prefix tokenization against `index`.

    Walks left-to-right; at each position it tries the longest substring
    (up to `max_len`) that has a dictionary hit. If nothing matches it
    advances one character. Returns a list of (token, matches) pairs.
    """
    result = []
    i = 0
    while i < len(run):
        matched = None
        for j in range(min(len(run), i + max_len), i, -1):
            sub = run[i:j]
            hits = index.lookup(sub)
            if hits:
                matched = (sub, hits)
                break
        if matched:
            result.append(matched)
            i += len(matched[0])
        else:
            i += 1
    return result


class JMdictIndex:
    """In-memory index of JMdict-simplified entries keyed by surface form.

    Expects the JMdict-simplified JSON layout: a top-level dict with a
    "words" list, each entry having "kanji"/"kana"/"sense" arrays.
    See https://github.com/scriptin/jmdict-simplified
    """

    def __init__(self, entries):
        self._by_surface = {}
        for entry in entries:
            surfaces = set()
            for k in entry.get("kanji", []) or []:
                if isinstance(k, dict) and k.get("text"):
                    surfaces.add(k["text"])
            for k in entry.get("kana", []) or []:
                if isinstance(k, dict) and k.get("text"):
                    surfaces.add(k["text"])
            glosses = []
            for sense in entry.get("sense", []) or []:
                for g in sense.get("gloss", []) or []:
                    text = g.get("text") if isinstance(g, dict) else g
                    if text:
                        glosses.append(text)
            if not glosses:
                continue
            simplified = {
                "kanji": sorted(s for s in surfaces if any("一" <= c <= "鿿" for c in s)),
                "kana": sorted(s for s in surfaces if not any("一" <= c <= "鿿" for c in s)),
                "meanings": glosses[:5],
            }
            for surface in surfaces:
                self._by_surface.setdefault(surface, []).append(simplified)

    def lookup(self, token, limit=3):
        return self._by_surface.get(token, [])[:limit]

    def __len__(self):
        return len(self._by_surface)

    @classmethod
    def load(cls, path):
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("words", []) if isinstance(data, dict) else data)


class TsvDictIndex:
    """Simple word\treading\tmeaning TSV dictionary (used as KO fallback)."""

    def __init__(self, rows):
        self._by_surface = {}
        for row in rows:
            if not row or len(row) < 2:
                continue
            word = row[0]
            reading = row[1] if len(row) > 1 else ""
            meaning = row[2] if len(row) > 2 else ""
            self._by_surface.setdefault(word, []).append({
                "reading": reading,
                "meanings": [m.strip() for m in meaning.split(";") if m.strip()],
            })

    def lookup(self, token, limit=3):
        return self._by_surface.get(token, [])[:limit]

    def __len__(self):
        return len(self._by_surface)

    @classmethod
    def load(cls, path):
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                rows.append(line.split("\t"))
        return cls(rows)


class DictionaryEnricher:
    name = "dictionary"

    def __init__(self, dict_dir=None, ja_index=None, ko_index=None):
        self.dict_dir = Path(dict_dir) if dict_dir else default_dict_dir()
        self._ja = ja_index
        self._ko = ko_index
        self._loaded = ja_index is not None or ko_index is not None

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        ja_path = self.dict_dir / "jmdict-eng.json"
        if ja_path.exists():
            try:
                self._ja = JMdictIndex.load(ja_path)
                log.info("Loaded JMdict (%d surface forms)", len(self._ja))
            except Exception as e:
                log.warning("Failed to load JMdict at %s: %s", ja_path, e)
        else:
            log.info("JMdict not found at %s. Run `lang-view fetch-dicts`.", ja_path)
        ko_path = self.dict_dir / "kodict.tsv"
        if ko_path.exists():
            try:
                self._ko = TsvDictIndex.load(ko_path)
                log.info("Loaded Korean TSV dict (%d entries)", len(self._ko))
            except Exception as e:
                log.warning("Failed to load Korean TSV dict at %s: %s", ko_path, e)

    def applies_to(self, lang):
        return lang in ("ja", "ko")

    def enrich(self, text, lang):
        self._ensure_loaded()
        index = self._ja if lang == "ja" else self._ko
        if index is None:
            return {}
        entries = []
        seen_tokens = set()
        for run in _runs(text, lang):
            for token, hits in _longest_prefix_matches(run, index):
                if token in seen_tokens:
                    continue
                seen_tokens.add(token)
                entries.append({"token": token, "matches": hits})
        return {"dictionary": entries} if entries else {}
