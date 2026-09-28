"""Native macOS reminders and timers, as with Siri.

- Reminders: a reminder is created in the Reminders app (default list), with an alert at the given time;
  macOS sends the notification, even if Boulito has quit. Through AppleScript, the text passed as an argument
  (never pasted into the script); asks once for the Automation → Reminders permission.
- Timers: the Clock app can only be driven by the Shortcuts app. If the user has created the shortcut
  "Minuteur Boulito" (or "Boulito Timer"), Boulito runs it with the duration in seconds; otherwise it
  keeps its own timer (see timers.py).
"""

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config

TIMER_SHORTCUTS = ("Minuteur Boulito", "Boulito Timer")
CONTROL_SHORTCUTS = ("Minuteur Boulito – Gestion", "Boulito Timer Control")  # cancel, time remaining
# Shortcut content, unsigned and free of personal data, shipped with the project. Boulito signs it
# on the user's Mac (shortcuts sign --mode anyone: macOS contacts Apple for the signature) then
# opens it: Shortcuts offers to add it, in one click. An already signed shortcut is never published (.gitignore).
SHORTCUT_SOURCE = config.PROJECT_DIR / "shortcuts" / "Minuteur Boulito.plist"
CONTROL_SOURCE = config.PROJECT_DIR / "shortcuts" / "Minuteur Boulito – Gestion.plist"
SIGNED_DIR = config.CACHE_DIR / "shortcuts"  # last signed copy, opened by Shortcuts (.cache/: never published)
CLOCK_MAX_SECONDS = 24 * 3600 - 1  # Clock rejects 24 h or more ("must be less than 24 hours", tested)

ADD_REMINDER = '''on run argv
  set theName to item 1 of argv
  set theDate to (current date) + ((item 2 of argv) as integer)
  tell application "Reminders"
    tell default list to make new reminder with properties {name:theName, remind me date:theDate, due date:theDate}
  end tell
  return "ok"
end run'''


LIST_REMINDERS = '''set out to ""
tell application "Reminders"
  set pending to (reminders whose completed is false)
  repeat with r in pending
    set d to due date of r
    set out to out & (name of r) & tab
    if d is not missing value then set out to out & ((year of d) as text) & "-" & ((month of d as integer) as text) & "-" & ((day of d) as text) & " " & ((hours of d) as text) & ":" & ((minutes of d) as text)
    set out to out & linefeed
  end repeat
end tell
return out'''


class ReminderError(Exception):
    pass


def add_reminder(label: str, seconds: float) -> None:
    out = subprocess.run(["osascript", "-e", ADD_REMINDER, label[:200], str(int(seconds))],
                         capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        error = out.stderr.strip()
        raise ReminderError("not_allowed" if "-1743" in error or "Not authorized" in error else error or "osascript")


# Shortcut list kept for 30 s: "shortcuts list" (10 to 20 ms, one process) ran up to 4 times per
# timer command and 3 times every 3 s in the setup window. Right after an install,
# it is reread every 2 s for 5 min: the user adds the shortcut in Shortcuts (or creates it
# by hand), and the setup window must see it right away.
NAMES_TTL = 30.0
WATCH_TTL, WATCH_SECONDS = 2.0, 300.0
_names_lock = threading.Lock()
_names: tuple[float, tuple[str, ...]] | None = None  # (read time, names)
_watch_until = 0.0


def _shortcut_names() -> tuple[str, ...] | None:
    """Shortcut names from the Shortcuts app; None if the list cannot be read (never cached)."""
    global _names
    with _names_lock:  # two threads asking at the same time: a single read
        ttl = WATCH_TTL if time.monotonic() < _watch_until else NAMES_TTL
        if _names is not None and time.monotonic() - _names[0] < ttl:
            return _names[1]
        try:
            out = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        names = tuple(out.stdout.splitlines())
        if out.returncode == 0:
            _names = (time.monotonic(), names)
        return names


def forget_shortcuts(watch: bool = False) -> None:
    """Forget the cached list (shortcut installed, or a failed run: it may have been deleted).

    watch: reread the list every 2 s for 5 min (install in progress in Shortcuts).
    """
    global _names, _watch_until
    with _names_lock:
        _names = None
        if watch:
            _watch_until = time.monotonic() + WATCH_SECONDS


def _installed(candidates: tuple[str, ...]) -> str | None:
    names = _shortcut_names()
    if names is None:
        return None
    return next((n for n in candidates if n in names), None)


def list_reminders() -> list[dict]:
    """Uncompleted reminders: [{"name", "due" (datetime or None)}], dated ones first."""
    import datetime as dt

    out = subprocess.run(["osascript", "-e", LIST_REMINDERS], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        error = out.stderr.strip()
        raise ReminderError("not_allowed" if "-1743" in error or "Not authorized" in error else error or "osascript")
    items = []
    for line in out.stdout.splitlines():
        name, _, due = line.partition("\t")
        when = None
        if due.strip():
            try:
                when = dt.datetime.strptime(due.strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                pass
        if name.strip():
            items.append({"name": name.strip(), "due": when})
    return sorted(items, key=lambda r: (r["due"] is None, r["due"] or dt.datetime.max))


def duplicates() -> list[str]:
    """Copies created by "Keep Both" instead of "Replace": "Minuteur Boulito 1", "Boulito Timer 2"…"""
    import re

    names = _shortcut_names()
    if names is None:
        return []
    ours = "|".join(re.escape(n) for n in TIMER_SHORTCUTS + CONTROL_SHORTCUTS)
    return [n for n in names if re.fullmatch(rf"(?:{ours}) \d{{1,2}}", n)]


def timer_shortcut() -> str | None:
    """Name of the "Minuteur Boulito" shortcut if it exists in the Shortcuts app."""
    return _installed(TIMER_SHORTCUTS)


def control_shortcut() -> str | None:
    """Name of the "Minuteur Boulito – Gestion" shortcut (cancel, time remaining) if it exists."""
    return _installed(CONTROL_SHORTCUTS)


def _run(name: str, text: str, want_output: bool = False) -> tuple[bool, str]:
    """Run a shortcut with a text input; (succeeded, output text)."""
    folder = Path(tempfile.mkdtemp(prefix="boulito-"))
    source, result = folder / "input.txt", folder / "output.txt"
    source.write_text(text)
    command = ["shortcuts", "run", name, "--input-path", str(source)]
    if want_output:
        command += ["--output-path", str(result), "--output-type", "public.plain-text"]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            forget_shortcuts()  # deleted or renamed since the last read? the next read will tell
        return out.returncode == 0, (result.read_text(errors="ignore").strip() if result.exists() else "")
    finally:
        for f in (source, result):
            f.unlink(missing_ok=True)
        folder.rmdir()


def cancel_native_timer() -> bool:
    name = control_shortcut()
    return bool(name) and _run(name, "cancel")[0]


def native_remaining() -> float | None:
    """Seconds left on the Clock timer (0 if there is none), or None if unreadable."""
    import re

    from .router import fold, timer_seconds

    name = control_shortcut()
    if not name:
        return None
    ok, text = _run(name, "status", want_output=True)
    if not ok:
        return None
    return parse_remaining(text)


UNIT_SECONDS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "seconde": 1, "secondes": 1,
                "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600,
                "hour": 3600, "hours": 3600, "heure": 3600, "heures": 3600}


def parse_remaining(text: str) -> float | None:
    """Output of "Get Current Timer" in seconds: "55.659 sec" → 55.659, "0 sec" or nothing → 0 (no timer)."""
    import re

    text = text.strip().lower()
    if not text:
        return 0.0
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}(?:[.,]\d+)?", text):  # "12:30", "1:05:00"
        total = 0.0
        for part in text.replace(",", ".").split(":"):
            total = total * 60 + float(part)
        return total
    total, found = 0.0, False
    for number, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*([a-zé]*)", text):
        total += float(number.replace(",", ".")) * UNIT_SECONDS.get(unit, 1)
        found = True
    return total if found else None


def install_timer_shortcut(name: str, source: Path = SHORTCUT_SOURCE) -> bool:
    """Sign a shipped shortcut and open it in Shortcuts; False if signing fails (manual steps)."""
    import shutil

    if not source.exists():
        return False
    folder = Path(tempfile.mkdtemp(prefix="boulito-shortcut-"))
    try:
        unsigned, signed = folder / "source" / f"{name}.shortcut", folder / f"{name}.shortcut"
        unsigned.parent.mkdir()
        shutil.copy(source, unsigned)
        out = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned), "--output", str(signed)],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0 or not signed.exists() or signed.read_bytes()[:4] != b"AEA1":
            print(f"· shortcut: signing failed ({out.stderr.strip()[:200]})", flush=True)
            return False
        # Shortcuts reads the file after we return (on opening, maybe even at "Add Shortcut"): the opened
        # copy stays in the project (replaced at the next install), the temporary folder is deleted
        SIGNED_DIR.mkdir(parents=True, exist_ok=True)
        kept = SIGNED_DIR / signed.name
        shutil.copy(signed, kept)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    subprocess.run(["open", str(kept)])  # Shortcuts: "Add Shortcut"
    forget_shortcuts(watch=True)
    return True


def install_shortcuts(names: dict[str, str]) -> list[str]:
    """Install the missing shortcuts (all of them if already there: update). Returns those opened.

    names: {"start": name of the start shortcut, "control": name of the control shortcut}, in the chosen language.
    """
    forget_shortcuts()  # decision made on the current list, not the cached one
    wanted = [(kind, source) for kind, source, present in (("start", SHORTCUT_SOURCE, timer_shortcut()),
                                                            ("control", CONTROL_SOURCE, control_shortcut())) if not present]
    if not wanted:  # everything is there: offer the project's version (Shortcuts asks "Replace")
        wanted = [("start", SHORTCUT_SOURCE), ("control", CONTROL_SOURCE)]
    try:
        return [kind for kind, source in wanted if install_timer_shortcut(names[kind], source)]
    finally:  # succeeded or not (manual steps next): the setup window watches for the new shortcut
        forget_shortcuts(watch=True)


def start_native_timer(seconds: float) -> bool:
    """Start the Clock timer through the shortcut; False if it does not exist or failed."""
    name = timer_shortcut()
    if name is None or not 1 <= seconds <= CLOCK_MAX_SECONDS:
        return False
    # Always a single integer of seconds, never the dictated text: "Get Numbers from Input" would extract
    # 1 and 30 from « 1 min 30 »
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(str(int(round(seconds))))
    try:
        out = subprocess.run(["shortcuts", "run", name, "--input-path", f.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(f.name)
    if out.returncode != 0:
        forget_shortcuts()  # deleted or renamed since the last read? the next read will tell
    return out.returncode == 0
