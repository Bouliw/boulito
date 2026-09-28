"""Simulated keyboard: typing text, keyboard shortcuts, media keys.

Requires the Accessibility permission, granted to Boulito.app only (never to Terminal).
Safeguards, checked before every keystroke:
- never in a terminal or a shell (that would be free-form shell access);
- never in a password field (secure input enabled);
- never a line break in the typed text: type_text never presses Return.
"""

import ctypes
import time

import objc

# Apps where Boulito never types: terminals, and editors that include one
SHELLS = {
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "com.mitchellh.ghostty",
    "io.alacritty", "net.kovidgoyal.kitty", "com.github.wez.wezterm", "co.zeit.hyper", "org.tabby",
    "com.microsoft.VSCode", "com.todesktop.230313mzl4w4u92", "dev.zed.Zed", "com.apple.dt.Xcode",
    "com.microsoft.VSCodeInsiders", "com.vscodium", "com.visualstudio.code.oss",
    # Virtual machines and remote desktops: the text would go to another system's command prompt
    "com.vmware.fusion", "com.parallels.desktop.console", "com.utmapp.UTM", "com.microsoft.rdc.macos",
    "com.microsoft.rdc.osx.beta", "com.teamviewer.TeamViewer", "com.jetbrains.fleet",
}
SHELL_PREFIXES = ("com.jetbrains.",)  # IntelliJ, PyCharm, WebStorm…: built-in terminal
KEYCODES = {"return": 36, "tab": 48, "delete": 51, "escape": 53, "down": 125, "up": 126,
            "a": 0, "f": 3, "k": 40, "n": 45, "q": 12, "t": 17, "w": 13}
MEDIA = {"play_pause": 16, "next": 17, "previous": 18}  # NX_KEYTYPE_*

_services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
_services.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
_services.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
_carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool


class KeyboardError(Exception):
    pass


def accessibility_allowed(prompt: bool = False) -> bool:
    """Does Boulito have the Accessibility permission? prompt=True adds it to the list in Settings."""
    from Foundation import NSDictionary

    options = NSDictionary.dictionaryWithObject_forKey_(prompt, "AXTrustedCheckOptionPrompt")
    return _services.AXIsProcessTrustedWithOptions(objc.pyobjc_id(options))


def frontmost() -> tuple[str, str]:
    """(identifier, name) of the frontmost app."""
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:  # rare (Space switch, lock screen): no app
        return "", ""
    return str(app.bundleIdentifier() or ""), str(app.localizedName() or "")


def check_can_type() -> None:
    """Refuses to type without permission, in a shell or in a password field."""
    if not accessibility_allowed():
        raise KeyboardError("accessibility")
    bundle, name = frontmost()
    if bundle in SHELLS or bundle.startswith(SHELL_PREFIXES):
        raise KeyboardError(f"shell:{name}")
    if _carbon.IsSecureEventInputEnabled():
        raise KeyboardError("password")
    # No check of the text field: Boulito is meant to control the Mac hands-free, without clicks; the text goes
    # wherever the frontmost app receives it


def _post(event) -> None:
    import Quartz

    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def type_text(text: str) -> None:
    """Types the text in the frontmost app, never submitting it (no Return)."""
    import Quartz

    check_can_type()
    text = " ".join(text.split())  # no line breaks: one would count as Return
    start_app = frontmost()[0]
    for i in range(0, len(text), 16):
        if i and frontmost()[0] != start_app:  # the user switched apps while typing: stop
            raise KeyboardError(f"shell:{frontmost()[1]}" if frontmost()[0] in SHELLS else "moved")
        chunk = text[i:i + 16]
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
            _post(event)
        time.sleep(0.01)


def press(key: str, command: bool = False, shift: bool = False, control: bool = False, option: bool = False) -> None:
    """Presses a key, with ⌘, ⇧, ⌃ or ⌥ if asked."""
    import Quartz

    if not accessibility_allowed():
        raise KeyboardError("accessibility")
    flags = ((Quartz.kCGEventFlagMaskCommand if command else 0) | (Quartz.kCGEventFlagMaskShift if shift else 0)
             | (Quartz.kCGEventFlagMaskControl if control else 0) | (Quartz.kCGEventFlagMaskAlternate if option else 0))
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, KEYCODES[key], down)
        Quartz.CGEventSetFlags(event, flags)
        _post(event)
    time.sleep(0.02)


# New line without sending: ⇧ + Return (Discord, WhatsApp, Telegram, Slack, Notes, TextEdit…);
# in Messages it is ⌥ + Return (⇧ + Return is not documented there by Apple). Never Return alone.
NEWLINE_WITH_OPTION = {"com.apple.MobileSMS"}


def newline(count: int = 1) -> None:
    check_can_type()
    option = frontmost()[0] in NEWLINE_WITH_OPTION
    for _ in range(count):
        press("return", shift=not option, option=option)


def backspace(count: int) -> None:
    """Deletes count characters (« efface ça » during dictation), never typing anything else."""
    import Quartz

    check_can_type()
    for i in range(count):
        for down in (True, False):
            _post(Quartz.CGEventCreateKeyboardEvent(None, KEYCODES["delete"], down))
        if i % 20 == 19:
            time.sleep(0.01)


def media_key(action: str) -> None:
    """Media key (play/pause, next, previous): controls whichever app is playing."""
    from AppKit import NSEvent

    if not accessibility_allowed():
        raise KeyboardError("accessibility")
    code = MEDIA[action]
    for state in (0xA, 0xB):  # key down, then key up
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), state << 8, 0, 0, None, 8, (code << 16) | (state << 8), -1)  # NSEventTypeSystemDefined
        _post(event.CGEvent())
