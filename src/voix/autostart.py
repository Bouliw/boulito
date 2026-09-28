"""Starts Boulito at login through a LaunchAgent ("Open at login" menu item).

The only project file outside its folder: ~/Library/LaunchAgents/local.boulito.plist (.dmg version:
io.github.bouliw.boulito.plist). It only opens Boulito.app (open -a), once, at login.
uninstall.sh (or "Erase everything" in the .dmg version's setup) removes it.
"""

import os
import plistlib
import subprocess
from pathlib import Path

from . import config

LABEL = config.BUNDLE_ID
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
APP = config.APP_PATH


def enabled() -> bool:
    return PLIST.exists()


def enable() -> None:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open("wb") as f:
        plistlib.dump({"Label": LABEL, "ProgramArguments": ["/usr/bin/open", "-a", str(APP)], "RunAtLoad": True}, f)


def disable() -> None:
    if PLIST.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        PLIST.unlink()
