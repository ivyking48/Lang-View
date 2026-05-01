import numpy as np

from lang_view.frames import (
    crop,
    frame_thumbnail,
    frames_similar,
    group_lines,
)


def _solid(color=(0, 0, 0), shape=(120, 200, 3)):
    f = np.zeros(shape, dtype=np.uint8)
    f[:, :] = color
    return f


class TestFrameThumbnail:
    def test_returns_grayscale_thumb(self):
        thumb = frame_thumbnail(_solid((100, 150, 200)), thumb=16)
        assert thumb.shape == (16, 16)
        assert thumb.dtype == np.uint8

    def test_handles_empty(self):
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        thumb = frame_thumbnail(empty)
        assert thumb.size == 0


class TestFramesSimilar:
    def test_identical_frames_are_similar(self):
        a = frame_thumbnail(_solid((50, 50, 50)))
        b = frame_thumbnail(_solid((50, 50, 50)))
        assert frames_similar(a, b)

    def test_solid_color_change_is_dissimilar(self):
        a = frame_thumbnail(_solid((10, 10, 10)))
        b = frame_thumbnail(_solid((200, 200, 200)))
        assert not frames_similar(a, b)

    def test_tiny_pixel_jitter_is_tolerated(self):
        a = frame_thumbnail(_solid((100, 100, 100)))
        b = a.copy()
        b[0, 0] = 255  # one rogue pixel out of 32*32
        assert frames_similar(a, b)

    def test_first_call_returns_false_when_prev_is_none(self):
        a = frame_thumbnail(_solid((10, 10, 10)))
        assert frames_similar(None, a) is False

    def test_shape_mismatch_returns_false(self):
        a = frame_thumbnail(_solid(shape=(100, 100, 3)), thumb=16)
        b = frame_thumbnail(_solid(shape=(100, 100, 3)), thumb=32)
        assert frames_similar(a, b) is False


class TestCrop:
    def test_basic_crop(self):
        f = _solid(shape=(100, 200, 3))
        c = crop(f, (10, 20, 50, 30))
        assert c.shape == (30, 50, 3)

    def test_clamps_to_image_bounds(self):
        f = _solid(shape=(100, 200, 3))
        c = crop(f, (180, 90, 100, 100))
        assert c.shape == (10, 20, 3)

    def test_zero_area_returns_empty(self):
        f = _solid(shape=(100, 200, 3))
        c = crop(f, (300, 300, 50, 50))
        assert c.size == 0


class TestGroupLines:
    def _det(self, x, y, w, h, text, conf=0.9):
        return ([x, y, w, h], text, conf)

    def test_groups_adjacent_words_on_same_line(self):
        dets = [
            self._det(10, 10, 30, 20, "안녕"),
            self._det(50, 11, 40, 20, "하세요"),
        ]
        out = group_lines(dets)
        assert len(out) == 1
        assert out[0][1] == "안녕 하세요"

    def test_keeps_separate_lines_apart(self):
        dets = [
            self._det(10, 10, 30, 20, "안녕"),
            self._det(10, 60, 30, 20, "세계"),
        ]
        out = group_lines(dets)
        assert len(out) == 2

    def test_does_not_merge_across_large_horizontal_gap(self):
        dets = [
            self._det(10, 10, 30, 20, "안녕"),
            self._det(500, 10, 30, 20, "세계"),
        ]
        out = group_lines(dets)
        assert len(out) == 2

    def test_bbox_covers_all_members(self):
        dets = [
            self._det(10, 10, 30, 20, "a"),
            self._det(50, 10, 40, 20, "b"),
        ]
        out = group_lines(dets)
        x, y, w, h = out[0][0]
        assert (x, y) == (10, 10)
        assert x + w >= 90
        assert h >= 20

    def test_confidence_is_min_by_default(self):
        dets = [
            self._det(10, 10, 30, 20, "a", conf=0.9),
            self._det(50, 10, 30, 20, "b", conf=0.4),
        ]
        out = group_lines(dets)
        assert out[0][2] == 0.4

    def test_confidence_mean_strategy(self):
        dets = [
            self._det(10, 10, 30, 20, "a", conf=1.0),
            self._det(50, 10, 30, 20, "b", conf=0.5),
        ]
        out = group_lines(dets, conf_strategy="mean")
        assert abs(out[0][2] - 0.75) < 1e-9

    def test_empty_input(self):
        assert group_lines([]) == []
