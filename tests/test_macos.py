from lang_view.macos import (
    ActiveWindow,
    get_active_window_via_osascript,
    parse_applescript_output,
)


def test_parse_applescript_output_with_bounds():
    win = parse_applescript_output("Safari|10,20,800,600\n")
    assert win == ActiveWindow(app="Safari", x=10, y=20, w=800, h=600)


def test_parse_applescript_output_without_bounds():
    win = parse_applescript_output("Finder|\n")
    assert win.app == "Finder"
    assert win.region == (0, 0, 0, 0)


def test_parse_applescript_handles_app_names_with_spaces():
    win = parse_applescript_output("Google Chrome|0,25,1440,900")
    assert win.app == "Google Chrome"
    assert win.w == 1440


def test_parse_applescript_handles_negative_coordinates():
    win = parse_applescript_output("Safari|-100,-50,800,600")
    assert win.x == -100
    assert win.y == -50


def test_parse_applescript_extracts_window_title():
    win = parse_applescript_output(
        "Google Chrome|0,25,1440,900|Perfect Crown | Disney+"
    )
    assert win.app == "Google Chrome"
    assert win.title == "Perfect Crown | Disney+"


def test_parse_applescript_title_without_geometry():
    win = parse_applescript_output("Finder||Documents")
    assert win.app == "Finder"
    assert win.title == "Documents"
    assert win.region == (0, 0, 0, 0)


def test_parse_applescript_no_title_defaults_empty():
    win = parse_applescript_output("Safari|10,20,800,600")
    assert win.title == ""


def test_get_visible_windows_returns_empty_without_quartz(monkeypatch):
    """If pyobjc-framework-Quartz isn't installed (or import fails),
    get_visible_windows should return an empty list, not raise."""
    import builtins
    real_import = builtins.__import__

    def stub_import(name, *args, **kwargs):
        if name.startswith("Quartz"):
            raise ImportError("Quartz unavailable in test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub_import)
    from lang_view.macos import get_visible_windows
    assert get_visible_windows() == []


def test_active_window_area_property():
    """ActiveWindow.area is used to pick the largest matching window."""
    big = ActiveWindow(app="Chrome", x=0, y=0, w=1440, h=900)
    small = ActiveWindow(app="Chrome", x=0, y=0, w=300, h=200)
    assert big.area > small.area
    assert ActiveWindow(app="x", x=0, y=0, w=0, h=0).area == 0


def test_capture_window_image_returns_none_without_quartz(monkeypatch):
    """capture_window_image must degrade gracefully when Quartz is absent."""
    import builtins
    real_import = builtins.__import__

    def stub_import(name, *args, **kwargs):
        if name.startswith("Quartz"):
            raise ImportError("Quartz unavailable in test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub_import)
    from lang_view.macos import capture_window_image
    assert capture_window_image(12345) is None


def test_parse_applescript_returns_none_on_garbage():
    assert parse_applescript_output("") is None


def test_get_active_window_via_osascript_uses_runner():
    def fake_runner(cmd):
        assert cmd[0] == "osascript"
        return "Terminal|0,0,1024,768"

    win = get_active_window_via_osascript(runner=fake_runner)
    assert win.app == "Terminal"
    assert win.region == (0, 0, 1024, 768)


def test_get_active_window_via_osascript_returns_none_when_unavailable():
    def fake_runner(cmd):
        raise FileNotFoundError("no osascript")

    assert get_active_window_via_osascript(runner=fake_runner) is None
