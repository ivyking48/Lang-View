import argparse
import csv
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .app_filter import AppFilter
from .crypto import generate_key, maybe_cipher
from .dedup import TimeWindowDedup
from .frames import (
    crop,
    frame_thumbnail,
    frames_similar,
    group_lines,
    to_xywh,
)
from .text_filter import classify
from .worker import AsyncOCRWorker, PauseSwitch

log = logging.getLogger("lang_view")


def _grab(sct, monitor_index):
    import numpy as np

    monitor = sct.monitors[monitor_index]
    img = np.array(sct.grab(monitor))
    return img[:, :, :3]


def _parse_region(spec):
    if not spec:
        return None
    parts = [int(p.strip()) for p in spec.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--region must be x,y,w,h")
    return tuple(parts)


def _build_record(detection, hint, args, app_name, snippet_path, pipeline):
    bbox, text, conf = detection
    if conf < args.min_confidence:
        return None
    text = text.strip()
    if not text:
        return None
    lang = classify(text, hint)
    if lang is None:
        return None
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "text": text,
        "confidence": round(float(conf), 3),
        "bbox": list(bbox) if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox) else to_xywh(bbox),
    }
    if app_name:
        record["app"] = app_name
    if snippet_path:
        record["snippet"] = snippet_path
    if pipeline is not None and len(pipeline) > 0:
        enrichment = pipeline.enrich(text, lang)
        if enrichment:
            record["enrichment"] = enrichment
    return record


def _add_watch_args(parser):
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=Path("captures.jsonl"),
                        help="JSONL output file (set '' to disable)")
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite DB path (enables structured storage)")
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--region", type=_parse_region, default=None,
                        help="Capture only this rect: x,y,w,h")
    parser.add_argument("--active-window", action="store_true",
                        help="Each cycle, capture the frontmost window only (macOS)")
    parser.add_argument("--app-allow", default="",
                        help="Comma-separated allowlist of app names (substring, case-insensitive)")
    parser.add_argument("--app-block", default="",
                        help="Comma-separated blocklist of app names")
    parser.add_argument("--lang", choices=("ko", "ja", "both"), default="both")
    parser.add_argument("--engine", choices=("easyocr", "manga-ocr"), default="easyocr")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=0.4)
    parser.add_argument("--dedup-seconds", type=float, default=60.0)
    parser.add_argument("--frame-mean-tolerance", type=float, default=2.0,
                        help="Mean pixel diff tolerated before re-OCR (0..255)")
    parser.add_argument("--frame-change-fraction", type=float, default=0.01,
                        help="Fraction of pixels that must change before re-OCR")
    parser.add_argument("--group-lines", action="store_true",
                        help="Merge adjacent OCR boxes into lines before logging")
    parser.add_argument("--snippets-dir", type=Path, default=None,
                        help="Save a cropped PNG per detection here")
    parser.add_argument("--encrypt-key-env", default=None,
                        help="Env var holding a Fernet key; enables JSONL encryption")
    parser.add_argument("--enrich", default="")
    parser.add_argument("--translator", default="none",
                        choices=("none", "argos", "deepl", "openai"))
    parser.add_argument("--translate-to", default="en")
    parser.add_argument("--dict-dir", type=Path, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")


def cmd_watch(args):
    from .enrich import build_pipeline
    from .engines import build_engines

    storage = None
    if args.db:
        from .storage import Storage
        storage = Storage(args.db)

    out_file = None
    out_path = args.output if str(args.output) else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_file = out_path.open("a", encoding="utf-8")

    cipher = maybe_cipher(args.encrypt_key_env)
    if cipher:
        log.info("JSONL encryption enabled (env=%s)", args.encrypt_key_env)

    snippets = None
    if args.snippets_dir:
        from .snippets import SnippetWriter
        snippets = SnippetWriter(args.snippets_dir)

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

    app_filter = AppFilter.parse(args.app_allow, args.app_block)
    dedup = TimeWindowDedup(args.dedup_seconds)
    pause = PauseSwitch()
    last_thumb = None

    try:
        signal.signal(signal.SIGUSR1, lambda *_: log.info(
            "Pause toggled: %s", "paused" if pause.toggle() else "running"))
    except (AttributeError, ValueError):
        pass  # Windows / non-main-thread

    def engines_run(frame, app_name):
        results = []
        for engine in engines:
            try:
                detections = engine.read(frame)
            except Exception as e:
                log.warning("OCR error (%s/%s): %s", engine.name, engine.lang_hint, e)
                continue
            if args.group_lines:
                detections = group_lines(detections)
            results.append((engine.lang_hint, detections))
        return results

    def on_detections(hint, detections, frame, app_name):
        for det in detections:
            bbox = det[0]
            bbox_xywh = list(bbox) if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox) else to_xywh(bbox)
            snippet_path = None
            if snippets is not None:
                snippet_path = snippets.write(frame, bbox_xywh, label=det[1] or hint)
            record = _build_record(det, hint, args, app_name, snippet_path, pipeline)
            if record is None:
                continue
            if not dedup.check_and_add(f"{record['lang']}\t{record['text']}"):
                continue
            if out_file is not None:
                payload = cipher.encrypt(record) if cipher else record
                out_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
                out_file.flush()
            if storage is not None:
                storage.write(record)
            log.info("[%s] %s", record["lang"], record["text"])

    def on_error(e):
        log.warning("OCR worker error: %s", e)

    worker = AsyncOCRWorker(engines_run, on_detections, on_error=on_error)
    worker.start()

    import mss
    log.info("Watching every %.2fs. Ctrl+C to stop. SIGUSR1 toggles pause.", args.interval)
    log.info("On macOS, grant Screen Recording permission to your terminal.")

    try:
        with mss.mss() as sct:
            while True:
                cycle_start = time.monotonic()
                if pause.is_paused():
                    time.sleep(args.interval)
                    continue

                app_name = None
                region = args.region
                if args.active_window:
                    from .macos import get_active_window
                    win = get_active_window()
                    if win is not None:
                        app_name = win.app
                        if win.w > 0 and win.h > 0:
                            region = win.region

                if not app_filter.allows(app_name):
                    log.debug("Skipping; app %r not allowed", app_name)
                    time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                    continue

                try:
                    frame = _grab(sct, args.monitor)
                except Exception as e:
                    log.warning("Capture error: %s", e)
                    time.sleep(args.interval)
                    continue

                if region is not None:
                    frame = crop(frame, region)
                    if frame.size == 0:
                        time.sleep(args.interval)
                        continue

                thumb = frame_thumbnail(frame)
                if frames_similar(last_thumb, thumb,
                                  mean_tolerance=args.frame_mean_tolerance,
                                  change_fraction=args.frame_change_fraction):
                    time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                    continue
                last_thumb = thumb

                worker.submit(frame, app_name=app_name)

                elapsed = time.monotonic() - cycle_start
                time.sleep(max(0.0, args.interval - elapsed))
    finally:
        worker.stop()
        if out_file is not None:
            out_file.close()
        if storage is not None:
            storage.close()
        if worker.dropped:
            log.info("Dropped %d frames while OCR was busy.", worker.dropped)


def cmd_fetch_dicts(args):
    from .enrich.fetch import fetch_jmdict
    path = fetch_jmdict(dest_dir=args.dict_dir)
    log.info("JMdict ready at %s", path)


def cmd_search(args):
    from .storage import Storage
    cipher = maybe_cipher(args.encrypt_key_env)
    if args.db:
        with Storage(args.db) as s:
            results = s.search(args.query, lang=args.lang, limit=args.limit)
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        return
    if args.input:
        for record in _iter_jsonl(args.input, cipher=cipher):
            if args.lang and record.get("lang") != args.lang:
                continue
            if args.query.lower() in record.get("text", "").lower():
                print(json.dumps(record, ensure_ascii=False))
        return
    log.error("--db or --input is required for search")
    sys.exit(2)


def cmd_stats(args):
    from .storage import Storage
    if not args.db:
        log.error("--db is required for stats")
        sys.exit(2)
    with Storage(args.db) as s:
        rows = s.frequencies(lang=args.lang, limit=args.limit)
    for row in rows:
        print(f"{row['n']:>6}  [{row['lang']}]  {row['text']}")


def cmd_export(args):
    cipher = maybe_cipher(args.encrypt_key_env)
    records = []
    if args.db:
        from .storage import Storage
        with Storage(args.db) as s:
            records = s.all(lang=args.lang)
    elif args.input:
        records = list(_iter_jsonl(args.input, cipher=cipher))
        if args.lang:
            records = [r for r in records if r.get("lang") == args.lang]
    else:
        log.error("--db or --input is required for export")
        sys.exit(2)

    if args.format == "csv":
        _write_csv(args.output, records)
    elif args.format == "anki":
        _write_anki_tsv(args.output, records)
    else:
        log.error("Unknown export format: %s", args.format)
        sys.exit(2)
    log.info("Wrote %d records to %s", len(records), args.output)


def cmd_keygen(args):
    print(generate_key())


def cmd_install_agent(args):
    from .launchd import (
        DEFAULT_LABEL,
        default_agent_path,
        install_agent,
        is_launchctl_available,
        render_plist,
        write_agent,
    )
    program_args = [sys.executable, "-m", "lang_view", "watch"]
    if args.db:
        program_args += ["--db", str(args.db)]
    if args.output:
        program_args += ["--output", str(args.output)]
    raw = render_plist(
        label=args.label,
        program_args=program_args,
        working_dir=str(Path.home()),
        stdout_path=str(args.log) if args.log else None,
        stderr_path=str(args.log) if args.log else None,
    )
    plist_path = args.plist or default_agent_path(args.label)
    write_agent(plist_path, raw)
    log.info("Wrote agent plist to %s", plist_path)
    if not is_launchctl_available():
        log.warning("launchctl not found; the plist was written but not loaded.")
        return
    install_agent(plist_path)
    log.info("Loaded LaunchAgent %s", args.label)


def cmd_uninstall_agent(args):
    from .launchd import (
        is_launchctl_available,
        uninstall_agent,
    )
    if not is_launchctl_available():
        log.warning("launchctl not found; remove the plist manually.")
        return
    uninstall_agent(label=args.label)
    log.info("Uninstalled LaunchAgent %s", args.label)


def cmd_dashboard(args):
    from .dashboard import create_app
    app = create_app(args.db)
    log.info("Serving dashboard for %s on http://%s:%d",
             args.db, args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


def cmd_menubar(args):
    from .menubar import MenubarApp, MenubarState
    state = MenubarState()
    if args.recent:
        from .storage import Storage
        with Storage(args.recent) as s:
            for record in s.all(limit=20):
                state.append(record)
    MenubarApp(state).run()


def _iter_jsonl(path, cipher=None):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if cipher and isinstance(obj, dict) and obj.get("v") == 1 and "ct" in obj:
                obj = cipher.decrypt(obj)
            yield obj


def _write_csv(path, records):
    fields = ["timestamp", "lang", "text", "confidence", "app",
              "furigana", "romaji", "romaja", "translation"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in ["timestamp", "lang", "text",
                                             "confidence", "app"]}
            enrichment = r.get("enrichment") or {}
            row["furigana"] = enrichment.get("furigana", "")
            row["romaji"] = enrichment.get("romaji", "")
            row["romaja"] = enrichment.get("romaja", "")
            translations = enrichment.get("translation", {})
            row["translation"] = next(iter(translations.values()), "") if translations else ""
            writer.writerow(row)


def _write_anki_tsv(path, records):
    """Anki-compatible TSV: front (text + reading) | back (translation/gloss).

    One unique surface form per row; later sightings are dropped.
    """
    seen = {}
    for r in records:
        text = r.get("text")
        if not text or text in seen:
            continue
        enrichment = r.get("enrichment") or {}
        reading = enrichment.get("furigana") or enrichment.get("romaji") \
            or enrichment.get("romaja") or ""
        translations = enrichment.get("translation", {})
        back = next(iter(translations.values()), "") if translations else ""
        if not back and enrichment.get("dictionary"):
            glosses = []
            for entry in enrichment["dictionary"]:
                for m in entry.get("matches", [])[:1]:
                    glosses.extend(m.get("meanings", [])[:2])
            back = "; ".join(glosses[:5])
        seen[text] = (reading, back, r.get("lang", ""))

    with open(path, "w", encoding="utf-8") as f:
        for text, (reading, back, lang) in seen.items():
            front = f"{text} <i>{reading}</i>" if reading else text
            f.write(f"{front}\t{back}\t{lang}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lang-view",
                                     description="Watch the screen and log Korean/Japanese text.")
    sub = parser.add_subparsers(dest="command")

    watch = sub.add_parser("watch", help="Start watching the screen (default).")
    _add_watch_args(watch)

    fetch = sub.add_parser("fetch-dicts", help="Download dictionary data files.")
    fetch.add_argument("--dict-dir", type=Path, default=None)
    fetch.add_argument("--verbose", "-v", action="store_true")

    search = sub.add_parser("search", help="Search captures by text.")
    search.add_argument("query")
    search.add_argument("--db", type=Path, default=None)
    search.add_argument("--input", type=Path, default=None,
                        help="JSONL file to search instead of a DB")
    search.add_argument("--lang", choices=("ko", "ja"), default=None)
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--encrypt-key-env", default=None)
    search.add_argument("--verbose", "-v", action="store_true")

    stats = sub.add_parser("stats", help="Show most frequent captured strings.")
    stats.add_argument("--db", type=Path, required=True)
    stats.add_argument("--lang", choices=("ko", "ja"), default=None)
    stats.add_argument("--limit", type=int, default=50)
    stats.add_argument("--verbose", "-v", action="store_true")

    export = sub.add_parser("export", help="Export captures to CSV or Anki TSV.")
    export.add_argument("--db", type=Path, default=None)
    export.add_argument("--input", type=Path, default=None)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--format", choices=("csv", "anki"), default="csv")
    export.add_argument("--lang", choices=("ko", "ja"), default=None)
    export.add_argument("--encrypt-key-env", default=None)
    export.add_argument("--verbose", "-v", action="store_true")

    keygen = sub.add_parser("keygen", help="Print a fresh Fernet encryption key.")
    keygen.add_argument("--verbose", "-v", action="store_true")

    install = sub.add_parser("install-agent",
                             help="Install a launchd LaunchAgent (macOS).")
    install.add_argument("--label", default="ai.lang-view.watcher")
    install.add_argument("--plist", type=Path, default=None,
                         help="Path to write the plist (default: ~/Library/LaunchAgents/<label>.plist)")
    install.add_argument("--db", type=Path, default=None,
                         help="SQLite DB to pass to the watch command")
    install.add_argument("--output", type=Path, default=None,
                         help="JSONL output to pass to the watch command")
    install.add_argument("--log", type=Path, default=None,
                         help="stdout/stderr log file for the agent")
    install.add_argument("--verbose", "-v", action="store_true")

    uninstall = sub.add_parser("uninstall-agent",
                               help="Unload and remove the LaunchAgent (macOS).")
    uninstall.add_argument("--label", default="ai.lang-view.watcher")
    uninstall.add_argument("--verbose", "-v", action="store_true")

    dashboard = sub.add_parser("dashboard",
                               help="Serve a small web UI for the SQLite store.")
    dashboard.add_argument("--db", type=Path, required=True)
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=7321)
    dashboard.add_argument("--verbose", "-v", action="store_true")

    menubar = sub.add_parser("menubar",
                             help="Run the macOS menubar app (rumps).")
    menubar.add_argument("--recent", type=Path, default=None,
                         help="Seed the recent list from this SQLite DB")
    menubar.add_argument("--verbose", "-v", action="store_true")

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

    handlers = {
        "watch": cmd_watch,
        "fetch-dicts": cmd_fetch_dicts,
        "search": cmd_search,
        "stats": cmd_stats,
        "export": cmd_export,
        "keygen": cmd_keygen,
        "install-agent": cmd_install_agent,
        "uninstall-agent": cmd_uninstall_agent,
        "dashboard": cmd_dashboard,
        "menubar": cmd_menubar,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    handler(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log.info("Stopped.")
        sys.exit(0)
