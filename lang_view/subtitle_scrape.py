"""Read live subtitles out of a Chrome tab via Apple Events.

DRM-protected video frames (Disney+, Netflix, …) are blocked from any
screen-recording API on macOS, but those players render their subtitles
as plain DOM text — that's how VoiceOver and other accessibility tools
read them. We use Chrome's built-in Apple Events JavaScript bridge to
ask the page for the current subtitle text every interval. The bridge
must be enabled in Chrome (View → Developer → "Allow JavaScript from
Apple Events"); see the README for the one-click setup.

This module is the testable surface (script generation, scrape result
parsing). The actual ``osascript`` invocation is isolated behind a
``runner`` callable so unit tests don't touch the system.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


_OSASCRIPT_TEMPLATE = r'''
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t contains {url_pattern} then
        try
          return execute t javascript {js_expr}
        on error
          return ""
        end try
      end if
    end repeat
  end repeat
  return ""
end tell
'''


def _applescript_quote(s: str) -> str:
    """Quote a string for use as an AppleScript string literal."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render_applescript(url_pattern: str, js_expression: str) -> str:
    """Build an AppleScript that finds a Chrome tab whose URL contains
    ``url_pattern`` and runs ``js_expression`` in it. The expression's
    return value is the AppleScript return value."""
    return _OSASCRIPT_TEMPLATE.format(
        url_pattern=_applescript_quote(url_pattern),
        js_expr=_applescript_quote(js_expression),
    )


def is_osascript_available() -> bool:
    return shutil.which("osascript") is not None


def scrape_chrome_subtitle(url_pattern: str, selector: str, *, runner=None) -> str | None:
    """Return the current subtitle text for the matching tab, or None.

    A blank/missing subtitle returns None so callers can distinguish "no
    subtitle on screen right now" from "scrape failed to talk to Chrome."
    """
    runner = runner or _default_runner
    js = f"document.querySelector({json.dumps(selector)})?.innerText || ''"
    script = render_applescript(url_pattern, js)
    try:
        out = runner(["osascript", "-e", script])
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        log.debug("osascript scrape failed: %s", e)
        return None
    text = out.strip()
    if not text:
        return None
    return text


def _default_runner(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=5.0)
