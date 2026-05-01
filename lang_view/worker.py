"""Async OCR worker thread.

The capture loop pushes frames into a single-slot queue. The worker
pulls frames, runs OCR, and emits per-detection records via a
callback. If the worker is busy when a new frame arrives the older
queued frame is discarded so the worker always processes the most
recent screen, not a backlog of stale ones.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

log = logging.getLogger(__name__)


class PauseSwitch:
    """Thread-safe pause toggle. Default state: not paused."""

    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False

    def toggle(self):
        with self._lock:
            self._paused = not self._paused
            return self._paused

    def set(self, paused):
        with self._lock:
            self._paused = bool(paused)

    def is_paused(self):
        with self._lock:
            return self._paused


class AsyncOCRWorker:
    """Run OCR in a background thread, dropping stale frames.

    The `engines` callable receives `(frame, app_name)` and should
    return an iterable of `(engine_lang_hint, [(bbox, text, conf), ...])`
    pairs.
    """

    def __init__(self, engines_run, on_detections,
                 on_error: Callable[[Exception], None] | None = None):
        self._engines_run = engines_run
        self._on_detections = on_detections
        self._on_error = on_error or (lambda _e: None)
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = None
        self._dropped = 0

    @property
    def dropped(self):
        return self._dropped

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="lang-view-ocr",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout=1.0):
        self._stop.set()
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(_SENTINEL)
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def submit(self, frame, app_name=None):
        """Drop the older queued frame if any, then enqueue this one.

        Returns True if the frame was accepted (always True today, but
        kept for symmetry with possible future shedding logic).
        """
        try:
            self._queue.put_nowait((frame, app_name))
            return True
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._dropped += 1
            except queue.Empty:
                pass
            self._queue.put_nowait((frame, app_name))
            return True

    def _run(self):
        while not self._stop.is_set():
            item = self._queue.get()
            if item is _SENTINEL:
                return
            frame, app_name = item
            try:
                for hint, detections in self._engines_run(frame, app_name):
                    self._on_detections(hint, detections, frame, app_name)
            except Exception as e:
                self._on_error(e)


_SENTINEL = object()
