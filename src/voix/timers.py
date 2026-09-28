"""Timers and reminders (« minuteur 10 minutes », « rappelle-moi dans 20 minutes de sortir le linge »).

They live in Boulito's process: nothing is written to disk, and they are lost if
Boulito quits. When one is due: sound, notification and spoken reminder. A reminder's text
always comes from the dictated words (checked by the executor), never from a page or the LLM.
"""

import threading
import time
from dataclasses import dataclass

from . import ui
from .i18n import spoken_duration, t

MAX_SECONDS = 7 * 24 * 3600  # beyond 24 h, a reminder (Reminders app) replaces the timer


@dataclass
class Timer:
    seconds: float
    label: str
    ends_at: float
    handle: threading.Timer

    @property
    def left(self) -> float:
        return max(0.0, self.ends_at - time.monotonic())


_timers: list[Timer] = []
NATIVE = [False]  # a timer was started in Clock (Boulito can neither read nor cancel it)
# Timers started in Clock by Boulito: (expected end, duration). The shortcut only reads the "current" timer:
# this list tells whether there are several (we say so rather than pick one at random).
LAUNCHED: list[tuple[float, float]] = []


def launched(seconds: float) -> None:
    with _lock:
        LAUNCHED.append((time.monotonic() + seconds, seconds))


def running_in_clock() -> int:
    """Number of timers Boulito started in Clock that are not finished yet (according to its list)."""
    now = time.monotonic()
    with _lock:
        LAUNCHED[:] = [x for x in LAUNCHED if x[0] > now]
        return len(LAUNCHED)


def forget_closest(left: float) -> None:
    """After a cancellation: removes from the list the timer whose remaining time was closest."""
    now = time.monotonic()
    with _lock:
        if LAUNCHED:
            LAUNCHED.remove(min(LAUNCHED, key=lambda x: abs((x[0] - now) - left)))
_lock = threading.Lock()


def start(seconds: float, label: str = "") -> Timer:
    if not 1 <= seconds <= MAX_SECONDS:
        raise ValueError(f"duration out of range: {seconds} s")
    handle = threading.Timer(seconds, lambda: _ring(timer))
    handle.daemon = True
    timer = Timer(seconds, label.strip(), time.monotonic() + seconds, handle)
    with _lock:
        _timers.append(timer)
    handle.start()
    return timer


def _ring(timer: Timer) -> None:
    with _lock:
        if timer not in _timers:
            return  # cancelled in the meantime
        _timers.remove(timer)
    text = t("timer.reminder", label=timer.label) if timer.label else t("timer.done")
    for _ in range(3):
        ui.sound("timer")
        time.sleep(0.7)
    ui.notify(ui.APP_NAME, text)
    ui.say(text)


def active() -> list[Timer]:
    with _lock:
        return sorted(_timers, key=lambda x: x.ends_at)


def cancel() -> int:
    """Cancels all timers and reminders; returns how many."""
    with _lock:
        cancelled = list(_timers)
        _timers.clear()
    for timer in cancelled:
        timer.handle.cancel()
    return len(cancelled)


def describe() -> str:
    """Sentence to speak: "4 minutes 30" (one timer) or "2 timers: …"."""
    timers = active()
    if not timers:
        return t("timer.none")
    parts = [(f"{x.label} : " if x.label else "") + spoken_duration(x.left, precise=True) for x in timers]
    return t("timer.left", left=", ".join(parts))
