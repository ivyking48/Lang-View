"""Save a per-detection cropped PNG snippet of the OCR bbox."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from .frames import crop

log = logging.getLogger(__name__)


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
