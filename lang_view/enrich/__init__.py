import logging
from typing import Protocol

log = logging.getLogger(__name__)


class Enricher(Protocol):
    name: str

    def applies_to(self, lang: str) -> bool: ...
    def enrich(self, text: str, lang: str) -> dict: ...


class EnrichmentPipeline:
    """Run a list of enrichers and merge their outputs.

    Failures in one enricher are logged and ignored — never prevent
    a record from being written.
    """

    def __init__(self, enrichers):
        self.enrichers = list(enrichers)

    def enrich(self, text, lang):
        out = {}
        for enricher in self.enrichers:
            if not enricher.applies_to(lang):
                continue
            try:
                out.update(enricher.enrich(text, lang))
            except Exception as e:
                log.warning("Enricher %s failed on %r: %s",
                            getattr(enricher, "name", type(enricher).__name__), text, e)
        return out

    def __len__(self):
        return len(self.enrichers)


def build_pipeline(spec, *, dict_dir=None, translator_name="none",
                   translate_to="en", api_key_env=None):
    """Construct an EnrichmentPipeline from a comma-separated spec.

    Recognised tokens: furigana, romaji, romaja, dict, translate.
    Unknown tokens are ignored with a warning. Each enricher is
    constructed lazily so missing optional dependencies only fail
    if the user actually requested them.
    """
    enrichers = []
    tokens = [t.strip() for t in (spec or "").split(",") if t.strip()]
    for token in tokens:
        if token in ("furigana", "romaji"):
            from .kana import KanaEnricher
            enrichers.append(KanaEnricher(include_furigana="furigana" in tokens,
                                          include_romaji="romaji" in tokens))
        elif token == "romaja":
            from .romaja import RomajaEnricher
            enrichers.append(RomajaEnricher())
        elif token == "dict":
            from .dictionary import DictionaryEnricher
            enrichers.append(DictionaryEnricher(dict_dir=dict_dir))
        elif token == "translate":
            from .translation import build_translator, TranslationEnricher
            translator = build_translator(translator_name, api_key_env=api_key_env)
            enrichers.append(TranslationEnricher(translator, target_lang=translate_to))
        else:
            log.warning("Unknown enrichment token: %r", token)

    # Deduplicate KanaEnricher if both furigana and romaji were requested.
    seen = set()
    deduped = []
    for e in enrichers:
        cls = type(e)
        if cls in seen and cls.__name__ == "KanaEnricher":
            continue
        seen.add(cls)
        deduped.append(e)
    return EnrichmentPipeline(deduped)
