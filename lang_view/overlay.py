"""In-page overlay for the subtitle-watch loop.

DRM-protected players (Disney+, Netflix) hide their video pixels from
every macOS screen-recording API, but the subtitle text is plain DOM —
that's why ``subtitle_scrape.py`` works at all. The same Apple Events
``execute t javascript`` bridge can also *write* into the page, so we
inject a singleton ``<div id="lang-view-overlay">`` that sits above the
player and renders:

  - the live subtitle, tokenized into clickable words
  - optional reading hints (furigana for ja, romaja for ko)
  - an inline translation row (toggleable)
  - pause / save / mode-toggle buttons

Communication is one-way per call. Each tick we send two scripts:

  1. ``pop_action_js`` — read & clear any pending user click
  2. ``update_js``     — push the latest state object into the overlay

Word lookups are a tiny round trip: the user clicks a word; that queues
a ``{type: "lookup", word}`` action; Python pops it next tick, runs it
against the dictionary index, and embeds the result in the next state
push so the overlay can render the popup.

Translation-visibility and reading-visibility toggles never leave the
page — they're CSS class flips on the overlay root. We keep those
client-side to avoid a 2x ``osascript`` round trip per click.

The module is structured so the JS payloads, the state builder, and the
tokenizers are all pure functions. ``OverlayDriver`` is the only
stateful piece, and it accepts a runner callable for tests.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from .subtitle_scrape import render_applescript

log = logging.getLogger(__name__)


_OVERLAY_ROOT_ID = "lang-view-overlay"


_BOOTSTRAP_JS = r"""
(function(){
  if (window.__langView && document.getElementById('lang-view-overlay')) return;
  var ROOT_ID = 'lang-view-overlay';
  var existing = document.getElementById(ROOT_ID);
  if (existing) existing.remove();

  var root = document.createElement('div');
  root.id = ROOT_ID;
  root.setAttribute('data-show-translation', '1');
  root.setAttribute('data-show-reading', '1');
  root.style.cssText = [
    'position:fixed','left:50%','bottom:14vh','transform:translateX(-50%)',
    'z-index:2147483647','max-width:80vw','min-width:32vw',
    'background:rgba(12,14,18,0.82)','color:#f5f5f7',
    'font:500 18px/1.4 -apple-system,BlinkMacSystemFont,sans-serif',
    'padding:10px 14px','border-radius:10px',
    'box-shadow:0 4px 24px rgba(0,0,0,0.5)',
    'pointer-events:auto','user-select:none',
    'backdrop-filter:blur(8px)','-webkit-backdrop-filter:blur(8px)'
  ].join(';');
  root.innerHTML = (
    '<style>'+
    '#'+ROOT_ID+' .lv-row{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:baseline}'+
    '#'+ROOT_ID+' .lv-word{cursor:pointer;padding:2px 4px;border-radius:4px;'+
      'transition:background 80ms ease}'+
    '#'+ROOT_ID+' .lv-word:hover{background:rgba(255,255,255,0.16)}'+
    '#'+ROOT_ID+' .lv-reading{display:block;font-size:11px;opacity:0.7;'+
      'line-height:1;margin-bottom:1px;text-align:center}'+
    '#'+ROOT_ID+'[data-show-reading="0"] .lv-reading{display:none}'+
    '#'+ROOT_ID+' .lv-translation{margin-top:6px;padding-top:6px;'+
      'border-top:1px solid rgba(255,255,255,0.14);font-size:14px;opacity:0.86}'+
    '#'+ROOT_ID+'[data-show-translation="0"] .lv-translation{display:none}'+
    '#'+ROOT_ID+' .lv-controls{margin-top:8px;display:flex;gap:6px;'+
      'justify-content:flex-end}'+
    '#'+ROOT_ID+' button.lv-btn{appearance:none;border:0;'+
      'background:rgba(255,255,255,0.08);color:#f5f5f7;'+
      'padding:4px 10px;border-radius:6px;font:inherit;font-size:12px;'+
      'cursor:pointer}'+
    '#'+ROOT_ID+' button.lv-btn:hover{background:rgba(255,255,255,0.18)}'+
    '#'+ROOT_ID+' .lv-lookup{position:absolute;left:50%;'+
      'transform:translateX(-50%);bottom:calc(100% + 6px);'+
      'background:#1c1f25;color:#f5f5f7;padding:8px 10px;border-radius:8px;'+
      'font-size:13px;max-width:60vw;box-shadow:0 4px 16px rgba(0,0,0,0.5);'+
      'pointer-events:auto}'+
    '#'+ROOT_ID+' .lv-lookup b{color:#9ad0ff}'+
    '#'+ROOT_ID+' .lv-empty{opacity:0.55;font-style:italic}'+
    '</style>'+
    '<div class="lv-subtitle"></div>'+
    '<div class="lv-translation"></div>'+
    '<div class="lv-controls">'+
      '<button class="lv-btn" data-act="reading">Reading</button>'+
      '<button class="lv-btn" data-act="translation">Translation</button>'+
      '<button class="lv-btn" data-act="save">Save</button>'+
      '<button class="lv-btn" data-act="pause">Pause</button>'+
    '</div>'
  );
  document.body.appendChild(root);

  var pendingAction = null;
  var lastLookupWord = null;

  function clearLookup(){
    var l = root.querySelector('.lv-lookup');
    if (l) l.remove();
    lastLookupWord = null;
  }

  root.addEventListener('click', function(ev){
    var t = ev.target;
    if (t.classList && t.classList.contains('lv-word')) {
      var w = t.getAttribute('data-word') || '';
      if (w === lastLookupWord) { clearLookup(); return; }
      pendingAction = {type:'lookup', word:w};
      lastLookupWord = w;
      return;
    }
    if (t.tagName === 'BUTTON' && t.dataset.act) {
      var act = t.dataset.act;
      if (act === 'reading') {
        var s = root.getAttribute('data-show-reading') === '1' ? '0' : '1';
        root.setAttribute('data-show-reading', s);
      } else if (act === 'translation') {
        var s2 = root.getAttribute('data-show-translation') === '1' ? '0' : '1';
        root.setAttribute('data-show-translation', s2);
      } else if (act === 'pause') {
        pendingAction = {type:'toggle_pause'};
      } else if (act === 'save') {
        pendingAction = {type:'save'};
      }
    }
  });

  function renderSubtitle(state){
    var host = root.querySelector('.lv-subtitle');
    host.innerHTML = '';
    var words = state.words || [];
    if (!words.length){
      host.innerHTML = '<span class="lv-empty">…</span>';
      return;
    }
    var row = document.createElement('div');
    row.className = 'lv-row';
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      var span = document.createElement('span');
      span.className = 'lv-word';
      span.setAttribute('data-word', w.orig || '');
      var reading = w.reading || w.romaji || '';
      if (reading && reading !== w.orig) {
        var r = document.createElement('span');
        r.className = 'lv-reading';
        r.textContent = reading;
        span.appendChild(r);
      }
      var o = document.createElement('span');
      o.textContent = w.orig || '';
      span.appendChild(o);
      row.appendChild(span);
    }
    host.appendChild(row);
  }

  function renderTranslation(state){
    var t = root.querySelector('.lv-translation');
    t.textContent = state.translation || '';
  }

  function renderLookup(state){
    clearLookup();
    var lk = state.lookup;
    if (!lk || !lk.word) return;
    var span = root.querySelector('.lv-word[data-word="' +
      (lk.word + '').replace(/"/g,'\\"') + '"]');
    if (!span) return;
    span.style.position = 'relative';
    var pop = document.createElement('div');
    pop.className = 'lv-lookup';
    var html = '<b>' + (lk.reading || lk.word) + '</b><br>';
    var hits = lk.hits || [];
    if (!hits.length) html += '<span class="lv-empty">no dictionary match</span>';
    else html += hits.slice(0,3).map(function(h){
      return (h.meanings || []).slice(0,3).join('; ');
    }).join('<br>');
    pop.innerHTML = html;
    span.appendChild(pop);
    lastLookupWord = lk.word;
  }

  function renderPause(state){
    var btn = root.querySelector('button[data-act="pause"]');
    if (btn) btn.textContent = state.paused ? 'Resume' : 'Pause';
  }

  window.__langView = {
    update: function(state){
      try {
        renderSubtitle(state || {});
        renderTranslation(state || {});
        renderLookup(state || {});
        renderPause(state || {});
      } catch (e) { /* never throw into Apple Events bridge */ }
    },
    popAction: function(){
      var a = pendingAction;
      pendingAction = null;
      return a;
    },
    teardown: function(){
      try {
        var n = document.getElementById(ROOT_ID);
        if (n) n.remove();
      } catch (e) {}
      delete window.__langView;
    }
  };
})();
""".strip()


def bootstrap_js() -> str:
    """Idempotent JS that installs ``window.__langView`` and the overlay div.

    Safe to evaluate every tick: the guard at the top short-circuits if
    the overlay is already present. We re-run it periodically anyway so
    SPA navigation (Disney+ next-episode auto-advance, Netflix profile
    switch) re-mounts the overlay on the new document.
    """
    return _BOOTSTRAP_JS


def teardown_js() -> str:
    """JS to remove the overlay node and forget the global."""
    return "(window.__langView && window.__langView.teardown && window.__langView.teardown())||0"


def update_js(state: dict) -> str:
    """JS expression that pushes a state object into the overlay.

    State JSON is embedded directly as a JS literal — JSON is a strict
    subset of JS so no extra escaping is needed beyond the AppleScript
    string-escaping that ``render_applescript`` does for us.
    """
    payload = json.dumps(state, ensure_ascii=False)
    return ("(window.__langView && window.__langView.update("
            + payload + "))||0")


def pop_action_js() -> str:
    """JS expression that returns the pending action (or null) as JSON."""
    return ("JSON.stringify((window.__langView && "
            "window.__langView.popAction()) || null)")


def parse_action(raw: Optional[str]) -> Optional[dict]:
    """Parse the string returned by the popAction JS evaluation.

    Returns None for "null", missing output, or anything that doesn't
    decode as a JSON object — we never want a malformed bridge response
    to surface as an exception in the watch loop.
    """
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if not obj.get("type"):
        return None
    return obj


def tokenize_japanese(text: str, kakasi) -> list[dict]:
    """Split Japanese text into per-word tokens with reading + romaji.

    Empty or whitespace-only segments are dropped. The ``kakasi`` arg is
    a ``pykakasi.kakasi()`` instance — passed in so callers can share
    one with the existing ``KanaEnricher`` rather than spinning up a
    second copy of the dictionary.
    """
    if not text or kakasi is None:
        return [{"orig": text, "reading": "", "romaji": ""}] if text else []
    segments = kakasi.convert(text)
    out = []
    for s in segments:
        orig = (s.get("orig") or "").strip()
        if not orig:
            continue
        out.append({
            "orig": orig,
            "reading": s.get("hira") or "",
            "romaji": (s.get("hepburn") or "").strip(),
        })
    return out


def tokenize_korean(text: str, transliter) -> list[dict]:
    """Split Korean text into eojeol with romaja per word.

    Whitespace-separated; punctuation stays attached to the word it sits
    against (the romanizer handles non-Hangul characters by passing them
    through). If ``transliter`` is None the romaja field is left blank
    rather than failing.
    """
    if not text:
        return []
    out = []
    for word in text.split():
        romaja = ""
        if transliter is not None:
            try:
                romaja = transliter.translit(word).strip()
            except Exception as e:  # noqa: BLE001
                log.debug("romaja failed for %r: %s", word, e)
        out.append({"orig": word, "reading": "", "romaji": romaja})
    return out


def build_state(text: str, lang: str, *, enrichment: dict | None = None,
                paused: bool = False, kakasi=None, transliter=None,
                lookup: dict | None = None) -> dict:
    """Assemble the JSON state object for the overlay.

    The state shape is intentionally flat and JSON-clean: no Python-only
    types leak through. Word tokens are derived per-language; the
    translation string is pulled from enrichment if present.
    """
    if lang == "ja":
        words = tokenize_japanese(text, kakasi)
    elif lang == "ko":
        words = tokenize_korean(text, transliter)
    else:
        words = [{"orig": text, "reading": "", "romaji": ""}] if text else []

    translation = ""
    if enrichment and isinstance(enrichment.get("translation"), dict):
        # pick the first available target lang
        for v in enrichment["translation"].values():
            if v:
                translation = v
                break
    state = {
        "text": text,
        "lang": lang,
        "words": words,
        "translation": translation,
        "paused": bool(paused),
    }
    if lookup:
        state["lookup"] = lookup
    return state


class OverlayDriver:
    """Drives the in-page overlay over the existing osascript bridge.

    One driver per subtitle-watch loop. ``tick()`` is called once per
    polling cycle with the current subtitle state; it bootstraps the
    overlay on first call (and re-bootstraps periodically so SPA
    navigation doesn't leave us with a stale empty page), pops any
    pending user action, and pushes the latest state.

    Returns the list of actions popped this tick — the caller decides
    what to do with them (toggle pause flag, save bookmark, look up
    word, …). We deliberately don't make any of those side effects
    inside the driver: it's just the JS bridge.
    """

    def __init__(self, url_match: str, *, runner: Optional[Callable] = None,
                 rebootstrap_every: int = 30):
        self.url_match = url_match
        self._runner = runner
        self._tick_count = 0
        self._rebootstrap_every = max(1, int(rebootstrap_every))
        self._installed = False

    def _run(self, js: str) -> Optional[str]:
        from .subtitle_scrape import _default_runner
        runner = self._runner or _default_runner
        script = render_applescript(self.url_match, js)
        try:
            return runner(["osascript", "-e", script])
        except Exception as e:  # noqa: BLE001
            log.debug("overlay osascript failed: %s", e)
            return None

    def install(self) -> bool:
        """Install (or re-install) the overlay in the matched tab."""
        out = self._run(bootstrap_js())
        self._installed = out is not None
        return self._installed

    def teardown(self) -> None:
        self._run(teardown_js())
        self._installed = False

    def pop_action(self) -> Optional[dict]:
        out = self._run(pop_action_js())
        return parse_action(out.strip() if out else None)

    def push(self, state: dict) -> None:
        self._run(update_js(state))

    def tick(self, state: dict) -> Optional[dict]:
        """Pop any pending action, then push the new state.

        Re-runs the bootstrap script every ``rebootstrap_every`` ticks so
        SPA navigation in the player (next episode, profile change) gets
        a fresh overlay even though we never see the navigation event.
        """
        self._tick_count += 1
        if not self._installed or (self._tick_count % self._rebootstrap_every) == 0:
            self.install()
        action = self.pop_action()
        self.push(state)
        return action
