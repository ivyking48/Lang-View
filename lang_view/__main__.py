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


def _grab(sct, monitor_index):
    import numpy as np

    monitor = sct.monitors[monitor_index]
    img = np.array(sct.grab(monitor))
    return img[:, :, :3]


def _process_detection(detection, hint, min_confidence, dedup, pipeline):
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
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "text": text,
        "confidence": round(float(conf), 3),
        "bbox": to_xywh(bbox),
    }
    if pipeline is not None and len(pipeline) > 0:
        enrichment = pipeline.enrich(text, lang)
        if enrichment:
            record["enrichment"] = enrichment
    return record


def _add_watch_args(parser):
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between captures (default: 1.0)")
    parser.add_argument("--output", type=Path, default=Path("captures.jsonl"),
                        help="Output JSONL file (default: captures.jsonl)")
    parser.add_argument("--monitor", type=int, default=1,
                        help="Monitor index, 1 = primary, 0 = all combined (default: 1)")
    parser.add_argument("--lang", choices=("ko", "ja", "both"), default="both",
                        help="Which language(s) to OCR (default: both)")
    parser.add_argument("--engine", choices=("easyocr", "manga-ocr"), default="easyocr",
                        help="OCR engine for Japanese (manga-ocr only affects ja)")
    parser.add_argument("--gpu", action="store_true",
                        help="Use GPU for OCR if available")
    parser.add_argument("--min-confidence", type=float, default=0.4,
                        help="Drop OCR results below this confidence (default: 0.4)")
    parser.add_argument("--dedup-seconds", type=float, default=60.0,
                        help="Suppress identical text seen within this window (default: 60)")
    parser.add_argument("--enrich", default="",
                        help="Comma-separated enrichers: furigana,romaji,romaja,dict,translate")
    parser.add_argument("--translator", default="none",
                        choices=("none", "argos", "deepl", "openai"),
                        help="Translation backend (default: none)")
    parser.add_argument("--translate-to", default="en",
                        help="Target language for translation (default: en)")
    parser.add_argument("--dict-dir", type=Path, default=None,
                        help="Directory holding jmdict-eng.json / kodict.tsv")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Log every detection to stderr")


def cmd_watch(args):
    from .enrich import build_pipeline
    from .engines import build_engines

    args.output.parent.mkdir(parents=True, exist_ok=True)

    log.info("Loading OCR engines (%s, engine=%s).", args.lang, args.engine)
    engines = build_engines(args.lang, engine=args.engine, gpu=args.gpu)

    pipeline = build_pipeline(
        args.enrich,
        dict_dir=args.dict_dir,
        translator_name=args.translator,
        translate_to=args.translate_to,
    )
    if len(pipeline) > 0:
        log.info("Enrichment pipeline: %s",
                 ", ".join(getattr(e, "name", type(e).__name__) for e in pipeline.enrichers))

    import mss

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

            for engine in engines:
                try:
                    detections = engine.read(frame)
                except Exception as e:
                    log.warning("OCR error (%s/%s): %s", engine.name, engine.lang_hint, e)
                    continue
                for det in detections:
                    record = _process_detection(
                        det, engine.lang_hint, args.min_confidence, dedup, pipeline,
                    )
                    if record is None:
                        continue
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    log.info("[%s] %s", record["lang"], record["text"])

            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, args.interval - elapsed))


def cmd_fetch_dicts(args):
    from .enrich.fetch import fetch_jmdict
    path = fetch_jmdict(dest_dir=args.dict_dir)
    log.info("JMdict ready at %s", path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lang-view",
                                     description="Watch the screen and log Korean/Japanese text.")
    sub = parser.add_subparsers(dest="command")

    watch = sub.add_parser("watch", help="Start watching the screen (default).")
    _add_watch_args(watch)

    fetch = sub.add_parser("fetch-dicts", help="Download dictionary data files.")
    fetch.add_argument("--dict-dir", type=Path, default=None,
                       help="Directory to write dictionaries into")
    fetch.add_argument("--verbose", "-v", action="store_true")

    # Allow `lang-view` with no subcommand to default to `watch`.
    if argv is None:
        argv = sys.argv[1:]
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["watch", *argv]

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.command == "watch":
        cmd_watch(args)
    elif args.command == "fetch-dicts":
        cmd_fetch_dicts(args)
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log.info("Stopped.")
        sys.exit(0)
