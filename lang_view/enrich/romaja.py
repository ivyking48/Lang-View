class RomajaEnricher:
    """Romanize Korean text via hangul-romanize (academic / Revised Romanization)."""

    name = "romaja"

    def __init__(self, transliter=None):
        if transliter is None:
            from hangul_romanize import Transliter
            from hangul_romanize.rule import academic
            transliter = Transliter(academic)
        self._t = transliter

    def applies_to(self, lang):
        return lang == "ko"

    def enrich(self, text, lang):
        return {"romaja": self._t.translit(text).strip()}
