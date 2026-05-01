import numpy as np
import pytest

pytest.importorskip("PIL")

from lang_view.snippets import SnippetWriter


def _frame(shape=(100, 200, 3)):
    f = np.zeros(shape, dtype=np.uint8)
    f[:, :] = (10, 20, 30)
    return f


def test_writes_png_to_disk(tmp_path):
    writer = SnippetWriter(tmp_path, padding=0)
    path = writer.write(_frame(), bbox_xywh=(10, 10, 50, 30), label="hi")
    assert path is not None
    p = tmp_path / __import__("os").path.basename(path)
    assert p.exists()
    assert p.suffix == ".png"


def test_returns_none_for_zero_area_crop(tmp_path):
    writer = SnippetWriter(tmp_path, padding=0)
    path = writer.write(_frame(), bbox_xywh=(500, 500, 10, 10), label="off")
    assert path is None


def test_padding_expands_crop(tmp_path):
    writer = SnippetWriter(tmp_path, padding=5)
    # Center a small bbox; with padding=5 the saved image should be larger.
    path = writer.write(_frame(), bbox_xywh=(50, 50, 10, 10), label="pad")
    assert path is not None
    from PIL import Image
    img = Image.open(path)
    assert img.size == (20, 20)


def test_label_is_sanitised(tmp_path):
    writer = SnippetWriter(tmp_path, padding=0)
    path = writer.write(_frame(), bbox_xywh=(0, 0, 50, 30), label="안녕!?")
    assert path is not None
    # Only alnum and underscore should appear in the filename.
    name = path.split("/")[-1]
    suffix = name.split("_", 1)[-1].rsplit(".", 1)[0]
    assert all(c.isalnum() or c == "_" for c in suffix)
