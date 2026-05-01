"""launchd LaunchAgent generation and install/uninstall.

We render a small property-list XML file that runs `lang-view watch`
under the user's login session, then drop it under
`~/Library/LaunchAgents/` and load it with `launchctl`. Bootout is
the symmetric uninstall.

The plist generator is pure and fully tested; the bits that shell
out to `launchctl` are isolated so they can be skipped on non-macOS
test runs.
"""

from __future__ import annotations

import getpass
import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_LABEL = "ai.lang-view.watcher"


def default_agent_path(label=DEFAULT_LABEL):
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def render_plist(*, label=DEFAULT_LABEL, program_args=None,
                 working_dir=None, stdout_path=None, stderr_path=None,
                 env=None, run_at_load=True, keep_alive=True):
    """Return a launchd plist as bytes.

    `program_args` is a list of argv tokens to exec. If omitted, we
    default to `[sys.executable, '-m', 'lang_view', 'watch']` so the
    agent invokes whatever Python is currently being used.
    """
    if program_args is None:
        program_args = [sys.executable, "-m", "lang_view", "watch"]
    plist = {
        "Label": label,
        "ProgramArguments": [str(a) for a in program_args],
        "RunAtLoad": bool(run_at_load),
        "KeepAlive": bool(keep_alive),
        "ProcessType": "Interactive",
    }
    if working_dir is not None:
        plist["WorkingDirectory"] = str(working_dir)
    if stdout_path is not None:
        plist["StandardOutPath"] = str(stdout_path)
    if stderr_path is not None:
        plist["StandardErrorPath"] = str(stderr_path)
    if env:
        plist["EnvironmentVariables"] = {k: str(v) for k, v in env.items()}
    return plistlib.dumps(plist)


def write_agent(path, plist_bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plist_bytes)
    return path


def _gui_target():
    return f"gui/{os.getuid()}"


def install_agent(path, runner=subprocess.run):
    """`launchctl bootstrap gui/<uid> <path>`."""
    return runner(
        ["launchctl", "bootstrap", _gui_target(), str(path)],
        check=True,
    )


def uninstall_agent(label=DEFAULT_LABEL, runner=subprocess.run):
    """`launchctl bootout gui/<uid>/<label>` and unlink the plist."""
    target = f"{_gui_target()}/{label}"
    runner(["launchctl", "bootout", target], check=False)
    plist = default_agent_path(label)
    if plist.exists():
        plist.unlink()


def is_launchctl_available():
    return shutil.which("launchctl") is not None


def whoami():
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")
