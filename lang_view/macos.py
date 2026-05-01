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
    title: str = ""
    window_id: int = 0  # CGWindowID, used by capture_window_image()

    @property
    def region(self):
        return (self.x, self.y, self.w, self.h)

    @property
    def area(self):
        return max(0, int(self.w)) * max(0, int(self.h))


_APPLESCRIPT = r'''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set winName to name of front window of frontApp
    on error
        set winName to ""
    end try
    try
        set winPos to position of front window of frontApp
        set winSize to size of front window of frontApp
        return appName & "|" & (item 1 of winPos) & "," & (item 2 of winPos) & "," & (item 1 of winSize) & "," & (item 2 of winSize) & "|" & winName
    on error
        return appName & "||" & winName
    end try
end tell
'''


def parse_applescript_output(stdout: str):
    """Parse the output of `_APPLESCRIPT` into an ActiveWindow.

    Format: ``appName|x,y,w,h|title`` (geometry and title both optional).
    The title is split with maxsplit=2 so titles may contain ``|``.
    """
    line = stdout.strip()
    if not line:
        return None
    parts = line.split("|", 2)
    if len(parts) < 2:
        return None
    app = parts[0]
    geom = parts[1]
    title = parts[2] if len(parts) > 2 else ""
    if not geom:
        return ActiveWindow(app=app, x=0, y=0, w=0, h=0, title=title)
    geom_match = re.match(r"^(-?\d+),(-?\d+),(\d+),(\d+)$", geom)
    if not geom_match:
        return ActiveWindow(app=app, x=0, y=0, w=0, h=0, title=title)
    return ActiveWindow(
        app=app,
        x=int(geom_match.group(1)),
        y=int(geom_match.group(2)),
        w=int(geom_match.group(3)),
        h=int(geom_match.group(4)),
        title=title,
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
                title=str(w.get("kCGWindowName", "") or ""),
            )
        return ActiveWindow(app=app_name, x=0, y=0, w=0, h=0, title="")
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


def get_visible_windows():
    """Return every on-screen window as an ``ActiveWindow``.

    Useful for the side-by-side case: the user is watching Disney+ in
    one window while Terminal is frontmost, and the watch loop should
    still fire because *some* visible window matches the title filter.
    """
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError:
        return []
    try:
        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        ) or []
    except Exception as e:
        log.debug("CGWindowListCopyWindowInfo failed: %s", e)
        return []
    out = []
    for w in windows:
        bounds = w.get("kCGWindowBounds") or {}
        out.append(ActiveWindow(
            app=str(w.get("kCGWindowOwnerName", "") or ""),
            x=int(bounds.get("X", 0)),
            y=int(bounds.get("Y", 0)),
            w=int(bounds.get("Width", 0)),
            h=int(bounds.get("Height", 0)),
            title=str(w.get("kCGWindowName", "") or ""),
            window_id=int(w.get("kCGWindowNumber", 0) or 0),
        ))
    return out


def capture_window_image(window_id):
    """Capture a single window's pixels by CGWindowID and return a BGR
    numpy array, or ``None`` on failure.

    Uses ``CGWindowListCreateImage`` so the capture works **even when
    the window is on a different macOS Space** (the user can be looking
    at another desktop while we OCR a fullscreen Disney+ tab in the
    background). The window doesn't need to be focused.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        from Quartz import (
            CGWindowListCreateImage,
            CGRectNull,
            kCGWindowImageBoundsIgnoreFraming,
            kCGWindowImageNominalResolution,
            kCGWindowListOptionIncludingWindow,
            CGImageGetWidth,
            CGImageGetHeight,
            CGImageGetBytesPerRow,
            CGImageGetDataProvider,
            CGDataProviderCopyData,
        )
    except ImportError:
        return None
    try:
        image = CGWindowListCreateImage(
            CGRectNull,
            kCGWindowListOptionIncludingWindow,
            int(window_id),
            kCGWindowImageBoundsIgnoreFraming | kCGWindowImageNominalResolution,
        )
    except Exception as e:
        log.debug("CGWindowListCreateImage failed: %s", e)
        return None
    if image is None:
        return None
    try:
        width = int(CGImageGetWidth(image))
        height = int(CGImageGetHeight(image))
        bytes_per_row = int(CGImageGetBytesPerRow(image))
        provider = CGImageGetDataProvider(image)
        data = CGDataProviderCopyData(provider)
        buf = bytes(data)
        arr = np.frombuffer(buf, dtype=np.uint8)
        # CGImage stride may include padding; reshape using bytes_per_row.
        row_bytes = bytes_per_row
        arr = arr.reshape(height, row_bytes // 4, 4)[:, :width, :3]
        # CGImage native order is BGRA on x86 -> drop alpha to get BGR.
        return np.ascontiguousarray(arr)
    except Exception as e:
        log.debug("Failed to convert CGImage to numpy: %s", e)
        return None
