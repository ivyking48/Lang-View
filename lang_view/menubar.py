"""macOS menubar app.

`MenubarState` holds the testable parts: the recent-captures buffer
and the pause/running flag. `MenubarApp` is the thin rumps wrapper
that wires the state to a menubar UI; it imports rumps lazily so
this module stays importable on non-macOS systems.

Wire the menubar to a running watch session in two ways:
- In-process: pass a ``PauseSwitch`` (from ``lang_view.worker``).
- Cross-process (the always-on agent): pass a ``FilePauseFlag``
  (from ``lang_view.pause``). The watch agent and the menubar
  coordinate via the same flag file path, so the menubar can pause
  the agent even though they live in different processes.
"""

from __future__ import annotations

import logging
from collections import deque
from threading import Lock

log = logging.getLogger(__name__)


class MenubarState:
    def __init__(self, pause_switch=None, max_recent=20):
        self._pause = pause_switch
        self._lock = Lock()
        self._recent = deque(maxlen=int(max_recent))
        self._listeners = []

    @property
    def is_paused(self):
        return bool(self._pause and self._pause.is_paused())

    def toggle_pause(self):
        if self._pause is None:
            return None
        new = self._pause.toggle()
        self._fire()
        return new

    def append(self, record):
        """Record a new capture for display in the recent menu."""
        if not record:
            return
        text = record.get("text") if isinstance(record, dict) else str(record)
        if not text:
            return
        lang = record.get("lang", "") if isinstance(record, dict) else ""
        with self._lock:
            self._recent.appendleft({"text": text, "lang": lang})
        self._fire()

    def recent(self):
        with self._lock:
            return list(self._recent)

    def add_listener(self, fn):
        self._listeners.append(fn)

    def _fire(self):
        for fn in list(self._listeners):
            try:
                fn(self)
            except Exception as e:
                log.warning("Menubar listener failed: %s", e)


class MenubarApp:
    """rumps-based menubar UI bound to a MenubarState.

    The menubar title shows ``語`` when running and ``⏸ 語`` when paused,
    so you can tell at a glance whether the agent is recording. A timer
    polls the underlying pause source every ``poll_seconds`` so the
    title updates even if the flag file is changed externally (e.g. by
    a shell command).
    """

    def __init__(self, state, *, title_running="▶ 語", title_paused="⏸ 語",
                 poll_seconds=2.0):
        self.state = state
        self.title_running = title_running
        self.title_paused = title_paused
        self.poll_seconds = float(poll_seconds)
        self._app = None
        self._recent_items = []
        self._pause_item = None

    def _title_for(self, state):
        return self.title_paused if state.is_paused else self.title_running

    def run(self):
        try:
            import rumps
        except ImportError as e:
            raise RuntimeError(
                "rumps is required for the menubar app. "
                "Install with `pip install lang-view[menubar]`."
            ) from e

        app = rumps.App(self._title_for(self.state))
        self._app = app

        pause_label = "Pause" if not self.state.is_paused else "Resume"
        pause_item = rumps.MenuItem(pause_label, callback=self._on_pause)
        self._pause_item = pause_item

        recent_header = rumps.MenuItem("Recent captures")
        recent_header.set_callback(None)

        quit_item = rumps.MenuItem("Quit Lang-View", callback=self._on_quit)

        app.menu = [pause_item, None, recent_header, None, quit_item]
        self.state.add_listener(self._refresh)
        self._refresh(self.state)

        # Poll the pause source so external changes (shell touch/rm of
        # the flag file) flow back into the UI title.
        try:
            timer = rumps.Timer(self._on_tick, self.poll_seconds)
            timer.start()
        except Exception as e:
            log.warning("Could not start refresh timer: %s", e)

        app.run()

    def _on_pause(self, _sender):
        self.state.toggle_pause()
        self._refresh(self.state)

    def _on_quit(self, _sender):
        try:
            import rumps
            rumps.quit_application()
        except ImportError:
            pass

    def _on_tick(self, _sender):
        self._refresh(self.state)

    def _refresh(self, state):
        if self._app is None:
            return
        self._app.title = self._title_for(state)
        if self._pause_item is not None:
            self._pause_item.title = "Resume" if state.is_paused else "Pause"
        # Replace recent items: remove old ones, insert fresh.
        for item in list(self._recent_items):
            try:
                del self._app.menu[item]
            except (KeyError, ValueError):
                pass
        self._recent_items = []
        for entry in state.recent()[:10]:
            label = f"[{entry['lang']}] {entry['text']}"
            label = label[:60] + ("…" if len(label) > 60 else "")
            self._app.menu.insert_after("Recent captures", label)
            self._recent_items.append(label)
