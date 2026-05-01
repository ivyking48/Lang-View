"""Save a per-detection cropped PNG snippet of the OCR bbox.

Also exposes ``FrameWriter`` for saving the full captured frame to disk
when at least one detection on that frame is kept.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from .frames import crop

log = logging.getLogger(__name__)


class FrameWriter:
    """Save the full captured frame as a PNG, deduped by time.

    The watch loop calls ``write(frame)`` from each engine's
    ``on_detections`` callback. A short time-based debounce
    (``min_interval_seconds``) ensures the frame is written only once
    per cycle even when multiple engines run on the same frame.
    """

    def __init__(self, dest_dir, min_interval_seconds=0.1):
        self.dest_dir = Path(dest_dir)
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = float(min_interval_seconds)
        self._last_written = 0.0

    def write(self, frame):
        try:
            from PIL import Image
        except ImportError:
            log.warning("Pillow not installed; skipping full-frame snapshot")
            return None
        import time
        now = time.monotonic()
        if now - self._last_written < self.min_interval:
            return None
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = self.dest_dir / f"{timestamp}_frame.png"
        try:
            Image.fromarray(frame[:, :, ::-1]).save(path)  # BGR -> RGB
        except Exception as e:
            log.warning("Could not write frame %s: %s", path, e)
            return None
        self._last_written = now
        return str(path)


class SnippetWriter:
    def __init__(self, dest_dir, padding=4):
        self.dest_dir = Path(dest_dir)
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.padding = int(padding)

    def write(self, frame, bbox_xywh, label="snippet"):
        """Crop `frame` to `bbox_xywh` (with padding) and save as PNG.

        Returns the path on disk, or None on failure.
        """
        try:
            from PIL import Image
        except ImportError:
            log.warning("Pillow not installed; skipping snippet for %s", label)
            return None
        x, y, w, h = bbox_xywh
        padded = (x - self.padding, y - self.padding,
                  w + 2 * self.padding, h + 2 * self.padding)
        cropped = crop(frame, padded)
        if cropped.size == 0:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        safe_label = "".join(c if c.isalnum() else "_" for c in label)[:40]
        path = self.dest_dir / f"{timestamp}_{safe_label}.png"
        try:
            Image.fromarray(cropped[:, :, ::-1]).save(path)  # BGR -> RGB
        except Exception as e:
            log.warning("Could not write snippet %s: %s", path, e)
            return None
        return str(path)
