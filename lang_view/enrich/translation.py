import logging
import os
from typing import Protocol

log = logging.getLogger(__name__)


class Translator(Protocol):
    name: str

    def translate(self, text: str, source_lang: str, target_lang: str) -> str: ...


class NoopTranslator:
    name = "none"

    def translate(self, text, source_lang, target_lang):
        return ""


class ArgosTranslator:
    name = "argos"

    def __init__(self):
        import argostranslate.translate  # noqa: F401  (validate import here)
        self._argos = __import__("argostranslate.translate", fromlist=["translate"])

    def translate(self, text, source_lang, target_lang):
        return self._argos.translate(text, source_lang, target_lang)


class DeepLTranslator:
    name = "deepl"

    def __init__(self, api_key):
        if not api_key:
            raise ValueError("DeepL API key is required")
        import deepl
        self._client = deepl.Translator(api_key)

    def translate(self, text, source_lang, target_lang):
        result = self._client.translate_text(
            text,
            source_lang=source_lang.upper() if source_lang else None,
            target_lang=target_lang.upper(),
        )
        return result.text if hasattr(result, "text") else str(result)


class OpenAITranslator:
    name = "openai"

    def __init__(self, api_key, model="gpt-4o-mini"):
        if not api_key:
            raise ValueError("OpenAI API key is required")
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def translate(self, text, source_lang, target_lang):
        prompt = (f"Translate the following {source_lang or 'text'} to {target_lang}. "
                  f"Reply with only the translation.\n\n{text}")
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return resp.choices[0].message.content.strip()


def build_translator(name, *, api_key_env=None, api_key=None):
    """Factory that defers heavy imports to the chosen backend.

    `api_key` overrides `api_key_env`; when neither is given we look up
    a sensible default env var per backend.
    """
    name = (name or "none").lower()
    if name in ("", "none"):
        return NoopTranslator()
    if name == "argos":
        return ArgosTranslator()
    if name == "deepl":
        key = api_key or os.environ.get(api_key_env or "DEEPL_API_KEY")
        return DeepLTranslator(key)
    if name == "openai":
        key = api_key or os.environ.get(api_key_env or "OPENAI_API_KEY")
        return OpenAITranslator(key)
    raise ValueError(f"Unknown translator backend: {name!r}")


class TranslationEnricher:
    name = "translation"

    def __init__(self, translator, target_lang="en"):
        self._translator = translator
        self._target = target_lang

    def applies_to(self, lang):
        if isinstance(self._translator, NoopTranslator):
            return False
        return lang in ("ja", "ko") and lang != self._target

    def enrich(self, text, lang):
        translated = self._translator.translate(text, lang, self._target)
        if not translated:
            return {}
        return {"translation": {self._target: translated}}
