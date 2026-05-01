import numpy as np

from lang_view.frames import frame_signature, to_xywh


def _solid_frame(color=(0, 0, 0), shape=(120, 200, 3)):
    frame = np.zeros(shape, dtype=np.uint8)
    frame[:, :] = color
    return frame


class TestFrameSignature:
    def test_deterministic(self):
        frame = _solid_frame((10, 20, 30))
        assert frame_signature(frame) == frame_signature(frame)

    def test_solid_colors_differ(self):
        a = _solid_frame((0, 0, 0))
        b = _solid_frame((255, 255, 255))
        assert frame_signature(a) != frame_signature(b)

    def test_localized_change_changes_signature(self):
        a = _solid_frame((0, 0, 0))
        b = _solid_frame((0, 0, 0))
        # Paint a stripe big enough to land on the sample grid.
        b[40:80, 50:150] = (200, 150, 100)
        assert frame_signature(a) != frame_signature(b)

    def test_handles_small_frames(self):
        tiny = _solid_frame((5, 5, 5), shape=(4, 4, 3))
        # Should not crash and should still be deterministic.
        assert frame_signature(tiny) == frame_signature(tiny)

    def test_empty_frame_returns_empty_bytes(self):
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        assert frame_signature(empty) == b""


class TestToXywh:
    def test_axis_aligned_rect(self):
        bbox = [[10, 20], [110, 20], [110, 50], [10, 50]]
        assert to_xywh(bbox) == [10, 20, 100, 30]

    def test_rotated_quadrilateral_uses_bounding_rect(self):
        bbox = [[10, 22], [108, 18], [112, 52], [12, 56]]
        assert to_xywh(bbox) == [10, 18, 102, 38]

    def test_floats_are_floored_to_ints(self):
        bbox = [[10.7, 20.9], [110.2, 20.1], [110.0, 50.6], [10.4, 50.0]]
        result = to_xywh(bbox)
        assert all(isinstance(v, int) for v in result)
