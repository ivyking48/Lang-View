"""OCR engine adapters.

Each engine exposes `read(frame_bgr) -> list[(bbox, text, conf)]` where
bbox is a 4-point polygon `[(x, y), ...]` (top-left, top-right,
bottom-right, bottom-left) and conf is a float in [0, 1]. This matches
EasyOCR's native return shape so the rest of the pipeline doesn't care
which engine produced the detections.
"""

import logging

log = logging.getLogger(__name__)


class EasyOCREngine:
    name = "easyocr"

    def __init__(self, lang_hint, gpu=False):
        import easyocr
        self.lang_hint = lang_hint
        self._reader = easyocr.Reader([lang_hint, "en"], gpu=gpu, verbose=False)

    def read(self, frame):
        return self._reader.readtext(frame)


class MangaOCREngine:
    """manga-ocr is an end-to-end Japanese OCR; it does no detection.

    We hand it the whole frame and report a single detection covering
    the image bounds with a constant confidence. This is suitable for
    pre-cropped regions or for screens that already contain a single
    block of Japanese text (e.g. a manga panel reader).
    """

    name = "manga-ocr"

    def __init__(self, **_):
        from manga_ocr import MangaOcr
        self._mocr = MangaOcr()
        self.lang_hint = "ja"

    def read(self, frame):
        text = self._mocr(frame)
        if not text:
            return []
        h, w = frame.shape[:2]
        bbox = [(0, 0), (w, 0), (w, h), (0, h)]
        return [(bbox, text, 1.0)]


def build_engines(lang, *, engine="easyocr", gpu=False):
    """Construct one engine per language requested.

    Returns a list of engines tagged with their language hint. When
    `engine='manga-ocr'` we use it only for Japanese; Korean falls
    back to EasyOCR even in that mode.
    """
    engines = []
    if lang in ("ko", "both"):
        engines.append(EasyOCREngine("ko", gpu=gpu))
    if lang in ("ja", "both"):
        if engine == "manga-ocr":
            engines.append(MangaOCREngine())
        else:
            engines.append(EasyOCREngine("ja", gpu=gpu))
    return engines
