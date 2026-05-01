import threading
import time

from lang_view.worker import AsyncOCRWorker, PauseSwitch


class TestPauseSwitch:
    def test_default_state(self):
        s = PauseSwitch()
        assert not s.is_paused()

    def test_toggle(self):
        s = PauseSwitch()
        assert s.toggle() is True
        assert s.is_paused()
        assert s.toggle() is False
        assert not s.is_paused()

    def test_explicit_set(self):
        s = PauseSwitch()
        s.set(True)
        assert s.is_paused()
        s.set(False)
        assert not s.is_paused()

    def test_thread_safe(self):
        s = PauseSwitch()
        errors = []

        def hammer():
            try:
                for _ in range(200):
                    s.toggle()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


class TestAsyncOCRWorker:
    def test_submit_runs_engines(self):
        seen = []
        done = threading.Event()

        def engines_run(frame, app):
            return [("ja", [("bbox", frame, 0.9)])]

        def on_det(hint, dets, frame, app):
            seen.append((hint, dets))
            done.set()

        w = AsyncOCRWorker(engines_run, on_det)
        w.start()
        try:
            w.submit("frame1", app_name="Safari")
            assert done.wait(timeout=2.0)
        finally:
            w.stop()

        assert seen[0][0] == "ja"
        assert seen[0][1][0][1] == "frame1"

    def test_drops_stale_frames(self):
        gate = threading.Event()
        first_started = threading.Event()
        seen = []

        def engines_run(frame, app):
            if frame == "slow":
                first_started.set()
                gate.wait(timeout=2.0)
            return [("ja", [(None, frame, 1.0)])]

        def on_det(hint, dets, frame, app):
            seen.append(dets[0][1])

        w = AsyncOCRWorker(engines_run, on_det)
        w.start()
        try:
            w.submit("slow")
            assert first_started.wait(timeout=1.0)
            # Now stuff three frames in while the worker is blocked on "slow".
            w.submit("a")
            w.submit("b")
            w.submit("c")  # This should evict "b" (or "a") from the queue.
            gate.set()
            time.sleep(0.5)
        finally:
            w.stop()

        # The worker definitely processed "slow" and the latest pushed frame.
        assert "slow" in seen
        assert "c" in seen
        assert w.dropped >= 1

    def test_errors_are_routed_to_callback(self):
        errors = []
        done = threading.Event()

        def engines_run(frame, app):
            raise RuntimeError("boom")

        def on_det(*a):
            pass

        def on_err(e):
            errors.append(e)
            done.set()

        w = AsyncOCRWorker(engines_run, on_det, on_error=on_err)
        w.start()
        try:
            w.submit("frame")
            assert done.wait(timeout=2.0)
        finally:
            w.stop()

        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)
