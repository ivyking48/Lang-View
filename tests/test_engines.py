"""The engine factory does heavy imports inside its constructors,
so here we test only the wiring that does not require easyocr/manga-ocr.
"""

from lang_view import engines


def test_build_engines_uses_easyocr_for_korean(monkeypatch):
    constructed = []

    class FakeEasy:
        name = "easyocr"

        def __init__(self, lang_hint, gpu=False):
            self.lang_hint = lang_hint
            constructed.append(("easyocr", lang_hint, gpu))

    class FakeManga:
        name = "manga-ocr"

        def __init__(self, **_):
            self.lang_hint = "ja"
            constructed.append(("manga-ocr", "ja", False))

    monkeypatch.setattr(engines, "EasyOCREngine", FakeEasy)
    monkeypatch.setattr(engines, "MangaOCREngine", FakeManga)

    out = engines.build_engines("both", engine="easyocr")
    assert [c[:2] for c in constructed] == [("easyocr", "ko"), ("easyocr", "ja")]
    assert len(out) == 2

    constructed.clear()
    out = engines.build_engines("both", engine="manga-ocr")
    # Korean still uses EasyOCR even when manga-ocr is selected.
    assert [c[:2] for c in constructed] == [("easyocr", "ko"), ("manga-ocr", "ja")]
    assert len(out) == 2

    constructed.clear()
    out = engines.build_engines("ko", engine="manga-ocr")
    assert [c[:2] for c in constructed] == [("easyocr", "ko")]

    constructed.clear()
    out = engines.build_engines("ja", engine="easyocr", gpu=True)
    assert constructed == [("easyocr", "ja", True)]
