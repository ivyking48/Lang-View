from collections import OrderedDict
from time import monotonic


class TimeWindowDedup:
    """Skip a key if it was last seen within `window_seconds`.

    Unlike a fixed-size sliding window, this lets recurring text be
    re-logged once it disappears for long enough, while still
    suppressing duplicates while the same text remains on screen.
    """

    def __init__(self, window_seconds, clock=monotonic):
        self.window = float(window_seconds)
        self._clock = clock
        self._seen = OrderedDict()

    def check_and_add(self, key):
        """Return True if `key` is fresh and should be recorded."""
        now = self._clock()
        cutoff = now - self.window
        last = self._seen.get(key)
        if last is not None and last >= cutoff:
            self._seen.move_to_end(key)
            self._seen[key] = now
            return False
        self._seen[key] = now
        self._seen.move_to_end(key)
        self._prune(cutoff)
        return True

    def _prune(self, cutoff):
        while self._seen:
            oldest_key = next(iter(self._seen))
            if self._seen[oldest_key] < cutoff:
                self._seen.popitem(last=False)
            else:
                break

    def __len__(self):
        return len(self._seen)
