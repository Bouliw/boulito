"""The "Talk key" window: the user presses the key or combination they want.

Key presses are read in this window only (AppKit local monitor: no extra permission),
and the usual key listener is paused while choosing. A key that types text on its
own (letter, space…) or Escape is refused; a combination with such a key requires
Accessibility, so that the key is not also typed into the frontmost app.
"""

from typing import Callable

from . import keys
from .hotkey import ESCAPE, FKEY_NAMES, FN_KEYS, SPECIAL_NAMES, Hotkey, check, held_modifiers
from .i18n import t

_window = None


def key_name(event) -> str:
    code = event.keyCode()
    if code in FKEY_NAMES:
        return FKEY_NAMES[code].upper()
    if code in SPECIAL_NAMES:
        return SPECIAL_NAMES[code]
    chars = str(event.charactersIgnoringModifiers() or "").strip()
    # "+" separates keys in config.toml, ":" separates the fields of key:<code>:<name>: these signs are never a name
    if not chars or any(c in chars for c in '+:"\\'):
        return f"#{code}"
    return chars.upper()


def _target_class():
    from Foundation import NSObject

    class BoulitoCaptureTarget(NSObject):
        def use_(self, sender):
            self.capture.finish(True)

        def retry_(self, sender):
            self.capture.reset()

        def cancel_(self, sender):
            self.capture.finish(False)

        def windowWillClose_(self, notification):
            self.capture.finish(False, closing=True)

    return BoulitoCaptureTarget


_Target = None


class KeyCapture:
    def __init__(self, on_choose: Callable[[str], None], on_pause: Callable[[bool], None]) -> None:
        global _Target
        from AppKit import (NSApplication, NSBackingStoreBuffered, NSEvent, NSFont, NSMakeRect, NSStackView,
                            NSTextField, NSUserInterfaceLayoutOrientationVertical, NSWindow,
                            NSWindowStyleMaskClosable, NSWindowStyleMaskTitled, NSButton, NSEdgeInsetsMake)

        _Target = _Target or _target_class()
        self.on_choose, self.on_pause = on_choose, on_pause
        self.target = _Target.alloc().init()
        self.target.capture = self
        self.hotkey: Hotkey | None = None
        self.peak: set = set()
        self.closed = False

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 440, 220), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        self.window.setTitle_(t("capture.title"))
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self.target)
        stack = NSStackView.alloc().init()
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setSpacing_(14)
        stack.setEdgeInsets_(NSEdgeInsetsMake(22, 24, 22, 24))
        prompt = NSTextField.wrappingLabelWithString_(t("capture.prompt"))
        prompt.setPreferredMaxLayoutWidth_(390)
        self.combo = NSTextField.labelWithString_(t("capture.waiting"))
        self.combo.setFont_(NSFont.boldSystemFontOfSize_(24))
        self.message = NSTextField.wrappingLabelWithString_("")
        self.message.setPreferredMaxLayoutWidth_(390)
        self.message.setFont_(NSFont.systemFontOfSize_(11))
        self.use = NSButton.buttonWithTitle_target_action_(t("capture.use"), self.target, "use:")
        self.use.setKeyEquivalent_("")  # Return might be the chosen key
        buttons = NSStackView.stackViewWithViews_([
            NSButton.buttonWithTitle_target_action_(t("capture.cancel"), self.target, "cancel:"),
            NSButton.buttonWithTitle_target_action_(t("capture.retry"), self.target, "retry:"), self.use])
        for view in (prompt, self.combo, self.message, buttons):
            stack.addArrangedSubview_(view)
        stack.widthAnchor().constraintEqualToConstant_(440).setActive_(True)
        self.window.setContentView_(stack)
        self.reset()
        mask = (1 << 10) | (1 << 11) | (1 << 12)  # key down, key up, modifiers
        self.monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self.handle)
        self.on_pause(True)  # the current key does not start listening while choosing
        self.window.center()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def reset(self) -> None:
        self.hotkey, self.peak = None, set()
        self.combo.setStringValue_(t("capture.waiting"))
        self.message.setStringValue_("")
        self.use.setEnabled_(False)

    def handle(self, event):
        """Local monitor: every key press in the window. Returns None so nothing gets typed."""
        if self.hotkey is not None:
            return None  # already chosen: "Try again" to pick another one
        kind = event.type()
        held = held_modifiers(event.modifierFlags())
        if kind == 12:  # modifiers only: chosen when all are released
            if held:
                self.peak |= held
                self.combo.setStringValue_(Hotkey(frozenset(self.peak)).label())
            elif self.peak:
                self.choose(Hotkey(frozenset(self.peak)))
        elif kind == 10 and not event.isARepeat():
            code = event.keyCode()
            mods = held - {"fn"} if code in FN_KEYS else held
            if code == ESCAPE and not mods:
                self.choose(Hotkey(frozenset(), ESCAPE, "⎋"))
            else:
                self.choose(Hotkey(frozenset(mods), code, key_name(event)))
        return None

    def choose(self, hotkey: Hotkey) -> None:
        self.hotkey = hotkey
        self.combo.setStringValue_(hotkey.label())
        ok, message = check(hotkey, keys.accessibility_allowed())
        self.message.setStringValue_(t(message) if message else "")
        self.use.setEnabled_(ok)

    def finish(self, use: bool, closing: bool = False) -> None:
        global _window
        from AppKit import NSEvent

        if self.closed:
            return
        self.closed = True
        NSEvent.removeMonitor_(self.monitor)
        self.on_pause(False)
        if not closing:
            self.window.orderOut_(None)
        _window = None
        if use and self.hotkey is not None:
            self.on_choose(str(self.hotkey))


def show(on_choose: Callable[[str], None], on_pause: Callable[[bool], None]) -> None:
    """Opens the choice window (or brings it to the front)."""
    global _window
    from AppKit import NSApplication

    if _window is None:
        _window = KeyCapture(on_choose, on_pause)
    else:
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        _window.window.makeKeyAndOrderFront_(None)
