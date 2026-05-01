import hashlib

import numpy as np


def frame_signature(frame, thumb=64):
    """Return a stable digest for a screen frame (numpy HxWxC uint8).

    Samples a `thumb` x `thumb` grid and hashes the bytes. Identical
    frames produce identical digests; any visible change typically
    changes at least one sampled pixel and thus the digest.
    """
    if frame.size == 0:
        return b""
    h, w = frame.shape[:2]
    ys = np.linspace(0, h - 1, num=min(thumb, h), dtype=np.int64)
    xs = np.linspace(0, w - 1, num=min(thumb, w), dtype=np.int64)
    sample = frame[np.ix_(ys, xs)]
    return hashlib.blake2b(sample.tobytes(), digest_size=16).digest()


def frame_thumbnail(frame, thumb=32):
    """Downsample to a `thumb` x `thumb` grayscale uint8 array.

    Used as the input to `frames_similar` for tolerant change detection.
    """
    if frame.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)
    h, w = frame.shape[:2]
    ys = np.linspace(0, h - 1, num=min(thumb, h), dtype=np.int64)
    xs = np.linspace(0, w - 1, num=min(thumb, w), dtype=np.int64)
    sample = frame[np.ix_(ys, xs)]
    if sample.ndim == 3:
        sample = sample.mean(axis=2)
    return sample.astype(np.uint8)


def frames_similar(prev_thumb, curr_thumb, *, mean_tolerance=2.0,
                   change_fraction=0.01):
    """Return True if `curr_thumb` is visually close enough to `prev_thumb`.

    Two thresholds gate the decision:
      - `mean_tolerance`: average absolute pixel diff (0..255)
      - `change_fraction`: fraction of pixels that differ by more than 16

    A blinking cursor or a clock tick changes only a handful of pixels and
    should not trigger a re-OCR; a scroll or a window switch trips both
    thresholds easily.
    """
    if prev_thumb is None or prev_thumb.shape != curr_thumb.shape:
        return False
    if curr_thumb.size == 0:
        return True
    diff = np.abs(prev_thumb.astype(np.int16) - curr_thumb.astype(np.int16))
    if float(diff.mean()) > mean_tolerance:
        return False
    if float((diff > 16).mean()) > change_fraction:
        return False
    return True


def to_xywh(bbox):
    """Convert a 4-point polygon bbox to an axis-aligned [x, y, w, h]."""
    xs = [int(p[0]) for p in bbox]
    ys = [int(p[1]) for p in bbox]
    x = min(xs)
    y = min(ys)
    return [x, y, max(xs) - x, max(ys) - y]


def crop(frame, region):
    """Crop `frame` to `region` = (x, y, w, h). Out-of-bounds is clamped."""
    x, y, w, h = region
    fh, fw = frame.shape[:2]
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(fw, int(x) + int(w))
    y1 = min(fh, int(y) + int(h))
    if x1 <= x0 or y1 <= y0:
        return frame[0:0, 0:0]
    return frame[y0:y1, x0:x1]


def group_lines(detections, *, y_tolerance=0.6, x_gap_tolerance=0.6,
                conf_strategy="min"):
    """Group adjacent detections into lines.

    Each detection is `(bbox, text, conf)` where bbox is xywh-or-polygon —
    we normalise via `to_xywh`. Detections whose vertical center differs
    by less than `y_tolerance * line_height` and whose horizontal gap is
    smaller than `x_gap_tolerance * line_height` are merged into a single
    line, joined with a single space.

    Returns a list of `(bbox_xywh, text, conf)` tuples.
    """
    rects = []
    for bbox, text, conf in detections:
        x, y, w, h = to_xywh(bbox) if not _is_xywh(bbox) else list(bbox)
        rects.append({"x": x, "y": y, "w": w, "h": h,
                      "text": text, "conf": float(conf)})
    if not rects:
        return []

    rects.sort(key=lambda r: (r["y"] + r["h"] / 2, r["x"]))

    groups = []
    for r in rects:
        cy = r["y"] + r["h"] / 2
        placed = False
        for group in groups:
            gcy = group["cy_sum"] / group["count"]
            line_height = max(r["h"], group["max_h"], 1)
            if abs(cy - gcy) <= y_tolerance * line_height:
                rightmost = max(m["x"] + m["w"] for m in group["members"])
                if r["x"] - rightmost <= x_gap_tolerance * line_height:
                    group["members"].append(r)
                    group["cy_sum"] += cy
                    group["count"] += 1
                    group["max_h"] = max(group["max_h"], r["h"])
                    placed = True
                    break
        if not placed:
            groups.append({
                "members": [r],
                "cy_sum": cy,
                "count": 1,
                "max_h": r["h"],
            })

    out = []
    for group in groups:
        members = sorted(group["members"], key=lambda m: m["x"])
        text = " ".join(m["text"] for m in members)
        x0 = min(m["x"] for m in members)
        y0 = min(m["y"] for m in members)
        x1 = max(m["x"] + m["w"] for m in members)
        y1 = max(m["y"] + m["h"] for m in members)
        confs = [m["conf"] for m in members]
        if conf_strategy == "min":
            conf = min(confs)
        elif conf_strategy == "mean":
            conf = sum(confs) / len(confs)
        else:
            conf = max(confs)
        out.append(([x0, y0, x1 - x0, y1 - y0], text, conf))
    return out


def _is_xywh(bbox):
    return (len(bbox) == 4
            and all(isinstance(v, (int, float)) for v in bbox))
