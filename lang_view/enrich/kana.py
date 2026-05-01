class KanaEnricher:
    """Add `furigana` (hiragana) and/or `romaji` for Japanese text via pykakasi."""

    name = "kana"

    def __init__(self, include_furigana=True, include_romaji=True, kakasi=None):
        self.include_furigana = include_furigana
        self.include_romaji = include_romaji
        if kakasi is None:
            import pykakasi
            kakasi = pykakasi.kakasi()
        self._kks = kakasi

    def applies_to(self, lang):
        return lang == "ja"

    def enrich(self, text, lang):
        segments = self._kks.convert(text)
        out = {}
        if self.include_furigana:
            out["furigana"] = "".join(s["hira"] for s in segments)
        if self.include_romaji:
            out["romaji"] = " ".join(s["hepburn"] for s in segments if s["hepburn"]).strip()
        return out
