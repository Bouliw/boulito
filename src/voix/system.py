"""Simple, safe system commands: Mac volume, display sleep and screen lock,
open a website, run an allowed Shortcut, music (media keys).

Deliberately left out: shut down or restart, brightness, screenshots, files.
"""

import ctypes
import functools
import subprocess
import urllib.parse
from typing import Callable

from . import config, keys
from .i18n import t


class SystemError_(Exception):
    pass


def osascript(script: str) -> str:
    """Basic AppleScript (volume): no other app is controlled, no permission requested."""
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    return out.stdout.strip()


# --- Mac volume ----------------------------------------------------------------------------------------------
# Read and set directly in CoreAudio (0.2 ms) rather than through osascript (50 to 100 ms: one process every
# time). Same values as AppleScript: virtual main volume of the default output (the one in the Sound menu and
# on the keyboard keys), as an integer from 0 to 100. osascript remains the fallback if the output has no
# adjustable volume (HDMI, some sound cards) or if CoreAudio rejects a call. No permission is needed.

class _NoCoreAudio(Exception):
    """Property missing or not settable, or call rejected: osascript takes over."""


class _Address(ctypes.Structure):  # AudioObjectPropertyAddress
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]


def _code(text: str) -> int:  # four-letter CoreAudio code
    return int.from_bytes(text.encode(), "big")


_SYSTEM_OBJECT = 1                          # kAudioObjectSystemObject
_DEFAULT_OUTPUT = _code("dOut")             # kAudioHardwarePropertyDefaultOutputDevice: AudioObjectID (UInt32)
_MAIN_VOLUME = _code("vmvc")                # kAudioHardwareServiceDeviceProperty_VirtualMainVolume: Float32, 0 to 1
_MUTE = _code("mute")                       # kAudioDevicePropertyMute: UInt32, 0 or 1
_GLOBAL, _OUTPUT = _code("glob"), _code("outp")  # scopes
_MAIN_ELEMENT = 0                           # kAudioObjectPropertyElementMain


@functools.cache
def _core_audio() -> ctypes.CDLL:
    lib = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    address, object_id, status = ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_int32  # OSStatus
    lib.AudioObjectHasProperty.argtypes = [object_id, address]
    lib.AudioObjectHasProperty.restype = ctypes.c_uint8  # Boolean
    lib.AudioObjectIsPropertySettable.argtypes = [object_id, address, ctypes.POINTER(ctypes.c_uint8)]
    lib.AudioObjectGetPropertyData.argtypes = [object_id, address, ctypes.c_uint32, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    lib.AudioObjectSetPropertyData.argtypes = [object_id, address, ctypes.c_uint32, ctypes.c_void_p,
                                              ctypes.c_uint32, ctypes.c_void_p]
    for name in ("AudioObjectIsPropertySettable", "AudioObjectGetPropertyData", "AudioObjectSetPropertyData"):
        getattr(lib, name).restype = status
    return lib


def _has(device: int, selector: int) -> bool:
    """Does the output have this property (output scope, main element)?"""
    try:
        lib = _core_audio()
    except OSError as e:
        raise _NoCoreAudio(e) from None
    return bool(lib.AudioObjectHasProperty(device, ctypes.byref(_Address(selector, _OUTPUT, _MAIN_ELEMENT))))


def _get(device: int, selector: int, scope: int, ctype):
    """Value of a CoreAudio property (main element), of the given C type."""
    try:
        lib = _core_audio()
    except OSError as e:
        raise _NoCoreAudio(e) from None
    address = _Address(selector, scope, _MAIN_ELEMENT)
    if not lib.AudioObjectHasProperty(device, ctypes.byref(address)):
        raise _NoCoreAudio(f"{selector:#x} missing")
    value, size = ctype(), ctypes.c_uint32(ctypes.sizeof(ctype))
    status = lib.AudioObjectGetPropertyData(device, ctypes.byref(address), 0, None, ctypes.byref(size), ctypes.byref(value))
    if status != 0 or size.value != ctypes.sizeof(ctype):
        raise _NoCoreAudio(f"{selector:#x} read: {status}")
    return value.value


def _set(device: int, selector: int, ctype, value) -> None:
    """Write an output property: same address, same C type and same size as when reading (_get)."""
    try:
        lib = _core_audio()
    except OSError as e:
        raise _NoCoreAudio(e) from None
    address = _Address(selector, _OUTPUT, _MAIN_ELEMENT)
    settable = ctypes.c_uint8()
    if (not lib.AudioObjectHasProperty(device, ctypes.byref(address))
            or lib.AudioObjectIsPropertySettable(device, ctypes.byref(address), ctypes.byref(settable)) != 0
            or not settable.value):
        raise _NoCoreAudio(f"{selector:#x} not settable")
    data = ctype(value)
    status = lib.AudioObjectSetPropertyData(device, ctypes.byref(address), 0, None, ctypes.sizeof(ctype), ctypes.byref(data))
    if status != 0:
        raise _NoCoreAudio(f"{selector:#x} write: {status}")


def _output_device() -> int:
    device = _get(_SYSTEM_OBJECT, _DEFAULT_OUTPUT, _GLOBAL, ctypes.c_uint32)
    if not device:  # kAudioObjectUnknown: no output
        raise _NoCoreAudio("no default output")
    return device


def _read_output() -> tuple[int, bool]:
    """(volume from 0 to 100, muted) of the default output, read from CoreAudio.

    Rounded to the nearest, halves up, like AppleScript (0.125 → 13); an output that cannot be muted
    is not muted.
    """
    device = _output_device()
    level = int(_get(device, _MAIN_VOLUME, _OUTPUT, ctypes.c_float) * 100 + 0.5)
    muted = _has(device, _MUTE) and bool(_get(device, _MUTE, _OUTPUT, ctypes.c_uint32))
    return level, muted


def _set_level(level: int) -> bool:
    """Volume from 0 to 100 set in CoreAudio (like "set volume output volume"); False if impossible."""
    try:
        _set(_output_device(), _MAIN_VOLUME, ctypes.c_float, level / 100)
        return True
    except _NoCoreAudio:
        return False


def _set_muted(muted: bool) -> bool:
    """Mute or unmute the sound in CoreAudio (like "set volume output muted"); False if impossible."""
    try:
        _set(_output_device(), _MUTE, ctypes.c_uint32, int(muted))
        return True
    except _NoCoreAudio:
        return False


def _volume_settings() -> tuple[int | None, bool]:
    """(volume from 0 to 100, or None if the output has none; muted)."""
    import re

    try:
        return _read_output()
    except _NoCoreAudio:
        settings = osascript("get volume settings")
        level = re.search(r"output volume:(\d+)", settings)
        return (int(level[1]) if level else None), "output muted:true" in settings


def _set_volume(level: int) -> None:
    if not _set_level(level):
        osascript(f"set volume output volume {level}")


def volume(action: str, value: float | None = None) -> int:
    """Mac output volume: up, down, set (0-100), mute, unmute. Returns the volume."""
    try:
        current = _read_output()[0]
    except _NoCoreAudio:
        current = int(osascript("output volume of (get volume settings)") or 0)
    if action in ("mute", "unmute"):
        if not _set_muted(action == "mute"):
            osascript(f"set volume output muted {'true' if action == 'mute' else 'false'}")
        return current
    step = config.load().get("mac", {}).get("volume_step", 15)
    new = {"up": current + step, "down": current - step, "set": value if value is not None else current}[action]
    new = max(0, min(100, int(round(new))))
    level_done, unmuted = _set_level(new), _set_muted(False)  # "set volume output volume N without output muted"
    if not level_done:
        osascript(f"set volume output volume {new} without output muted")
    elif not unmuted:
        osascript("set volume output muted false")
    return new


class Ducker:
    """Lower the Mac's sound while listening, like Siri, then restore it: a video on the speakers
    no longer drowns out the voice. Runs on its own thread (CoreAudio answers at once, but the osascript fallback
    takes about 50 ms); wait() ensures the sound is back before the action (« monte le son » starts from the real volume).
    """

    def __init__(self, enabled: Callable[[], bool]) -> None:
        import queue
        import threading

        self.enabled = enabled
        self.jobs: "queue.Queue[str]" = queue.Queue()
        self.idle = threading.Event()
        self.idle.set()
        self.saved: int | None = None
        self.ducked: int | None = None  # level set when ducking: if it has changed, the user adjusted the sound themselves
        self.factor = 0.25
        threading.Thread(target=self._run, name="voix-sound", daemon=True).start()

    def duck(self, factor: float = 0.25) -> None:
        if self.enabled():
            self.idle.clear()
            self.factor = factor
            self.jobs.put("duck")

    def restore(self) -> None:
        self.idle.clear()
        self.jobs.put("restore")

    def wait(self, timeout: float = 1.5) -> None:
        self.idle.wait(timeout)

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                if job == "duck" and self.saved is None:
                    level, muted = _volume_settings()
                    if level is not None and not muted and level > 8:
                        self.saved = level
                        self.ducked = max(3, round(self.saved * self.factor))
                        _set_volume(self.ducked)
                elif job == "restore" and self.saved is not None:
                    level, _ = _volume_settings()
                    # Sound changed in the meantime (keyboard keys, command): that setting is kept
                    if level is None or abs(level - self.ducked) <= 1:
                        _set_volume(self.saved)
                    self.saved = self.ducked = None
            except Exception as e:  # sound must never block listening
                print(f"· sound: {e!r}", flush=True)
            finally:
                if self.jobs.empty():
                    self.idle.set()


def sleep_display() -> None:
    subprocess.run(["pmset", "displaysleepnow"], check=True, timeout=5)


def lock_screen() -> None:
    """Lock the session through the macOS function (the one behind Apple menu → Lock Screen).

    No simulated ⌃⌘Q: a frontmost app can intercept it (some apps do).
    """
    import ctypes

    try:
        ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login").SACLockScreenImmediate()
    except (OSError, AttributeError):
        sleep_display()  # also locks if a password is required on wake


# Standard macOS shortcuts, valid in most apps (no fine-grained control)
APP_SHORTCUTS = {"new": ("n", {"command": True}), "new_tab": ("t", {"command": True}),
                 "close_tab": ("w", {"command": True}), "find": ("f", {"command": True})}


def app_shortcut(action: str) -> str:
    """New document or note, new tab, close tab, find: in the frontmost app."""
    key, modifiers = APP_SHORTCUTS[action]
    bundle, name = keys.frontmost()
    if bundle in keys.SHELLS:
        raise keys.KeyboardError(f"shell:{name}")
    keys.press(key, **modifiers)
    return name


def local_info(what: str) -> str:
    """Sentence to say: time, date or battery (read on the Mac, no network)."""
    import datetime
    import re

    from .i18n import DAYS, MONTHS, language, spoken_duration

    now = datetime.datetime.now()
    lang = language()
    if what == "time":
        if lang == "fr":
            return t("info.time", time=f"{now.hour} h {now.minute:02d}" if now.minute else f"{now.hour} heures")
        if lang == "en":
            return t("info.time", time=now.strftime("%-I:%M %p"))
        return t("info.time", time=f"{now.hour}:{now.minute:02d}")  # es, de, it, pt: 24-hour clock
    if what == "date":
        day = DAYS.get(lang, DAYS["en"])[now.weekday()]
        month = MONTHS.get(lang, MONTHS["en"])[now.month - 1]
        return t("info.date", day=day, month=month, date=now.day)
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    m = re.search(r"(\d+)%;\s*([\w ]+?);\s*(?:(\d+):(\d+) remaining)?", out)
    if not m:
        return t("info.no_battery")
    state = m[2].strip()
    extra = t("info.charged") if state in ("charged", "finishing charge") else t("info.charging") if state == "charging" else ""
    if state == "discharging" and m[3] is not None and (int(m[3]) or int(m[4])):
        extra = t("info.remaining", duration=spoken_duration(int(m[3]) * 3600 + int(m[4]) * 60))
    return t("info.battery", percent=m[1], state=extra)


def open_url(url: str) -> str:
    """Open a web address (http or https only) in Safari."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in parsed.netloc:
        raise SystemError_(t("url.invalid", url=url))
    subprocess.run(["open", "-a", "Safari", url], check=True, timeout=10)
    return parsed.netloc


def allowed_shortcuts() -> list[str]:
    return list(config.load().get("shortcuts", {}).get("allowed", []))


def run_shortcut(name: str) -> str:
    """Run a macOS Shortcut, only if it is listed in [shortcuts] allowed in config.toml."""
    import difflib

    allowed = allowed_shortcuts()
    wanted = name.strip().lower()
    match = next((s for s in allowed if s.lower() == wanted), None)
    if match is None:
        close = difflib.get_close_matches(wanted, [s.lower() for s in allowed], n=1, cutoff=0.75)
        match = next((s for s in allowed if close and s.lower() == close[0]), None)
    if match is None:
        raise SystemError_(t("shortcut.not_allowed", name=name))
    subprocess.Popen(["shortcuts", "run", match], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return match


def media(action: str) -> None:
    """Music: play/pause, next, previous, via the Mac's media keys."""
    keys.media_key(action)


def _running(bundle_id: str) -> bool:
    from AppKit import NSWorkspace

    return any(app.bundleIdentifier() == bundle_id for app in NSWorkspace.sharedWorkspace().runningApplications())


def pause_players() -> bool:
    """« Arrête la musique »: pause whatever is playing, without ever restarting what was already stopped.

    The media key toggles play and pause: if nothing was playing, it started the music again. Music and YouTube
    are queried (permissions already granted for Music and YouTube); for other players (Spotify…),
    the media key is sent only if one is running. Returns True if something was paused.
    """
    paused = False
    if _running("com.apple.Music"):
        script = 'tell application "Music"\nif player state is playing then\npause\nreturn "paused"\nend if\nend tell'
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        paused = out.stdout.strip() == "paused"
    from . import youtube

    try:
        player = youtube.state()["player"]
        if player and player.get("state") == "playing":
            youtube.player("pause")
            paused = True
    except Exception:
        pass  # no YouTube tab, or Safari closed
    if not paused and any(_running(b) for b in ("com.spotify.client", "com.deezer.Deezer", "com.tidal.desktop")):
        keys.media_key("play_pause")  # another player is running: the media key pauses it
        paused = True
    return paused
