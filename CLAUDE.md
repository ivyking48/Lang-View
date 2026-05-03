# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup and commands

Editable install (use a per-project conda env, not base):

```
conda create -n lang-view python=3.11 -y && conda activate lang-view
pip install -e ".[dev]"  # core + pytest
pip install -e ".[dev,kana,romaja,snippets,encrypt,macos,menubar,dashboard]"  # full
```

`numpy<2` may be required when torch's wheel hasn't caught up; the install path will surface this loudly.

Optional extras are split per feature (`kana`, `romaja`, `mangaocr`, `deepl`, `openai`, `argos`, `snippets`, `encrypt`, `macos`, `menubar`, `dashboard`). Each `from .xxx import ...` for an optional feature is done lazily inside command handlers, so missing extras only break the subcommand that needs them.

Run the CLI:

```
lang-view                                  # `watch` is the implicit default
lang-view watch --db ~/lv.db --output ''   # disable JSONL with empty --output
lang-view subtitle-watch --db ~/lv.db      # DOM scrape Disney+/Netflix via AppleScript
lang-view subtitle-watch --overlay --enrich furigana,romaji,dict,translate \
                         --bookmarks ~/lv-bookmarks.jsonl \
                         --pause-flag-file ~/.local/share/lang-view/paused.flag
lang-view search "안녕" --db ~/lv.db
lang-view stats   --db ~/lv.db
lang-view export  --db ~/lv.db --output out.csv  --format csv
lang-view export  --db ~/lv.db --output out.tsv  --format anki
lang-view dashboard --db ~/lv.db           # Flask UI on 127.0.0.1:7321
lang-view menubar --pause-flag-file ~/.local/share/lang-view/paused.flag --recent ~/lv.db
lang-view package-app --foreground         # macOS .app bundle for stable TCC
lang-view chrome-permission-test           # provoke macOS Automation prompt
lang-view install-agent --bundle /Applications/Lang-View.app --db ~/lv.db ...
lang-view install-menubar-agent --bundle /Applications/Lang-View.app --pause-flag-file ...
lang-view install-subtitle-agent --bundle /Applications/Lang-View.app --url-match disneyplus.com/play ...
lang-view uninstall-agent / uninstall-menubar-agent / uninstall-subtitle-agent
lang-view keygen                           # Fernet key for --encrypt-key-env
```

Tests (pytest, headless):

```
pytest                          # whole suite — ~250 tests, runs in ~2s
pytest tests/test_storage.py    # one file
pytest tests/test_storage.py::test_search_substring -v
```

Most tests stub or monkey-patch the OCR engine; `easyocr`, `mss`, `numpy` are the only mandatory runtime deps.

## Architecture

Two pipelines now share the same `Storage` and pause/menubar UI:

### Pipeline A: OCR watch loop (non-DRM screen content)

Reading top-to-bottom in `lang_view/__main__.py:cmd_watch`:

```
mss.grab → optional crop (--region / --active-window)
        → optional --visible-window gate (any visible Chrome/Safari)
        → frame_thumbnail → frames_similar gate (idle skip)
        → AsyncOCRWorker.submit (max-1 queue, drops stale)
        ↓ (background thread)
   engines_run → group_lines? → on_detections
        → text_filter.classify (hangul/kana/CJK → ko|ja|None)
        → _build_record (conf floor, bbox normalisation, frame_path)
        → TimeWindowDedup.check_and_add
        → optional enrichment (furigana / romaji / romaja / dict / translate)
        → JSONL append (optional Fernet envelope) + Storage.write (SQLite)
        → optional snippet PNG (cropped bbox) + frame PNG (full capture)
```

### Pipeline B: subtitle-watch (DRM-protected players)

`lang_view/__main__.py:cmd_subtitle_watch`:

```
osascript → tell app "Google Chrome" → execute t javascript "document.querySelector(...)?.innerText"
        → for each unique non-empty subtitle (last_text dedup, reset on blank)
        → text_filter.classify (fall back to --lang-hint)
        → _build_record (conf=1.0, app="Chrome")
        → optional EnrichmentPipeline (furigana / romaji / romaja / dict / translate)
        → JSONL + Storage.write
        → optional OverlayDriver.tick (push state, pop user action)
                → toggle_pause flips FilePauseFlag
                → save appends to bookmarks.jsonl
                → lookup runs DictionaryEnricher.lookup_token, returned in next push
```

Both pipelines respect the same `FilePauseFlag`. The menubar app touches/removes the flag file; both watchers poll it each cycle. With `--overlay`, the in-page HUD's Pause button drives the *same* flag file, so menubar/HUD/external `touch` are all equivalent.

### Pipeline B.1: in-page overlay (`--overlay`)

`lang_view/overlay.py` injects a singleton `<div id="lang-view-overlay">` into the same Chrome tab that `subtitle_scrape` reads from, via the same Apple Events JS bridge. Three JS payloads:

- `bootstrap_js()` — idempotent installer. Re-evaluated every `rebootstrap_every` ticks (default 30) so SPA navigation in the player (next-episode auto-advance, profile switch) re-mounts the overlay on the new document.
- `update_js(state)` — pushes a JSON state object: tokenized words (orig + reading + romaji), translation, paused flag, optional dictionary lookup result. JSON is embedded as a JS literal; AppleScript-quoting handles the outer string escape.
- `pop_action_js()` — returns the pending user action as a JSON string, then clears it. Action shapes: `{type:"toggle_pause"}`, `{type:"save"}`, `{type:"lookup", word}`.

Translation- and reading-visibility toggles stay client-side (CSS data-attribute flips on the root) to avoid a 2× osascript round trip per click. Only state-changing buttons (pause / save) and dictionary lookups round-trip to Python.

The overlay's container is `position:fixed; bottom:14vh; z-index:2147483647`, sandbox-styled with `lang-view-*` IDs. `pointer-events:auto` only on the container itself; the `<video>` underneath stays clickable for play/pause/seek through the negative space around the HUD.

### Why overlay is in-page (not a native NSWindow)

A native transparent NSWindow over the Chrome process would work everywhere but introduces three problems we don't want: (a) it needs Accessibility permission to track Chrome's window geometry through Spaces and full-screen, (b) tracking the player's bottom-center under macOS's full-screen animation is racy, and (c) click-through carve-outs require window-level event hit-testing. The DOM overlay piggybacks on the Apple Events JS bridge that subtitle-watch already needs, so it costs zero new permissions and follows the player's geometry for free (the player resizes the DOM, we re-read its bounding box).

### Why two pipelines

macOS HDCP/Widevine enforcement substitutes blanked pixels at the compositor level for DRM-protected video (Disney+, Netflix). Every screen-recording API — `mss`, `CGWindowListCreateImage`, ScreenCaptureKit — gets the substitution, regardless of TCC tier or code-signing trust. The OCR pipeline can capture *anything else* on screen but is blind to DRM video frames. The subtitle-scrape pipeline reads subtitles from DOM (rendered for accessibility tools, not in the protected canvas), bypassing this entirely.

### Key invariants

- **One screenshot per cycle, at most one OCR call per visible change.** The thumbnail similarity gate (`frames.frames_similar`) is what makes idle screens cheap; do not bypass it. The `AsyncOCRWorker` enforces a max-1 queue depth so a slow OCR cycle never accumulates a backlog — instead old frames are dropped and `worker.dropped` is reported on shutdown.
- **Detections flow through `_build_record` exactly once.** That function decides whether a detection is kept (confidence threshold + `text_filter.classify`) and is the only place language is assigned. CJK ideograph-only text is ambiguous and falls back to the OCR engine's `lang_hint`.
- **Frame paths are written once per OCR cycle, attached to every kept detection in that batch.** `FrameWriter` time-debounces (default 100 ms) so the second engine's call doesn't write a duplicate PNG. The path lives on the SQLite row's `frame` column AND in the JSONL — so any record can be reproduced from disk.
- **Storage is append-only.** `lang_view/storage.py` deliberately has no update/delete API. Captures are facts. The schema gains columns over time via a small `_migrate` step that runs `ALTER TABLE ADD COLUMN` if absent — old DBs continue to work without backfill.
- **Storage is cross-thread.** SQLite connection uses `check_same_thread=False`. The watch loop creates `Storage` on the main thread and the OCR worker writes from a background thread; SQLite serialises writes via the connection lock, which is fine because there is exactly one writer process.
- **Search uses `LIKE`, not FTS5.** The default FTS5 tokenizer fails on CJK because there is no whitespace — see the docstring on `Storage.search`. If you're tempted to add FTS5 back, you need a CJK tokenizer (icu/jieba/etc.), not a config tweak.
- **Encryption is a per-line envelope.** When `--encrypt-key-env LV_KEY` is set, JSONL lines become `{"v":1,"ct":"<fernet>"}`. `_iter_jsonl` transparently decrypts when a cipher is supplied. SQLite rows are stored in cleartext — encrypt the disk if that matters.
- **Overlay actions are sourced through the JS bridge, never out-of-band.** The overlay never opens a socket or a side channel back to Python. Every user click sets `pendingAction` in-page; Python pops it via `JSON.stringify(window.__langView.popAction() || null)` on the next tick. This keeps the trust boundary identical to the existing `subtitle-watch` permission model — if Chrome's "Allow JavaScript from Apple Events" is granted, both reads and writes go through it; if not, neither side works.
- **Overlay bookmarks live in their own JSONL, not in `Storage`.** Save action appends the *current* capture record to `--bookmarks` and flushes. Storage stays append-only and bookmark-agnostic; if you later add a bookmarks view to the dashboard, read from the JSONL — don't mutate captures.
- **Overlay tokenization shares the kakasi/transliter instances with the enrichers.** `KanaEnricher` and the overlay's per-word splitter both want pykakasi loaded; we instantiate one of each in `cmd_subtitle_watch` and pass them into both code paths. Don't construct a second kakasi — the dictionary load alone is ~150 ms.

### Engines

`lang_view/engines.py` exposes a uniform `read(frame_bgr) → [(bbox, text, conf)]` shape that matches EasyOCR's native return so the rest of the pipeline is engine-agnostic. `MangaOCREngine` does no detection — it returns one detection covering the whole frame, so combine it with `--region` or `--active-window` for usable bounding boxes.

`build_engines("both", ...)` returns a list, one engine per requested language. Each engine has a `lang_hint` that is the last-resort tie-breaker for ambiguous CJK ideographs.

### Enrichment

`lang_view/enrich/__init__.py:build_pipeline` parses a comma-separated spec (`furigana,romaji,romaja,dict,translate`) and constructs each enricher lazily. The `EnrichmentPipeline` swallows per-enricher exceptions — an enrichment failure must never block the underlying record from being written.

If both `furigana` and `romaji` are in the spec, they share a single `KanaEnricher` (deduped by class name in `build_pipeline`) — keep that dedup if you add new kana-related tokens.

### Filtering: app, title, visibility

`lang_view/app_filter.py` is a generic substring matcher reused for both `--app-allow / --app-block` (against frontmost app name) and `--title-allow / --title-block` (against the active window title). `lang_view/macos.py:get_active_window` populates `ActiveWindow.title` (Quartz `kCGWindowName`) and `ActiveWindow.window_id` (CGWindowID).

Three modes:
- *neither flag* — OCR every frame.
- `--active-window` — gate by frontmost app/title; capture only that window's region.
- `--visible-window` — gate by *any* visible window's app/title; OCR full screen (no crop). Use this for side-by-side layouts.

There's a known macOS quirk that bites launchd-spawned processes: `kCGWindowName` returns blank for windows owned by other apps, *even when Screen Recording is granted*. The visible-window path detects "all candidate windows have empty titles" and falls through to app-only matching, otherwise the agent would never match (see comment in `cmd_watch`).

### Pause coordination (cross-process)

`lang_view/pause.py:FilePauseFlag` — touch a path to pause, delete to resume. The watch loop and subtitle-watch loop check this every cycle. The menubar (running as its own process) flips the flag in response to clicks, and polls every 2 s to keep the title icon (`▶ 語` / `⏸ 語`) accurate when the flag is changed externally.

### macOS .app bundle (TCC stability)

`lang_view/bundle.py` generates a minimal `.app` (Info.plist + bash launcher) so macOS TCC can attribute Screen Recording / Automation to a stable `CFBundleIdentifier=ai.lang-view`. Without a bundle, an unsigned `python3.11` interpreter spawned by launchd has unstable TCC identity — permissions get silently dropped or denied.

The launcher does **not** `exec` python; it spawns python as a child and `wait`s. `exec` would replace the running process image and macOS would attribute TCC to the unsigned python binary instead of the bundle. The trap forwards SIGTERM/INT for clean launchd `bootout`.

The bundle is ad-hoc-signed via `codesign --force --deep --sign -` and registered with Launch Services via `lsregister`. `--foreground` flips `LSUIElement=false` so the bundle is treated as a foreground app, which is required for macOS to *show* an Automation prompt (background-only bundles get silent denial).

`install-agent`, `install-menubar-agent`, `install-subtitle-agent` all accept `--bundle <path>` and use the bundle's launcher as `ProgramArguments[0]` so the launchd-spawned process inherits the bundle's TCC identity.

### Subtitle scraping (DRM-blind)

`lang_view/subtitle_scrape.py` shells out to `osascript` with a tiny AppleScript that iterates Chrome windows/tabs, matches a URL substring, and runs `execute t javascript "<expr>"` against the matched tab. Default selector is `.hive-subtitle-renderer-wrapper` (Disney+); Netflix is `.player-timedtext`.

Two prerequisites — both surfaced via `chrome-permission-test`:
1. **Chrome:** `View → Developer → Allow JavaScript from Apple Events` (one-time toggle).
2. **macOS:** Automation grant for `ai.lang-view → Google Chrome`. Provoked by running `chrome-permission-test` once via the bundle (`open -a Lang-View --args chrome-permission-test`). The first request from the foreground bundle pops the dialog; user accepts; later launchd-spawned bundles inherit the grant.

`lang_view/overlay.py` reuses the *same* bridge — it just runs `execute t javascript` for write operations (DOM injection, state updates) instead of read operations. No new permissions; the same Apple Events grant covers both directions.

### Dashboard

`lang_view/dashboard.py` is a tiny Flask read-only UI. It opens a fresh `Storage` per request so a concurrent `watch` process can keep writing while you browse. Don't share a Storage handle across requests — SQLite + threads will bite.

The `/artifact?path=...` route serves snippet/frame PNGs but only if the resolved path lives under one of `app.config["ARTIFACT_ROOTS"]` — directory traversal is blocked. Default roots are the `frames/` and `snippets/` directories alongside the DB. HTML rows render `frame · snippet` links that open the PNG in a new tab.

### macOS integration files (non-bundle)

- `lang_view/macos.py` — `get_active_window` (Quartz first, osascript fallback) and `get_visible_windows` (CGWindowList). Also `capture_window_image` (CGWindowListCreateImage) — note this **fails for DRM-protected windows** even with TCC-Screen-Recording, by macOS HDCP design.
- `lang_view/launchd.py` — pure plist generator (`render_plist`) plus shelling helpers for `launchctl bootstrap`/`bootout`. The plist generator is the unit-testable surface.
- `lang_view/menubar.py` — `MenubarState` (plain Python, tested) and `MenubarApp` (rumps, lazy import). The state's `pause_switch` parameter accepts anything with `is_paused()` / `toggle()` — `PauseSwitch` (in-process, SIGUSR1) and `FilePauseFlag` (cross-process) both fit.

## macOS permissions cheat sheet

| Capability | Requirement | Notes |
|---|---|---|
| `mss.grab` (full screen) | Screen Recording | Granted to the binary; survives launchd spawn under the .app bundle. |
| `CGWindowListCreateImage` (per-window pixels) | Screen Recording | Same gate, but **fails on DRM-protected video** regardless of grant. |
| `kCGWindowName` (window titles) | Screen Recording | Often blank for windows owned by other apps in launchd context. |
| `--active-window` | Quartz (no Accessibility needed) | Falls back to osascript only if Quartz import fails — install `pyobjc-framework-Quartz`. |
| `subtitle-watch` (osascript JS injection) | Chrome's "Allow JS from Apple Events" + macOS Automation grant | Both steps required; `chrome-permission-test` provokes the prompt. |
| `subtitle-watch --overlay` (in-page HUD) | Same as above | Reuses the same Apple Events grant — no extra permissions. |

## Project context (parent workspace)

This repo lives under `/Users/ivanacoronado/Projects/`, which has its own top-level `CLAUDE.md` describing the multi-project workspace. Lang-View is one of several Python projects there; its own conventions in this file take precedence inside this directory.
