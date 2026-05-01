"""Best-effort macOS active-window detection.

Tries the AppKit/Quartz Python bindings if available, otherwise falls
back to spawning `osascript` with an AppleScript snippet. Returns
`ActiveWindow(app, x, y, w, h)` or `None` if detection failed.

Both code paths read public Accessibility APIs so the user does not
need any extra permissions beyond what Screen Recording already
grants. We never inject events.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveWindow:
    app: str
    x: int
    y: int
    w: int
    h: int

    @property
    def region(self):
        return (self.x, self.y, self.w, self.h)


_APPLESCRIPT = r'''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set winPos to position of front window of frontApp
        set winSize to size of front window of frontApp
        return appName & "|" & (item 1 of winPos) & "," & (item 2 of winPos) & "," & (item 1 of winSize) & "," & (item 2 of winSize)
    on error
        return appName & "|"
    end try
end tell
'''


_APPLESCRIPT_OUTPUT = re.compile(
    r"^(?P<app>.+?)\|"
    r"(?:(?P<x>-?\d+),(?P<y>-?\d+),(?P<w>\d+),(?P<h>\d+))?$"
)


def parse_applescript_output(stdout: str):
    """Parse the output of `_APPLESCRIPT` into an ActiveWindow."""
    line = stdout.strip()
    if not line:
        return None
    match = _APPLESCRIPT_OUTPUT.match(line)
    if not match:
        return None
    app = match.group("app")
    if match.group("x") is None:
        return ActiveWindow(app=app, x=0, y=0, w=0, h=0)
    return ActiveWindow(
        app=app,
        x=int(match.group("x")),
        y=int(match.group("y")),
        w=int(match.group("w")),
        h=int(match.group("h")),
    )


def get_active_window_via_quartz():
    try:
        from AppKit import NSWorkspace
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError:
        return None
    try:
        active_app = NSWorkspace.sharedWorkspace().activeApplication()
        if not active_app:
            return None
        app_name = active_app["NSApplicationName"]
        owner_pid = active_app["NSApplicationProcessIdentifier"]
        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
        for w in windows:
            if w.get("kCGWindowOwnerPID") != owner_pid:
                continue
            bounds = w.get("kCGWindowBounds")
            if not bounds:
                continue
            return ActiveWindow(
                app=app_name,
                x=int(bounds.get("X", 0)),
                y=int(bounds.get("Y", 0)),
                w=int(bounds.get("Width", 0)),
                h=int(bounds.get("Height", 0)),
            )
        return ActiveWindow(app=app_name, x=0, y=0, w=0, h=0)
    except Exception as e:
        log.debug("Quartz active-window detection failed: %s", e)
        return None


def get_active_window_via_osascript(runner=None):
    runner = runner or _default_runner
    try:
        out = runner(["osascript", "-e", _APPLESCRIPT])
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        log.debug("osascript not available: %s", e)
        return None
    return parse_applescript_output(out)


def _default_runner(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL,
                                   timeout=2.0)


def get_active_window():
    """Try Quartz first, then osascript. Returns None if both fail."""
    win = get_active_window_via_quartz()
    if win is not None:
        return win
    return get_active_window_via_osascript()
