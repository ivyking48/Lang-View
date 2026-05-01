"""Cross-process pause coordination via a flag file.

The watch loop and the menubar UI live in separate processes, so we
can't share an in-memory ``PauseSwitch``. Instead the menubar touches
or removes a small flag file, and the watch loop checks for the file
at the top of each cycle.

This is intentionally not signal-based: SIGUSR1 only carries a toggle
and the menubar would have no way to *read* the watch loop's current
state. A file flag is queryable by anyone, persists across menubar
restarts, and is trivial to inspect or override from a shell.
"""

from pathlib import Path


class FilePauseFlag:
    def __init__(self, path):
        self.path = Path(path)

    def is_paused(self):
        return self.path.exists()

    def set_paused(self, paused):
        if paused:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
        else:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def toggle(self):
        new = not self.is_paused()
        self.set_paused(new)
        return new
