import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import easyocr
import mss
import numpy as np


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
    """Return 'ko', 'ja', or None. `hint` resolves ideograph-only text."""
    if has_hangul(text):
        return "ko"
    if has_kana(text):
        return "ja"
    if has_cjk_ideograph(text):
        return hint
    return None


def grab_screen(sct, monitor_index):
    monitor = sct.monitors[monitor_index]
    img = np.array(sct.grab(monitor))
    return img[:, :, :3]


def main():
    parser = argparse.ArgumentParser(
        description="Watch the screen and log any Korean or Japanese text it shows."
    )
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between captures (default: 1.0)")
    parser.add_argument("--output", type=Path, default=Path("captures.jsonl"),
                        help="Output JSONL file (default: captures.jsonl)")
    parser.add_argument("--monitor", type=int, default=1,
                        help="Monitor index, 1 = primary, 0 = all combined (default: 1)")
    parser.add_argument("--gpu", action="store_true",
                        help="Use GPU for OCR if available")
    parser.add_argument("--min-confidence", type=float, default=0.4,
                        help="Drop OCR results below this confidence (default: 0.4)")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    print("Loading EasyOCR readers (Korean + Japanese). First run downloads models (~100MB).",
          file=sys.stderr)
    ko_reader = easyocr.Reader(["ko", "en"], gpu=args.gpu, verbose=False)
    ja_reader = easyocr.Reader(["ja", "en"], gpu=args.gpu, verbose=False)
    readers = [("ko", ko_reader), ("ja", ja_reader)]

    seen = set()
    seen_order = []
    dedup_window = 500

    print(f"Watching screen every {args.interval}s. Logging to {args.output}. Ctrl+C to stop.",
          file=sys.stderr)
    print("On macOS, grant Screen Recording permission to your terminal in "
          "System Settings > Privacy & Security.", file=sys.stderr)

    with mss.mss() as sct, args.output.open("a", encoding="utf-8") as out:
        while True:
            cycle_start = time.monotonic()
            try:
                frame = grab_screen(sct, args.monitor)
            except Exception as e:
                print(f"Capture error: {e}", file=sys.stderr)
                time.sleep(args.interval)
                continue

            for hint, reader in readers:
                try:
                    detections = reader.readtext(frame)
                except Exception as e:
                    print(f"OCR error ({hint}): {e}", file=sys.stderr)
                    continue
                for bbox, text, conf in detections:
                    if conf < args.min_confidence:
                        continue
                    text = text.strip()
                    if not text:
                        continue
                    lang = classify(text, hint)
                    if lang is None:
                        continue
                    key = f"{lang}\t{text}"
                    if key in seen:
                        continue
                    seen.add(key)
                    seen_order.append(key)
                    if len(seen_order) > dedup_window:
                        seen.discard(seen_order.pop(0))
                    record = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "lang": lang,
                        "text": text,
                        "confidence": round(float(conf), 3),
                        "bbox": [[int(x), int(y)] for x, y in bbox],
                    }
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"[{lang}] {text}", file=sys.stderr)

            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, args.interval - elapsed))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        sys.exit(0)
