"""Send a message in a messaging app: Discord, Messages, WhatsApp, Telegram.

For each app, a recipe of keyboard shortcuts (RECIPES, to adjust here if an app changes):
open the conversation, write the message, then send it or erase it. Sending only happens
after the user's "yes" (see safety.send_message): the message is first written
in the conversation, visible on screen, and read back aloud with the recipient.
When the app shows the conversation name in its window title (Discord, Messages),
Boulito checks it before writing, reading it through Accessibility (never through a screenshot).
"""

import ctypes
import difflib
import time
import unicodedata

from . import apps, keys

# Steps: ("key", key, {modifiers}) or ("type", "{recipient}") or ("wait", seconds)
RECIPES = {
    "discord": {
        "app": "Discord",
        "open_chat": [("key", "k", {"command": True}), ("wait", 0.4), ("type", "{recipient}"), ("wait", 0.9),
                      ("key", "return", {}), ("wait", 0.8)],
        "title_check": True,
    },
    "messages": {
        "app": "Messages",
        "open_chat": [("key", "n", {"command": True}), ("wait", 0.6), ("type", "{recipient}"), ("wait", 1.2),
                      ("key", "return", {}), ("wait", 0.5), ("key", "tab", {}), ("wait", 0.3)],
        "title_check": False,
    },
    "whatsapp": {
        "app": "WhatsApp",
        "open_chat": [("key", "f", {"command": True}), ("wait", 0.5), ("type", "{recipient}"), ("wait", 1.2),
                      ("key", "down", {}), ("wait", 0.2), ("key", "return", {}), ("wait", 0.8)],
        "title_check": False,
    },
    "telegram": {
        "app": "Telegram",
        "open_chat": [("key", "escape", {}), ("wait", 0.2), ("key", "f", {"command": True}), ("wait", 0.5),
                      ("type", "{recipient}"), ("wait", 1.2), ("key", "return", {}), ("wait", 0.8)],
        "title_check": False,
    },
}
SEND = [("key", "return", {})]
CLEAR = [("key", "a", {"command": True}), ("key", "delete", {})]


class MessagingError(Exception):
    pass


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    return "".join(c for c in text if c.isalnum() or c == " ").strip()


def recipe(app_name: str) -> dict:
    key = fold(app_name).replace(" ", "")
    if key in ("imessage", "sms", "message"):
        key = "messages"
    if key not in RECIPES:
        raise MessagingError(f"app:{app_name}")
    return RECIPES[key]


def run_steps(steps: list, recipient: str = "") -> None:
    for step in steps:
        if step[0] == "wait":
            time.sleep(step[1])
        elif step[0] == "type":
            keys.type_text(step[1].format(recipient=recipient))
        else:
            keys.press(step[1], **step[2])


# --- Frontmost window title (Accessibility) ---------------------------------------

_ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
_ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
_ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
_ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
_ax.AXUIElementCopyAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_cf.CFRelease.argtypes = [ctypes.c_void_p]
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p
_cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFGetTypeID.restype = ctypes.c_ulong
_cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
_cf.CFStringGetTypeID.restype = ctypes.c_ulong
UTF8 = 0x08000100


def _attribute(element: int, name: str) -> int | None:
    """Value of an accessibility attribute (to release with CFRelease), or None."""
    key = _cf.CFStringCreateWithCString(None, name.encode(), UTF8)
    try:
        value = ctypes.c_void_p()
        if _ax.AXUIElementCopyAttributeValue(element, key, ctypes.byref(value)):
            return None  # no window, or no Accessibility permission
        return value.value
    finally:
        _cf.CFRelease(key)


def _text(ref: int) -> str:
    if _cf.CFGetTypeID(ref) != _cf.CFStringGetTypeID():
        return ""
    buffer = ctypes.create_string_buffer(1024)
    return buffer.value.decode() if _cf.CFStringGetCString(ref, buffer, len(buffer), UTF8) else ""


def focused_role() -> str:
    """Accessibility role of the element with keyboard focus ("AXTextArea", "AXWebArea"…), "" if unreadable."""
    from AppKit import NSWorkspace

    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return ""
    app = _ax.AXUIElementCreateApplication(front.processIdentifier())
    try:
        element = _attribute(app, "AXFocusedUIElement")
        if not element:
            return ""
        try:
            role = _attribute(element, "AXRole")
            if not role:
                return ""
            try:
                return _text(role)
            finally:
                _cf.CFRelease(role)
        finally:
            _cf.CFRelease(element)
    finally:
        _cf.CFRelease(app)


def front_window_title() -> str:
    """Title of the frontmost app's active window ("" if unreadable)."""
    from AppKit import NSWorkspace

    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return ""
    app = _ax.AXUIElementCreateApplication(front.processIdentifier())
    try:
        window = _attribute(app, "AXFocusedWindow")
        if not window:
            return ""
        try:
            title = _attribute(window, "AXTitle")
            if not title:
                return ""
            try:
                return _text(title)
            finally:
                _cf.CFRelease(title)
        finally:
            _cf.CFRelease(window)
    finally:
        _cf.CFRelease(app)


def title_matches(title: str, recipient: str) -> bool:
    """Does the requested name appear in the window title (allowing one transcription error)?"""
    words, wanted = fold(title).split(), fold(recipient)
    if wanted and wanted in fold(title):
        return True
    return any(difflib.SequenceMatcher(None, w, wanted).ratio() >= 0.75 for w in words)


# --- Flow --------------------------------------------------------------------------

PREPARED: dict = {}  # prepared conversation: Return or erasing only target it


def prepare(app_name: str, recipient: str, text: str) -> tuple[str, bool | None]:
    """Opens the conversation and writes the message, without sending it.

    Returns (app name, recipient verified: True, False, or None if the app does not allow it).
    """
    r = recipe(app_name)
    if not keys.accessibility_allowed():
        raise keys.KeyboardError("accessibility")
    apps.switch_app(r["app"])
    deadline = time.monotonic() + 8
    while keys.frontmost()[1] != r["app"]:
        if time.monotonic() > deadline:
            raise MessagingError(f"not_front:{r['app']}")
        time.sleep(0.2)
    time.sleep(0.4)
    run_steps(r["open_chat"], recipient)
    verified = title_matches(front_window_title(), recipient) if r["title_check"] else None
    if verified is False:
        return r["app"], False  # wrong conversation: write nothing
    keys.type_text(text)
    PREPARED.update(app=r["app"], recipient=recipient, title_check=r["title_check"])
    return r["app"], verified


def _still_there() -> None:
    """During the question (≈ 10 s), the user may have switched apps: Return or ⌘A + Delete would go elsewhere
    (a terminal, a document). Acts only if the prepared conversation is still frontmost."""
    if not PREPARED or keys.frontmost()[1] != PREPARED["app"]:
        raise MessagingError("not_front")
    if PREPARED["title_check"] and title_matches(front_window_title(), PREPARED["recipient"]) is False:
        raise MessagingError("not_front")


def send() -> None:
    _still_there()
    run_steps(SEND)
    PREPARED.clear()


def clear() -> None:
    _still_there()
    run_steps(CLEAR)
    PREPARED.clear()
