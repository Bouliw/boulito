"""Talk key: a key or a combination, chosen by the user by pressing it.

Format in config.toml ([trigger] key): parts separated by "+":
- modifier keys, side included: alt_l, alt_r, cmd_l, cmd_r, ctrl_l, ctrl_r, shift_l, shift_r, fn;
- at most one other key: f1 … f19, or key:<code>:<display name> (e.g. key:49:␣ for Space).
Examples: "alt_r", "ctrl_l+alt_l", "f13", "ctrl_l+alt_l+key:49:␣".

Listening only starts after min_hold_ms of holding with no other key: a shortcut (⌘C, ⇧ + letter)
never turns the mic on. A combination with a key that types (letter, space…) is "swallowed"
so it is not typed into the frontmost app: this requires Accessibility (active keyboard tap).
Every key press received late is written to the app log (latency diagnostics).
"""

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

# name → (key code, left- or right-side mask in the event flags)
# (in Apple's display order: fn ⌃ ⌥ ⇧ ⌘)
MODIFIERS = {
    "fn": (63, 0x800000), "ctrl_l": (59, 0x01), "ctrl_r": (62, 0x2000), "alt_l": (58, 0x20), "alt_r": (61, 0x40),
    "shift_l": (56, 0x02), "shift_r": (60, 0x04), "cmd_l": (55, 0x08), "cmd_r": (54, 0x10),
}
FKEYS = {"f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
         "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64,
         "f18": 79, "f19": 80}
FKEY_NAMES = {code: name for name, code in FKEYS.items()}
SPECIAL_NAMES = {49: "␣", 36: "↩", 76: "⌤", 48: "⇥", 51: "⌫", 117: "⌦", 123: "←", 124: "→", 125: "↓", 126: "↑",
                 115: "↖", 119: "↘", 116: "⇞", 121: "⇟"}
FN_KEYS = set(FKEY_NAMES) | {123, 124, 125, 126, 115, 119, 116, 121, 117}  # macOS adds the fn flag to these on its own
ESCAPE = 53


def held_modifiers(flags: int) -> frozenset:
    return frozenset(name for name, (_, mask) in MODIFIERS.items() if flags & mask)


@dataclass(frozen=True)
class Hotkey:
    mods: frozenset
    key: int | None = None
    key_name: str = ""

    @classmethod
    def parse(cls, text: str) -> "Hotkey":
        mods, key, name = set(), None, ""
        for part in str(text).split("+"):
            part = part.strip()
            if part in MODIFIERS:
                mods.add(part)
            elif part in FKEYS:
                key, name = FKEYS[part], part.upper()
            elif part.startswith("key:"):
                _, code, *label = part.split(":", 2)
                key, name = int(code), (label[0] if label else code)
            else:
                raise ValueError(f"unknown key: {part}")
        if not mods and key is None:
            raise ValueError("no key")
        return cls(frozenset(mods), key, name)

    def __str__(self) -> str:
        parts = [m for m in MODIFIERS if m in self.mods]
        if self.key is not None:
            name = "".join(c for c in self.key_name if c not in '+:"\\') or f"#{self.key}"
            parts.append(FKEY_NAMES.get(self.key) or f"key:{self.key}:{name}")
        return "+".join(parts)

    def label(self) -> str:
        """Label such as "right ⌥", "left ⌃ + left ⌥", "left ⌃ + ␣", in the chosen language."""
        from .i18n import key_label

        parts = [key_label(m) for m in MODIFIERS if m in self.mods]
        if self.key is not None:
            parts.append(FKEY_NAMES[self.key].upper() if self.key in FKEY_NAMES else self.key_name)
        return " + ".join(parts)

    @property
    def types_text(self) -> bool:
        """Does the main key type something (letter, space…)? If so, it must be swallowed."""
        return self.key is not None and self.key not in FKEY_NAMES

    def matches(self, held: frozenset, code: int | None = None) -> bool:
        if self.key is not None and "fn" not in self.mods and (code in FN_KEYS or code is None):
            held = held - {"fn"}
        return held == self.mods


def check(hotkey: Hotkey, accessibility: bool) -> tuple[bool, str | None]:
    """(usable, id of the message to show) for a captured key."""
    if hotkey.key == ESCAPE:
        return False, "capture.refused"
    if hotkey.types_text and not (hotkey.mods - {"shift_l", "shift_r"}):
        return False, "capture.refused"  # letter alone, or uppercase: it types
    if hotkey.types_text and not accessibility:
        return False, "capture.needs_accessibility"
    if hotkey.key is None and hotkey.mods <= {"shift_l", "shift_r"}:
        return True, "capture.shift_warning"
    return True, None


def event_delay_ms(timestamp: int) -> float | None:
    """Time elapsed since a keyboard event's timestamp (in ns, or in machine clock units)."""
    now_ns = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
    for scale in (1.0, 125 / 3):  # 125/3: mach_absolute_time units → ns on Apple silicon
        delay = (now_ns - timestamp * scale) / 1e6
        if 0 <= delay < 600_000:
            return delay
    return None


class PushToTalk:
    """Calls on_press when the key (or combination) is held min_hold_ms with no other key,
    then on_release when it is released. Another key while listening, or Escape: on_cancel.
    """

    def __init__(self, key: str, min_hold_ms: int, on_press: Callable[[], None],
                 on_release: Callable[[], None], on_cancel: Callable[[str], None],
                 on_escape: Callable[[], None] | None = None) -> None:
        self.hotkey = Hotkey.parse(key)
        self.min_hold = min_hold_ms / 1000
        self.on_press, self.on_release, self.on_cancel = on_press, on_release, on_cancel
        self.on_escape = on_escape  # Escape outside listening: emergency stop of the current action
        self.lock = threading.RLock()
        self.down = self.started = self.cancelled = False
        self.timer: threading.Timer | None = None
        self.paused = False  # while a new key is being chosen
        self.tap = self.source = None
        self.swallowing = False
        self.own_pid = os.getpid()  # keys that Boulito sends itself are ignored

    # --- key state (main thread, except _begin) ---

    def _down(self) -> None:
        with self.lock:
            if self.down:
                return
            self.down, self.started, self.cancelled = True, False, False
            self.timer = threading.Timer(self.min_hold, self._begin)
            self.timer.daemon = True
            self.timer.start()

    def _begin(self) -> None:
        with self.lock:
            if self.down and not self.cancelled and not self.started:
                self.started = True
                self.on_press()

    def _up(self) -> None:
        with self.lock:
            if not self.down:
                return
            self.down = False
            if self.timer:
                self.timer.cancel()
            if self.started and not self.cancelled:
                self.on_release()
            self.started = False

    def _cancel(self, reason: str) -> None:
        with self.lock:
            if self.down and not self.cancelled:
                self.cancelled = True
                if self.timer:
                    self.timer.cancel()
                if self.started:
                    self.on_cancel(reason)

    # --- keyboard events ---

    def _handle(self, proxy, event_type, event, refcon):
        import Quartz

        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)
            print("· keyboard listening turned off by macOS (too slow), restarted", flush=True)
            return event
        if self.paused:
            return event
        # Keys sent by Boulito itself (Escape from a messaging recipe, ⌘N…): never taken for
        # the user's (otherwise that Escape would cancel its own command)
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == self.own_pid:
            return event
        hk = self.hotkey
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        held = held_modifiers(Quartz.CGEventGetFlags(event))
        was_down, swallow = self.down, False
        if event_type == Quartz.kCGEventFlagsChanged:
            if self.down:
                if not hk.mods <= held:
                    self._up()
                elif hk.key is None and held != hk.mods:
                    self._cancel("other key pressed")
            elif hk.key is None and hk.matches(held):
                self._down()
        elif event_type == Quartz.kCGEventKeyDown:
            repeat = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
            if hk.key is not None and code == hk.key and (self.down or hk.matches(held, code)):
                swallow = True
                if not repeat:
                    self._down()
            elif code == ESCAPE:
                if not self.down and self.on_escape:
                    self.on_escape()
                self._cancel("Escape")
            elif self.down:
                self._cancel("other key pressed")
        elif event_type == Quartz.kCGEventKeyUp and hk.key is not None and code == hk.key:
            swallow = self.down or hk.matches(held, code)
            self._up()
        if self.down != was_down:  # press or release: reception delay (diagnostics)
            delay = event_delay_ms(Quartz.CGEventGetTimestamp(event))
            if delay is not None and delay > 150:
                print(f"· key received {delay:.0f} ms late", flush=True)
        return None if swallow and self.swallowing else event

    def set_key(self, key: str) -> None:
        """Changes the key on the fly (assistant menu)."""
        hotkey = Hotkey.parse(key)
        self._cancel("key change")
        with self.lock:
            self.down = self.started = False
            self.hotkey = hotkey
        if self.tap is not None and self._wants_swallow() != self.swallowing:
            self.install()  # listen-only ↔ active tap (to swallow the key)

    def _wants_swallow(self) -> bool:
        from .keys import accessibility_allowed

        return self.hotkey.types_text and accessibility_allowed()

    def run(self) -> None:
        """Listens to the keyboard on the main thread until Ctrl-C."""
        import Quartz

        self.install()
        while True:  # 0.2 s slices so that Ctrl-C is still handled
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.2, False)

    def install(self) -> None:
        """Attaches the keyboard tap to the main thread's event loop (or re-attaches it)."""
        import Quartz

        if self.tap is not None:
            Quartz.CGEventTapEnable(self.tap, False)
            Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetMain(), self.source, Quartz.kCFRunLoopCommonModes)
            Quartz.CFMachPortInvalidate(self.tap)
            self.tap = self.source = None
        mask = 0
        for t in (Quartz.kCGEventFlagsChanged, Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            mask |= Quartz.CGEventMaskBit(t)
        self.swallowing = self._wants_swallow()
        option = Quartz.kCGEventTapOptionDefault if self.swallowing else Quartz.kCGEventTapOptionListenOnly
        self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, option, mask,
                                           self._handle, None)
        if self.tap is None and self.swallowing:  # no Accessibility: listen only
            self.swallowing = False
            self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                               Quartz.kCGEventTapOptionListenOnly, mask, self._handle, None)
        if self.tap is None:
            raise PermissionError("Input Monitoring denied")
        self.source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), self.source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
