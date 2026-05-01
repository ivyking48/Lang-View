HANGUL_RANGES = [(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)]
KANA_RANGES = [(0x3040, 0x309F), (0x30A0, 0x30FF)]
CJK_IDEOGRAPH_RANGES = [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)]


def _in_ranges(ch, ranges):
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in ranges)


def has_hangul(text):
    return any(_in_ranges(c, HANGUL_RANGES) for c in text)


def has_kana(text):
    return any(_in_ranges(c, KANA_RANGES) for c in text)


def has_cjk_ideograph(text):
    return any(_in_ranges(c, CJK_IDEOGRAPH_RANGES) for c in text)


def classify(text, hint):
    """Return 'ko', 'ja', or None.

    Hangul -> 'ko'. Kana -> 'ja'. Bare CJK ideographs are ambiguous,
    so they fall back to `hint` (the language of the OCR reader that
    produced the text).
    """
    if has_hangul(text):
        return "ko"
    if has_kana(text):
        return "ja"
    if has_cjk_ideograph(text):
        return hint
    return None
