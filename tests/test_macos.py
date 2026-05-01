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
