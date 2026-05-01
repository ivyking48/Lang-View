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


def to_xywh(bbox):
    """Convert a 4-point polygon bbox to an axis-aligned [x, y, w, h]."""
    xs = [int(p[0]) for p in bbox]
    ys = [int(p[1]) for p in bbox]
    x = min(xs)
    y = min(ys)
    return [x, y, max(xs) - x, max(ys) - y]
