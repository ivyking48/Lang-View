from lang_view.dedup import TimeWindowDedup


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_first_seen_returns_true():
    dedup = TimeWindowDedup(window_seconds=10, clock=FakeClock())
    assert dedup.check_and_add("hello") is True


def test_repeat_within_window_is_suppressed():
    clock = FakeClock()
    dedup = TimeWindowDedup(window_seconds=10, clock=clock)
    assert dedup.check_and_add("hello") is True
    clock.advance(5)
    assert dedup.check_and_add("hello") is False


def test_repeat_after_window_is_allowed_again():
    clock = FakeClock()
    dedup = TimeWindowDedup(window_seconds=10, clock=clock)
    assert dedup.check_and_add("hello") is True
    clock.advance(11)
    assert dedup.check_and_add("hello") is True


def test_repeat_within_window_refreshes_timestamp():
    """A repeat within the window should reset the dedup clock."""
    clock = FakeClock()
    dedup = TimeWindowDedup(window_seconds=10, clock=clock)
    dedup.check_and_add("hello")
    clock.advance(8)
    # Refreshes the timestamp even though it returns False.
    assert dedup.check_and_add("hello") is False
    clock.advance(8)  # 16s after first sighting, but only 8s since last
    assert dedup.check_and_add("hello") is False


def test_distinct_keys_are_independent():
    dedup = TimeWindowDedup(window_seconds=10, clock=FakeClock())
    assert dedup.check_and_add("a") is True
    assert dedup.check_and_add("b") is True
    assert dedup.check_and_add("a") is False
    assert dedup.check_and_add("b") is False


def test_expired_entries_are_pruned():
    clock = FakeClock()
    dedup = TimeWindowDedup(window_seconds=10, clock=clock)
    dedup.check_and_add("a")
    dedup.check_and_add("b")
    clock.advance(15)
    dedup.check_and_add("c")  # triggers prune of expired a, b
    assert len(dedup) == 1
