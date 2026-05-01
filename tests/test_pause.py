from lang_view.pause import FilePauseFlag


def test_default_is_not_paused(tmp_path):
    flag = FilePauseFlag(tmp_path / "p.flag")
    assert flag.is_paused() is False


def test_set_paused_creates_file(tmp_path):
    path = tmp_path / "p.flag"
    flag = FilePauseFlag(path)
    flag.set_paused(True)
    assert path.exists()
    assert flag.is_paused() is True


def test_set_paused_false_removes_file(tmp_path):
    path = tmp_path / "p.flag"
    flag = FilePauseFlag(path)
    flag.set_paused(True)
    flag.set_paused(False)
    assert not path.exists()
    assert flag.is_paused() is False


def test_set_paused_false_is_idempotent_when_missing(tmp_path):
    flag = FilePauseFlag(tmp_path / "p.flag")
    # Should not raise even though the file does not exist.
    flag.set_paused(False)
    assert flag.is_paused() is False


def test_toggle_alternates_state(tmp_path):
    flag = FilePauseFlag(tmp_path / "p.flag")
    assert flag.toggle() is True   # off -> on
    assert flag.is_paused() is True
    assert flag.toggle() is False  # on -> off
    assert flag.is_paused() is False


def test_set_paused_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "p.flag"
    flag = FilePauseFlag(path)
    flag.set_paused(True)
    assert path.exists()


def test_external_file_change_is_observed(tmp_path):
    """If something else (e.g. a shell `touch` from the menubar) creates
    the flag file, ``is_paused`` should reflect that immediately — no
    cached state."""
    path = tmp_path / "p.flag"
    flag = FilePauseFlag(path)
    assert flag.is_paused() is False
    path.touch()
    assert flag.is_paused() is True
    path.unlink()
    assert flag.is_paused() is False
