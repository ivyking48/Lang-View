import subprocess

import pytest

from lang_view.subtitle_scrape import (
    _applescript_quote,
    render_applescript,
    scrape_chrome_subtitle,
)


def test_applescript_quote_escapes_double_quotes():
    assert _applescript_quote('say "hi"') == '"say \\"hi\\""'


def test_applescript_quote_escapes_backslashes():
    assert _applescript_quote(r"a\b") == r'"a\\b"'


def test_render_applescript_embeds_url_and_js():
    s = render_applescript(
        "disneyplus.com/play",
        "document.querySelector('.x')?.innerText || ''",
    )
    assert 'tell application "Google Chrome"' in s
    assert '"disneyplus.com/play"' in s
    assert "document.querySelector" in s
    assert "execute t javascript" in s


def test_scrape_returns_text_when_runner_yields_subtitle():
    captured_cmd = []

    def fake_runner(cmd):
        captured_cmd.append(cmd)
        return "안녕하세요\n"

    text = scrape_chrome_subtitle(
        "disneyplus.com/play",
        ".hive-subtitle-renderer-wrapper",
        runner=fake_runner,
    )
    assert text == "안녕하세요"
    assert captured_cmd[0][0] == "osascript"


def test_scrape_returns_none_when_runner_yields_blank():
    text = scrape_chrome_subtitle(
        "disneyplus.com/play",
        ".x",
        runner=lambda _cmd: "   \n",
    )
    assert text is None


def test_scrape_returns_none_when_osascript_missing():
    def boom(_cmd):
        raise FileNotFoundError("osascript not on PATH")

    assert scrape_chrome_subtitle("x", "x", runner=boom) is None


def test_scrape_returns_none_on_subprocess_error():
    def boom(_cmd):
        raise subprocess.TimeoutExpired(cmd=_cmd, timeout=5.0)

    assert scrape_chrome_subtitle("x", "x", runner=boom) is None


def test_scrape_passes_selector_to_querySelector():
    seen = {}

    def fake_runner(cmd):
        # cmd is ["osascript", "-e", "<script>"] — capture the script.
        seen["script"] = cmd[2]
        return ""

    scrape_chrome_subtitle(
        "netflix.com/watch",
        ".player-timedtext",
        runner=fake_runner,
    )
    # The selector should appear inside the JavaScript expression embedded
    # in the AppleScript.
    assert ".player-timedtext" in seen["script"]
    assert "netflix.com/watch" in seen["script"]
