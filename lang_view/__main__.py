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


def _build_record(detection, hint, args, app_name, snippet_path, pipeline,
                  frame_path=None):
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
    if frame_path:
        record["frame"] = frame_path
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
                        help="Each cycle, capture the frontmost window only (macOS). "
                             "Filters by app/title against the frontmost window.")
    parser.add_argument("--visible-window", action="store_true",
                        help="Each cycle, accept the frame if ANY visible window's "
                             "app+title pass the filters. Captures the full screen "
                             "(no cropping) so side-by-side layouts still work.")
    parser.add_argument("--capture-window-app", default="",
                        help="Capture the largest visible window owned by this app "
                             "(comma-separated substrings, e.g. 'Chrome,Safari'). "
                             "Uses CGWindowListCreateImage so it works even when the "
                             "window is on a different macOS Space (e.g. fullscreen "
                             "video). Disables full-screen mss.grab.")
    parser.add_argument("--capture-window-title", default="",
                        help="When --capture-window-app is set, further restrict "
                             "to windows whose title matches one of these substrings.")
    parser.add_argument("--app-allow", default="",
                        help="Comma-separated allowlist of app names (substring, case-insensitive)")
    parser.add_argument("--app-block", default="",
                        help="Comma-separated blocklist of app names")
    parser.add_argument("--title-allow", default="",
                        help="Comma-separated allowlist of window titles (substring, case-insensitive). "
                             "Requires --active-window.")
    parser.add_argument("--title-block", default="",
                        help="Comma-separated blocklist of window titles")
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
    parser.add_argument("--frames-dir", type=Path, default=None,
                        help="Save the full captured frame as a PNG when any "
                             "detection is kept (one per OCR cycle)")
    parser.add_argument("--encrypt-key-env", default=None,
                        help="Env var holding a Fernet key; enables JSONL encryption")
    parser.add_argument("--pause-flag-file", type=Path, default=None,
                        help="If this file exists, the watch loop pauses each "
                             "cycle. Touch the file to pause, delete to resume. "
                             "Used by the menubar app for cross-process control.")
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

    frames_writer = None
    if args.frames_dir:
        from .snippets import FrameWriter
        frames_writer = FrameWriter(args.frames_dir)

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
    title_filter = AppFilter.parse(args.title_allow, args.title_block)
    capture_app_filter = AppFilter.parse(args.capture_window_app, "")
    capture_title_filter = AppFilter.parse(args.capture_window_title, "")
    dedup = TimeWindowDedup(args.dedup_seconds)
    pause = PauseSwitch()
    file_pause = None
    if args.pause_flag_file:
        from .pause import FilePauseFlag
        file_pause = FilePauseFlag(args.pause_flag_file)
        log.info("Pause flag file: %s", args.pause_flag_file)
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
        # Write the full frame once per cycle, then attach the same path to
        # every kept detection so each row is reproducible from disk.
        # FrameWriter's time-debounce keeps the second engine's call from
        # producing a duplicate PNG.
        frame_path = None
        if frames_writer is not None and detections:
            frame_path = frames_writer.write(frame)
        for det in detections:
            bbox = det[0]
            bbox_xywh = list(bbox) if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox) else to_xywh(bbox)
            snippet_path = None
            if snippets is not None:
                snippet_path = snippets.write(frame, bbox_xywh, label=det[1] or hint)
            record = _build_record(det, hint, args, app_name, snippet_path,
                                   pipeline, frame_path=frame_path)
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
    if args.visible_window and (args.title_allow or args.title_block):
        from .macos import get_visible_windows
        sample = get_visible_windows()
        if sample and all(not w.title for w in sample):
            log.warning(
                "macOS returned %d visible windows but every title is empty. "
                "Window-title filtering won't work in this process; falling "
                "back to app-only matching at runtime. (This typically "
                "happens when Screen Recording is granted to the binary but "
                "the process was spawned by launchd. Re-grant via System "
                "Settings if you need title matching.)", len(sample))

    try:
        with mss.mss() as sct:
            while True:
                cycle_start = time.monotonic()
                if pause.is_paused() or (file_pause is not None and file_pause.is_paused()):
                    time.sleep(args.interval)
                    continue

                app_name = None
                window_title = None
                region = args.region
                if args.visible_window:
                    # Gate by ANY visible window matching both filters. The
                    # full screen is OCR'd; we don't crop so subtitles in a
                    # side-by-side layout are still in frame.
                    #
                    # macOS quirk: launchd-spawned processes get
                    # CGWindowListCopyWindowInfo entries but
                    # ``kCGWindowName`` comes back blank for many apps even
                    # when Screen Recording is granted to the binary. We
                    # detect that the **candidate** (app-allowed) windows
                    # all have blank titles and, in that case, drop title
                    # filtering for the cycle — otherwise we'd never match.
                    from .macos import get_visible_windows
                    visible = get_visible_windows()
                    candidates = [w for w in visible if app_filter.allows(w.app)]
                    candidate_titles_unreadable = (
                        bool(candidates) and all(not w.title for w in candidates)
                    )
                    matched = None
                    for w in candidates:
                        if candidate_titles_unreadable or title_filter.allows(w.title):
                            matched = w
                            break
                    if matched is None:
                        log.debug("Skipping; no visible window matches filters "
                                  "(saw %d windows, %d app-allowed, "
                                  "candidate_titles_unreadable=%s, candidates: %s)",
                                  len(visible), len(candidates),
                                  candidate_titles_unreadable,
                                  [(w.app, w.title) for w in candidates])
                        time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                        continue
                    if candidate_titles_unreadable and title_filter.allow:
                        log.debug("Title filter not enforced this cycle "
                                  "(macOS returned blank titles for app-allowed windows)")
                    app_name = matched.app
                    window_title = matched.title
                else:
                    if args.active_window:
                        from .macos import get_active_window
                        win = get_active_window()
                        if win is not None:
                            app_name = win.app
                            window_title = win.title
                            if win.w > 0 and win.h > 0:
                                region = win.region

                    if not app_filter.allows(app_name):
                        log.debug("Skipping; app %r not allowed", app_name)
                        time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                        continue
                    if not title_filter.allows(window_title):
                        log.debug("Skipping; title %r not allowed", window_title)
                        time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                        continue

                if args.capture_window_app:
                    # Per-window capture path: pull pixels of a specific
                    # window via CGWindowListCreateImage, regardless of
                    # which Space is currently shown.
                    from .macos import capture_window_image, get_visible_windows
                    matches = [w for w in get_visible_windows()
                                if capture_app_filter.allows(w.app)
                                and (not args.capture_window_title
                                     or capture_title_filter.allows(w.title))]
                    if not matches:
                        log.debug("Skipping; no window matches --capture-window-app/title")
                        time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                        continue
                    target = max(matches, key=lambda w: w.area)
                    app_name = app_name or target.app
                    window_title = window_title or target.title
                    frame = capture_window_image(target.window_id)
                    if frame is None or getattr(frame, "size", 0) == 0:
                        log.debug("Skipping; capture_window_image returned empty for %s", target)
                        time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
                        continue
                else:
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


def cmd_package_app(args):
    """Generate a macOS .app bundle that wraps the watcher.

    macOS TCC tracks Screen Recording per-app via CFBundleIdentifier.
    A standalone python interpreter spawned by launchd has no stable
    bundle identity, so the system can't reliably grant or attribute
    Screen Recording. Bundling fixes that.
    """
    from .bundle import (
        DEFAULT_BUNDLE_ID,
        DEFAULT_BUNDLE_NAME,
        default_bundle_path,
        write_app_bundle,
    )
    dest = args.dest or default_bundle_path(args.bundle_name)
    write_app_bundle(
        dest,
        python_executable=sys.executable,
        bundle_id=args.bundle_id,
        bundle_name=args.bundle_name,
        lsui_element=not args.foreground,
    )
    log.info("Wrote .app bundle to %s", dest)
    log.info("Next: grant Screen Recording to %s in", dest.name)
    log.info("System Settings > Privacy & Security > Screen Recording.")


def cmd_install_agent(args):
    from .launchd import (
        DEFAULT_LABEL,
        default_agent_path,
        install_agent,
        is_launchctl_available,
        render_plist,
        write_agent,
    )
    if args.bundle:
        from .bundle import bundle_executable
        program_args = [str(bundle_executable(args.bundle)), "watch"]
    else:
        program_args = [sys.executable, "-m", "lang_view", "watch"]
    if args.db:
        program_args += ["--db", str(args.db)]
    if args.output is not None:
        program_args += ["--output", str(args.output)]
    # Watch-loop flags forwarded so the agent runs with the same config a
    # user would interactively pass to `lang-view watch`.
    if args.lang and args.lang != "both":
        program_args += ["--lang", args.lang]
    if args.engine and args.engine != "easyocr":
        program_args += ["--engine", args.engine]
    if args.gpu:
        program_args += ["--gpu"]
    if args.interval is not None and args.interval != 1.0:
        program_args += ["--interval", str(args.interval)]
    if args.monitor is not None and args.monitor != 1:
        program_args += ["--monitor", str(args.monitor)]
    if args.region:
        program_args += ["--region", ",".join(str(int(v)) for v in args.region)]
    if args.active_window:
        program_args += ["--active-window"]
    if args.visible_window:
        program_args += ["--visible-window"]
    if args.capture_window_app:
        program_args += ["--capture-window-app", args.capture_window_app]
    if args.capture_window_title:
        program_args += ["--capture-window-title", args.capture_window_title]
    if args.app_allow:
        program_args += ["--app-allow", args.app_allow]
    if args.app_block:
        program_args += ["--app-block", args.app_block]
    if args.title_allow:
        program_args += ["--title-allow", args.title_allow]
    if args.title_block:
        program_args += ["--title-block", args.title_block]
    if args.min_confidence is not None and args.min_confidence != 0.4:
        program_args += ["--min-confidence", str(args.min_confidence)]
    if args.dedup_seconds is not None and args.dedup_seconds != 60.0:
        program_args += ["--dedup-seconds", str(args.dedup_seconds)]
    if args.group_lines:
        program_args += ["--group-lines"]
    if args.snippets_dir:
        program_args += ["--snippets-dir", str(args.snippets_dir)]
    if args.frames_dir:
        program_args += ["--frames-dir", str(args.frames_dir)]
    if args.encrypt_key_env:
        program_args += ["--encrypt-key-env", args.encrypt_key_env]
    if args.pause_flag_file:
        program_args += ["--pause-flag-file", str(args.pause_flag_file)]
    if args.verbose:
        program_args += ["--verbose"]
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


def cmd_install_subtitle_agent(args):
    """Install a launchd LaunchAgent that runs ``subtitle-watch``."""
    from .launchd import (
        default_agent_path,
        install_agent,
        is_launchctl_available,
        render_plist,
        write_agent,
    )
    if args.bundle:
        from .bundle import bundle_executable
        program_args = [str(bundle_executable(args.bundle)), "subtitle-watch"]
    else:
        program_args = [sys.executable, "-m", "lang_view", "subtitle-watch"]
    if args.url_match:
        program_args += ["--url-match", args.url_match]
    if args.selector:
        program_args += ["--selector", args.selector]
    if args.interval is not None and args.interval != 1.0:
        program_args += ["--interval", str(args.interval)]
    if args.db:
        program_args += ["--db", str(args.db)]
    if args.output is not None:
        program_args += ["--output", str(args.output)]
    if args.lang_hint and args.lang_hint != "ko":
        program_args += ["--lang-hint", args.lang_hint]
    if args.pause_flag_file:
        program_args += ["--pause-flag-file", str(args.pause_flag_file)]
    if getattr(args, "enrich", ""):
        program_args += ["--enrich", args.enrich]
    if getattr(args, "translator", "none") not in ("none", None):
        program_args += ["--translator", args.translator]
    if getattr(args, "translate_to", "en") and args.translate_to != "en":
        program_args += ["--translate-to", args.translate_to]
    if getattr(args, "translator_key_env", None):
        program_args += ["--translator-key-env", args.translator_key_env]
    if getattr(args, "dict_dir", None):
        program_args += ["--dict-dir", str(args.dict_dir)]
    if getattr(args, "overlay", False):
        program_args += ["--overlay"]
    if getattr(args, "bookmarks", None):
        program_args += ["--bookmarks", str(args.bookmarks)]
    if args.verbose:
        program_args += ["--verbose"]
    raw = render_plist(
        label=args.label,
        program_args=program_args,
        working_dir=str(Path.home()),
        stdout_path=str(args.log) if args.log else None,
        stderr_path=str(args.log) if args.log else None,
    )
    plist_path = args.plist or default_agent_path(args.label)
    write_agent(plist_path, raw)
    log.info("Wrote subtitle agent plist to %s", plist_path)
    if not is_launchctl_available():
        log.warning("launchctl not found; the plist was written but not loaded.")
        return
    install_agent(plist_path)
    log.info("Loaded LaunchAgent %s", args.label)


def cmd_uninstall_subtitle_agent(args):
    from .launchd import (
        is_launchctl_available,
        uninstall_agent,
    )
    if not is_launchctl_available():
        log.warning("launchctl not found; remove the plist manually.")
        return
    uninstall_agent(label=args.label)
    log.info("Uninstalled LaunchAgent %s", args.label)


def cmd_install_menubar_agent(args):
    """Install a launchd LaunchAgent that runs the menubar app at login."""
    from .launchd import (
        default_agent_path,
        install_agent,
        is_launchctl_available,
        render_plist,
        write_agent,
    )
    if args.bundle:
        from .bundle import bundle_executable
        program_args = [str(bundle_executable(args.bundle)), "menubar"]
    else:
        program_args = [sys.executable, "-m", "lang_view", "menubar"]
    if args.pause_flag_file:
        program_args += ["--pause-flag-file", str(args.pause_flag_file)]
    if args.recent:
        program_args += ["--recent", str(args.recent)]
    raw = render_plist(
        label=args.label,
        program_args=program_args,
        working_dir=str(Path.home()),
        stdout_path=str(args.log) if args.log else None,
        stderr_path=str(args.log) if args.log else None,
    )
    plist_path = args.plist or default_agent_path(args.label)
    write_agent(plist_path, raw)
    log.info("Wrote menubar agent plist to %s", plist_path)
    if not is_launchctl_available():
        log.warning("launchctl not found; the plist was written but not loaded.")
        return
    install_agent(plist_path)
    log.info("Loaded LaunchAgent %s", args.label)


def cmd_uninstall_menubar_agent(args):
    from .launchd import (
        default_agent_path,
        is_launchctl_available,
        uninstall_agent,
    )
    if not is_launchctl_available():
        log.warning("launchctl not found; remove the plist manually.")
        return
    # uninstall_agent uses default_agent_path internally for the label.
    uninstall_agent(label=args.label)
    log.info("Uninstalled LaunchAgent %s", args.label)


def cmd_dashboard(args):
    from .dashboard import create_app
    app = create_app(args.db)
    log.info("Serving dashboard for %s on http://%s:%d",
             args.db, args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


def cmd_chrome_permission_test(args):
    """One-shot AppleScript probe to provoke the macOS Automation prompt.

    Run this once after installing the .app bundle so macOS asks the
    user "Lang-View wants to control Google Chrome — Allow?". Once
    granted, subtitle-watch's launchd agent inherits the permission.
    """
    import subprocess
    # Always tee to a known file so the user can inspect the result even
    # when launched detached via `open -a` (where stdout is lost).
    out_log = Path("/tmp/lv-chrome-permission-test.log")
    out_log.write_text("starting probe\n")
    def tee(msg):
        log.info("%s", msg)
        with out_log.open("a") as f:
            f.write(msg + "\n")
    tee("Probing Chrome via AppleScript — macOS should now pop a prompt.")
    tee("If you see 'Lang-View wants to control Google Chrome', click OK.")
    # Ask Chrome for window count — unconditional output regardless of
    # what's on the page. If Automation is denied, this errors instead of
    # silently returning empty.
    script = '''
tell application "Google Chrome"
  set winCount to count of windows
  set tabCount to 0
  repeat with w in windows
    set tabCount to tabCount + (count of tabs of w)
  end repeat
  return "windows=" & winCount & " tabs=" & tabCount
end tell
'''
    try:
        out = subprocess.check_output(["osascript", "-e", script],
                                       text=True, timeout=10.0,
                                       stderr=subprocess.STDOUT)
        tee(f"OK: {out.strip()}")
        tee("Automation permission appears to be granted to this bundle.")
    except subprocess.CalledProcessError as e:
        tee(f"osascript returned non-zero ({e.returncode}): "
            f"{e.output.strip() if e.output else '<no stderr>'}")
    except subprocess.TimeoutExpired:
        tee("osascript timed out — Automation prompt likely needs to be "
            "accepted (look for a dialog from macOS).")
    except FileNotFoundError:
        tee("osascript not on PATH; macOS only.")


def cmd_subtitle_watch(args):
    """Poll a Chrome tab's subtitle DOM via Apple Events.

    DRM-blocked players (Disney+, Netflix, …) hide their video pixels
    from every screen-capture API. But the player renders the subtitle
    cue as plain DOM text, which we can pull out via Chrome's built-in
    AppleScript ``execute javascript`` bridge. ``--pause-flag-file``
    lets the menubar's existing toggle pause this loop the same way it
    pauses the OCR ``watch`` loop.

    With ``--overlay`` we additionally inject an in-page HUD that shows
    the live subtitle, optional reading hints (furigana / romaja), an
    inline translation, and pause / save / lookup buttons. Click a word
    in the overlay to get a dictionary popup. The overlay shares the
    pause-flag file with the watch loop and the menubar, and writes
    saved subtitles to ``--bookmarks``.
    """
    from .subtitle_scrape import is_osascript_available, scrape_chrome_subtitle

    if not is_osascript_available():
        log.error("osascript not found; subtitle-watch requires macOS")
        sys.exit(2)

    storage = None
    if args.db:
        from .storage import Storage
        storage = Storage(args.db)

    out_file = None
    out_path = args.output if str(args.output) else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_file = out_path.open("a", encoding="utf-8")

    file_pause = None
    if args.pause_flag_file:
        from .pause import FilePauseFlag
        file_pause = FilePauseFlag(args.pause_flag_file)

    pipeline = None
    if args.enrich:
        from .enrich import build_pipeline
        pipeline = build_pipeline(
            args.enrich,
            dict_dir=args.dict_dir,
            translator_name=args.translator,
            translate_to=args.translate_to,
            api_key_env=args.translator_key_env,
        )
        log.info("Subtitle-watch enrichers: %s",
                 ", ".join(getattr(e, "name", type(e).__name__)
                           for e in pipeline.enrichers) or "(none)")

    overlay = None
    overlay_kakasi = None
    overlay_transliter = None
    overlay_dict = None
    bookmarks_file = None
    build_state = None
    if args.overlay:
        from .overlay import OverlayDriver, build_state as _build_state
        build_state = _build_state
        overlay = OverlayDriver(args.url_match)
        try:
            import pykakasi
            overlay_kakasi = pykakasi.kakasi()
        except ImportError:
            log.info("pykakasi not installed; overlay reading hints disabled "
                     "for Japanese (pip install 'lang-view[kana]').")
        try:
            from hangul_romanize import Transliter
            from hangul_romanize.rule import academic
            overlay_transliter = Transliter(academic)
        except ImportError:
            log.info("hangul-romanize not installed; overlay romaja disabled "
                     "for Korean (pip install 'lang-view[romaja]').")
        from .enrich.dictionary import DictionaryEnricher
        overlay_dict = DictionaryEnricher(dict_dir=args.dict_dir)
        if args.bookmarks:
            args.bookmarks.parent.mkdir(parents=True, exist_ok=True)
            bookmarks_file = args.bookmarks.open("a", encoding="utf-8")

    log.info("Subtitle-watch: url=%r selector=%r interval=%.2fs overlay=%s",
             args.url_match, args.selector, args.interval, bool(overlay))
    log.info("Chrome must have View > Developer > Allow JavaScript from "
             "Apple Events enabled.")

    last_text = ""
    last_record = None
    last_lang = args.lang_hint
    pending_lookup = None
    try:
        while True:
            paused = file_pause is not None and file_pause.is_paused()
            text = None if paused else scrape_chrome_subtitle(args.url_match, args.selector)

            if not text:
                last_text = ""  # reset so a re-appearing cue can re-fire
            elif text != last_text:
                last_text = text
                lang = classify(text, args.lang_hint) or args.lang_hint
                last_lang = lang
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "lang": lang,
                    "text": text,
                    "confidence": 1.0,
                    "bbox": [0, 0, 0, 0],
                    "app": "Chrome",
                }
                if pipeline is not None:
                    enrichment = pipeline.enrich(text, lang)
                    if enrichment:
                        record["enrichment"] = enrichment
                if out_file is not None:
                    out_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out_file.flush()
                if storage is not None:
                    storage.write(record)
                last_record = record
                log.info("[%s] %s", lang, text.replace("\n", " | "))

            if overlay is not None:
                state = build_state(
                    last_text or "",
                    last_lang,
                    enrichment=(last_record or {}).get("enrichment"),
                    paused=paused,
                    kakasi=overlay_kakasi,
                    transliter=overlay_transliter,
                    lookup=pending_lookup,
                )
                pending_lookup = None
                action = overlay.tick(state)
                if action:
                    pending_lookup = _handle_overlay_action(
                        action, last_record, last_lang,
                        file_pause=file_pause,
                        dict_enricher=overlay_dict,
                        bookmarks_file=bookmarks_file,
                    )
            time.sleep(args.interval)
    finally:
        if overlay is not None:
            try:
                overlay.teardown()
            except Exception as e:  # noqa: BLE001
                log.debug("overlay teardown failed: %s", e)
        if bookmarks_file is not None:
            bookmarks_file.close()
        if out_file is not None:
            out_file.close()
        if storage is not None:
            storage.close()


def _handle_overlay_action(action, last_record, last_lang, *,
                           file_pause, dict_enricher, bookmarks_file):
    """Apply one user-driven action from the overlay.

    Returns a ``lookup`` dict to be attached to the *next* state push,
    or None if no follow-up render is needed. Side effects (pause flip,
    bookmark append) happen here so the watch loop stays linear.
    """
    kind = action.get("type")
    if kind == "toggle_pause":
        if file_pause is None:
            log.info("overlay: pause requested but --pause-flag-file is unset")
            return None
        new_state = file_pause.toggle()
        log.info("overlay: pause %s", "ON" if new_state else "OFF")
        return None
    if kind == "save":
        if last_record is None:
            log.info("overlay: save requested but no subtitle has been captured yet")
            return None
        if bookmarks_file is None:
            log.info("overlay: save requested but --bookmarks is unset")
            return None
        bookmarks_file.write(json.dumps(last_record, ensure_ascii=False) + "\n")
        bookmarks_file.flush()
        log.info("overlay: bookmarked %r", last_record.get("text", ""))
        return None
    if kind == "lookup":
        word = (action.get("word") or "").strip()
        if not word or dict_enricher is None:
            return None
        hits = dict_enricher.lookup_token(word, last_lang)
        return {"word": word, "hits": list(hits)}
    log.debug("overlay: unknown action %r", action)
    return None


def cmd_menubar(args):
    from .menubar import MenubarApp, MenubarState
    pause_source = None
    if args.pause_flag_file:
        from .pause import FilePauseFlag
        pause_source = FilePauseFlag(args.pause_flag_file)
    state = MenubarState(pause_switch=pause_source)
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

    pkg = sub.add_parser("package-app",
                         help="Build a macOS .app bundle that wraps the watcher (for stable TCC permissions).")
    pkg.add_argument("--dest", type=Path, default=None,
                     help="Destination .app path (default: ~/Applications/Lang-View.app)")
    pkg.add_argument("--bundle-id", default="ai.lang-view")
    pkg.add_argument("--bundle-name", default="Lang-View")
    pkg.add_argument("--foreground", action="store_true",
                     help="Mark bundle as a foreground app (LSUIElement=false). "
                          "Required for macOS to show TCC/Automation prompts.")
    pkg.add_argument("--verbose", "-v", action="store_true")

    install = sub.add_parser("install-agent",
                             help="Install a launchd LaunchAgent (macOS).")
    install.add_argument("--label", default="ai.lang-view.watcher")
    install.add_argument("--plist", type=Path, default=None,
                         help="Path to write the plist (default: ~/Library/LaunchAgents/<label>.plist)")
    install.add_argument("--bundle", type=Path, default=None,
                         help="Path to a .app bundle from `lang-view package-app`. "
                              "When set, the agent runs the bundle's launcher so "
                              "macOS TCC attributes Screen Recording to the bundle id.")
    install.add_argument("--db", type=Path, default=None,
                         help="SQLite DB to pass to the watch command")
    install.add_argument("--output", type=Path, default=None,
                         help="JSONL output to pass to the watch command")
    install.add_argument("--log", type=Path, default=None,
                         help="stdout/stderr log file for the agent")
    # Watch-loop flags forwarded to the agent's `lang-view watch` invocation.
    install.add_argument("--lang", choices=("ko", "ja", "both"), default="both")
    install.add_argument("--engine", choices=("easyocr", "manga-ocr"), default="easyocr")
    install.add_argument("--gpu", action="store_true")
    install.add_argument("--interval", type=float, default=1.0)
    install.add_argument("--monitor", type=int, default=1)
    install.add_argument("--region", type=_parse_region, default=None,
                         help="Capture only this rect: x,y,w,h")
    install.add_argument("--active-window", action="store_true")
    install.add_argument("--visible-window", action="store_true")
    install.add_argument("--capture-window-app", default="")
    install.add_argument("--capture-window-title", default="")
    install.add_argument("--app-allow", default="")
    install.add_argument("--app-block", default="")
    install.add_argument("--title-allow", default="")
    install.add_argument("--title-block", default="")
    install.add_argument("--min-confidence", type=float, default=0.4)
    install.add_argument("--dedup-seconds", type=float, default=60.0)
    install.add_argument("--group-lines", action="store_true")
    install.add_argument("--snippets-dir", type=Path, default=None)
    install.add_argument("--frames-dir", type=Path, default=None)
    install.add_argument("--encrypt-key-env", default=None)
    install.add_argument("--pause-flag-file", type=Path, default=None,
                         help="Path of the menubar's pause flag file")
    install.add_argument("--verbose", "-v", action="store_true")

    uninstall = sub.add_parser("uninstall-agent",
                               help="Unload and remove the LaunchAgent (macOS).")
    uninstall.add_argument("--label", default="ai.lang-view.watcher")
    uninstall.add_argument("--verbose", "-v", action="store_true")

    inst_sub = sub.add_parser("install-subtitle-agent",
                              help="Install a launchd LaunchAgent that runs subtitle-watch.")
    inst_sub.add_argument("--label", default="ai.lang-view.subtitle")
    inst_sub.add_argument("--plist", type=Path, default=None)
    inst_sub.add_argument("--bundle", type=Path, default=None,
                          help="Path to a .app bundle from `lang-view package-app`.")
    inst_sub.add_argument("--db", type=Path, default=None)
    inst_sub.add_argument("--output", type=Path, default=None)
    inst_sub.add_argument("--log", type=Path, default=None)
    inst_sub.add_argument("--url-match", default="disneyplus.com/play")
    inst_sub.add_argument("--selector", default=".hive-subtitle-renderer-wrapper")
    inst_sub.add_argument("--interval", type=float, default=1.0)
    inst_sub.add_argument("--lang-hint", choices=("ko", "ja"), default="ko")
    inst_sub.add_argument("--pause-flag-file", type=Path, default=None)
    inst_sub.add_argument("--enrich", default="",
                          help="Same syntax as `subtitle-watch --enrich`.")
    inst_sub.add_argument("--translator", default="none",
                          choices=("none", "argos", "deepl", "openai"))
    inst_sub.add_argument("--translate-to", default="en")
    inst_sub.add_argument("--translator-key-env", default=None)
    inst_sub.add_argument("--dict-dir", type=Path, default=None)
    inst_sub.add_argument("--overlay", action="store_true",
                          help="Inject the in-page HUD over the player.")
    inst_sub.add_argument("--bookmarks", type=Path, default=None,
                          help="JSONL path the overlay's Save button appends to.")
    inst_sub.add_argument("--verbose", "-v", action="store_true")

    uninst_sub = sub.add_parser("uninstall-subtitle-agent",
                                help="Unload and remove the subtitle LaunchAgent.")
    uninst_sub.add_argument("--label", default="ai.lang-view.subtitle")
    uninst_sub.add_argument("--verbose", "-v", action="store_true")

    inst_mb = sub.add_parser("install-menubar-agent",
                             help="Install a launchd LaunchAgent that auto-starts the menubar at login.")
    inst_mb.add_argument("--label", default="ai.lang-view.menubar")
    inst_mb.add_argument("--plist", type=Path, default=None)
    inst_mb.add_argument("--bundle", type=Path, default=None,
                         help="Path to a .app bundle from `lang-view package-app`.")
    inst_mb.add_argument("--log", type=Path, default=None,
                         help="stdout/stderr log file for the menubar agent")
    inst_mb.add_argument("--pause-flag-file", type=Path, default=None,
                         help="Path of the pause flag file shared with the watch agent")
    inst_mb.add_argument("--recent", type=Path, default=None,
                         help="Seed the menubar's recent list from this SQLite DB")
    inst_mb.add_argument("--verbose", "-v", action="store_true")

    uninst_mb = sub.add_parser("uninstall-menubar-agent",
                               help="Unload and remove the menubar LaunchAgent (macOS).")
    uninst_mb.add_argument("--label", default="ai.lang-view.menubar")
    uninst_mb.add_argument("--verbose", "-v", action="store_true")

    dashboard = sub.add_parser("dashboard",
                               help="Serve a small web UI for the SQLite store.")
    dashboard.add_argument("--db", type=Path, required=True)
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=7321)
    dashboard.add_argument("--verbose", "-v", action="store_true")

    cpt = sub.add_parser("chrome-permission-test",
                         help="Provoke macOS Automation prompt for the .app bundle "
                              "(run once via the bundle launcher to grant permission).")
    cpt.add_argument("--verbose", "-v", action="store_true")

    sw = sub.add_parser("subtitle-watch",
                        help="Poll a Chrome tab's subtitle DOM via Apple Events "
                             "(works on DRM-blocked players like Disney+/Netflix).")
    sw.add_argument("--url-match", default="disneyplus.com/play",
                    help="Substring matched against tab URLs (default: disneyplus.com/play)")
    sw.add_argument("--selector", default=".hive-subtitle-renderer-wrapper",
                    help="CSS selector of the subtitle text node "
                         "(Disney+ default; Netflix uses .player-timedtext)")
    sw.add_argument("--interval", type=float, default=1.0,
                    help="Seconds between polls (default: 1.0)")
    sw.add_argument("--db", type=Path, default=None,
                    help="SQLite DB to write captures to")
    sw.add_argument("--output", type=Path, default=Path(""),
                    help="JSONL output (set '' to disable)")
    sw.add_argument("--lang-hint", choices=("ko", "ja"), default="ko",
                    help="Fallback language label when text_filter.classify "
                         "is ambiguous (e.g. CJK ideograph-only)")
    sw.add_argument("--pause-flag-file", type=Path, default=None,
                    help="Pause when this file exists (shared with the menubar)")
    sw.add_argument("--enrich", default="",
                    help="Comma-separated enrichers to run on each subtitle "
                         "(furigana,romaji,romaja,dict,translate). Same syntax "
                         "as `watch --enrich`.")
    sw.add_argument("--translator", default="none",
                    choices=("none", "argos", "deepl", "openai"))
    sw.add_argument("--translate-to", default="en")
    sw.add_argument("--translator-key-env", default=None,
                    help="Env var holding the translator API key, if needed")
    sw.add_argument("--dict-dir", type=Path, default=None,
                    help="Directory holding dictionaries (jmdict-eng.json, "
                         "kodict.tsv). Used for the overlay's click-to-define "
                         "popup as well as the `dict` enricher.")
    sw.add_argument("--overlay", action="store_true",
                    help="Inject an in-page HUD over the player showing the "
                         "live subtitle, reading hints, translation and quick "
                         "action buttons. Requires the same Chrome Apple Events "
                         "permission that subtitle-watch already needs.")
    sw.add_argument("--bookmarks", type=Path, default=None,
                    help="JSONL path to append saved subtitles to when the "
                         "overlay's Save button is clicked. Storage stays "
                         "append-only — bookmarks live in their own file.")
    sw.add_argument("--verbose", "-v", action="store_true")

    menubar = sub.add_parser("menubar",
                             help="Run the macOS menubar app (rumps).")
    menubar.add_argument("--recent", type=Path, default=None,
                         help="Seed the recent list from this SQLite DB")
    menubar.add_argument("--pause-flag-file", type=Path, default=None,
                         help="Path to the pause flag file shared with the watch agent. "
                              "Click Pause to touch the file, click Resume to delete it.")
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
        "package-app": cmd_package_app,
        "install-agent": cmd_install_agent,
        "uninstall-agent": cmd_uninstall_agent,
        "install-menubar-agent": cmd_install_menubar_agent,
        "uninstall-menubar-agent": cmd_uninstall_menubar_agent,
        "install-subtitle-agent": cmd_install_subtitle_agent,
        "uninstall-subtitle-agent": cmd_uninstall_subtitle_agent,
        "dashboard": cmd_dashboard,
        "menubar": cmd_menubar,
        "subtitle-watch": cmd_subtitle_watch,
        "chrome-permission-test": cmd_chrome_permission_test,
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
