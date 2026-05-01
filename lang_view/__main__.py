import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .dedup import TimeWindowDedup
from .frames import frame_signature, to_xywh
from .text_filter import classify

log = logging.getLogger("lang_view")


def _build_readers(lang, gpu):
    import easyocr  # heavy import, defer until run-time

    readers = []
    if lang in ("ko", "both"):
        readers.append(("ko", easyocr.Reader(["ko", "en"], gpu=gpu, verbose=False)))
    if lang in ("ja", "both"):
        readers.append(("ja", easyocr.Reader(["ja", "en"], gpu=gpu, verbose=False)))
    return readers


def _grab(sct, monitor_index):
    import numpy as np

    monitor = sct.monitors[monitor_index]
    img = np.array(sct.grab(monitor))
    return img[:, :, :3]


def _process_detection(detection, hint, min_confidence, dedup):
    bbox, text, conf = detection
    if conf < min_confidence:
        return None
    text = text.strip()
    if not text:
        return None
    lang = classify(text, hint)
    if lang is None:
        return None
    if not dedup.check_and_add(f"{lang}\t{text}"):
        return None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "text": text,
        "confidence": round(float(conf), 3),
        "bbox": to_xywh(bbox),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Watch the screen and log any Korean or Japanese text it shows."
    )
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between captures (default: 1.0)")
    parser.add_argument("--output", type=Path, default=Path("captures.jsonl"),
                        help="Output JSONL file (default: captures.jsonl)")
    parser.add_argument("--monitor", type=int, default=1,
                        help="Monitor index, 1 = primary, 0 = all combined (default: 1)")
    parser.add_argument("--lang", choices=("ko", "ja", "both"), default="both",
                        help="Which language(s) to OCR (default: both)")
    parser.add_argument("--gpu", action="store_true",
                        help="Use GPU for OCR if available")
    parser.add_argument("--min-confidence", type=float, default=0.4,
                        help="Drop OCR results below this confidence (default: 0.4)")
    parser.add_argument("--dedup-seconds", type=float, default=60.0,
                        help="Suppress identical text seen within this window (default: 60)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Log every detection to stderr")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    log.info("Loading EasyOCR (%s). First run downloads ~100MB of models.", args.lang)
    readers = _build_readers(args.lang, args.gpu)

    import mss  # defer until after readers loaded so the user sees model download first

    dedup = TimeWindowDedup(args.dedup_seconds)
    last_sig = None
    skipped_unchanged = 0

    log.info("Watching every %.2fs -> %s. Ctrl+C to stop.", args.interval, args.output)
    log.info("On macOS, grant Screen Recording permission to your terminal.")

    with mss.mss() as sct, args.output.open("a", encoding="utf-8") as out:
        while True:
            cycle_start = time.monotonic()
            try:
                frame = _grab(sct, args.monitor)
            except Exception as e:
                log.warning("Capture error: %s", e)
                time.sleep(args.interval)
                continue

            sig = frame_signature(frame)
            if sig == last_sig:
                skipped_unchanged += 1
                if skipped_unchanged % 30 == 0:
                    log.debug("Skipped %d unchanged frames", skipped_unchanged)
                time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                continue
            last_sig = sig
            skipped_unchanged = 0

            for hint, reader in readers:
                try:
                    detections = reader.readtext(frame)
                except Exception as e:
                    log.warning("OCR error (%s): %s", hint, e)
                    continue
                for det in detections:
                    record = _process_detection(det, hint, args.min_confidence, dedup)
                    if record is None:
                        continue
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    log.info("[%s] %s", record["lang"], record["text"])

            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, args.interval - elapsed))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log.info("Stopped.")
        sys.exit(0)
