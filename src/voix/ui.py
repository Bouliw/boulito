"""Feedback: sounds, macOS notifications, say, and Boulito's icon in the menu bar."""

import subprocess
from typing import Callable

from . import config

# macOS system sounds (/System/Library/Sounds)
SOUNDS = {"start": "Tink", "stop": "Pop", "cancel": "Funk", "error": "Basso", "timer": "Glass"}
_loaded: dict = {}


def sound(name: str) -> None:
    """Play a short sound without blocking."""
    if not config.load()["feedback"]["sounds"]:
        return
    from AppKit import NSSound

    if name not in _loaded:
        _loaded[name] = NSSound.soundNamed_(SOUNDS[name])
    s = _loaded[name]
    s.stop()
    s.play()


def _notify_fd() -> int | None:
    """Notification channel opened by the Boulito.app launcher: only if it really is a pipe.

    The variable is removed from the environment: commands started later (download…) never write
    to a descriptor 3 that could be another file for them.
    """
    import os
    import stat

    fd = os.environ.pop("BOULITO_NOTIFY_FD", None)
    try:
        return int(fd) if fd and stat.S_ISFIFO(os.fstat(int(fd)).st_mode) else None
    except (OSError, ValueError):
        return None


_NOTIFY_FD = _notify_fd()


def notify(title: str, text: str) -> None:
    """macOS notification (transcript and action done), without waiting.

    In Boulito.app, posted by the app itself (its name and icon): the launcher reads "title<TAB>text" on
    the BOULITO_NOTIFY_FD descriptor. From the command line (./voix start), through osascript.
    """
    if not config.load()["feedback"]["notifications"]:
        return
    if _NOTIFY_FD is not None:
        import os

        clean = lambda s: " ".join(str(s).replace("\t", " ").split())
        try:
            os.write(_NOTIFY_FD, f"{clean(title)}\t{clean(text)}\n".encode())
            return
        except OSError:
            pass  # channel closed: osascript

    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    subprocess.Popen(["osascript", "-e", f"display notification {q(text)} with title {q(title)}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def say(text: str) -> None:
    """Short spoken reply, without waiting, in the voice of the chosen language."""
    from . import i18n

    global _speech
    voice = config.load()["feedback"].get("voice") or i18n.voice()
    # "--": a text starting with "-" (model reply, summary) is never read as a say option
    _speech = subprocess.Popen(["say", *(["-v", voice] if voice else []), "--", text],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_speech = None


def speaking() -> bool:
    """Is Boulito speaking? (open listening then ignores the mic)"""
    return _speech is not None and _speech.poll() is None


# --- Menu bar --------------------------------------------------------------------

STATE_SYMBOLS = {  # state → SF Symbol for the icon
    "loading": "hourglass", "idle": "waveform", "listening": "mic.fill",
    "working": "ellipsis.circle", "error": "exclamationmark.triangle", "dictating": "pencil.line",
}
MODES = ("push_to_talk", "open")


ICON_DIR = config.PROJECT_DIR / "app" / "icon"
_images: dict = {}


def brand_image(name: str):
    """Boulito's brand images (app/icon, drawn by draw_icon.py): "app" (icon) or "menubar"."""
    from AppKit import NSImage, NSImageRep

    if name not in _images:
        if name == "app":
            _images[name] = NSImage.alloc().initWithContentsOfFile_(str(ICON_DIR / "Boulito.iconset" / "icon_512x512@2x.png"))
        else:  # 18 pt silhouette, in two resolutions, tinted by macOS to match the theme
            image = NSImage.alloc().initWithSize_((18, 18))
            for file in ("menubar.png", "menubar@2x.png"):
                rep = NSImageRep.imageRepWithContentsOfFile_(str(ICON_DIR / file))
                if rep is not None:
                    rep.setSize_((18, 18))
                    image.addRepresentation_(rep)
            image.setTemplate_(True)
            _images[name] = image if image.representations() else None
    return _images[name]


def new_alert():
    """Alert window with Boulito's icon (otherwise macOS shows Python's, the rocket)."""
    from AppKit import NSAlert

    alert = NSAlert.alloc().init()
    if (image := brand_image("app")) is not None:
        alert.setIcon_(image)
    return alert


def use_app_icon() -> None:
    """Boulito's icon for this process (alert windows, ⓘ), instead of Python's."""
    from AppKit import NSApplication

    image = brand_image("app")
    if image is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(image)


APP_NAME = "Boulito"  # the software always keeps this name; the assistant answers to the chosen name (assistant_name)


def assistant_name() -> str:
    return config.load()["trigger"].get("wake_word", "Boulito")


def model_label(key: str, available: bool) -> str:
    from .i18n import t
    from .llm import TIERS

    from .llm import DISK_GB

    _, model, gb, _ = TIERS[key]
    label = t("model.label", tier=t(f"tier.{key}"), model=model, gb=gb)
    from .i18n import decimal

    return label if available else label + t("model.missing", size=decimal(DISK_GB[key]))


def model_usable(key: str, downloaded: bool) -> bool:
    """Always selectable: a missing tier downloads in one click (confirmation, then in the background);
    the recommended RAM is only a hint, the user decides."""
    return True


def syllables(name: str) -> int:
    """Approximate number of syllables (vowel groups), to suggest a long enough name."""
    import re
    import unicodedata

    folded = unicodedata.normalize("NFD", name.lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    folded = re.sub(r"e\b", "", folded) or folded  # silent final e: "Robe" = 1
    return len(re.findall(r"[aeiouy]+", folded))


_menubar = None


def note(text: str | None) -> None:
    """Replace the menu's status line (download progress); None restores it."""
    if _menubar is not None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_menubar.set_note, text)


def ask_download(tier: str, size_gb: float, ram_gb: int) -> bool:
    """Confirmation before downloading a model (main thread)."""
    from AppKit import NSAlert, NSApplication

    from .i18n import t

    alert = new_alert()
    alert.setMessageText_(t("model.download.title", tier=tier))
    from .i18n import decimal

    alert.setInformativeText_(t("model.download.info", size=decimal(size_gb), gb=ram_gb))
    alert.addButtonWithTitle_(t("model.download.ok"))
    alert.addButtonWithTitle_(t("rename.cancel"))
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    return alert.runModal() == 1000


def status(state: str) -> None:
    """Change the icon and the state shown in the menu bar (from any thread)."""
    if _menubar is not None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_menubar.set_state, state)


def _target_class():
    """Objective-C class that receives the menu clicks (created only once)."""
    from Foundation import NSObject

    class BoulitoMenuTarget(NSObject):
        def chooseModel_(self, sender):
            self.menubar.choose_model(sender.representedObject())

        def chooseKey_(self, sender):
            self.menubar.capture_key()

        def updateNow_(self, sender):
            self.menubar.on_update()

        def chooseMode_(self, sender):
            self.menubar.choose_mode(sender.representedObject())

        def toggleUnderstood_(self, sender):
            self.menubar.toggle_understood(sender.representedObject())

        def chooseLanguage_(self, sender):
            self.menubar.choose_language(sender.representedObject())

        def setup_(self, sender):
            from . import setup

            setup.show(self.menubar)

        def toggleConversation_(self, sender):
            self.menubar.toggle_conversation()

        def toggleDuck_(self, sender):
            self.menubar.toggle_duck()

        def menuWillOpen_(self, menu):
            self.menubar.refresh_models()

        def quit_(self, sender):
            self.menubar.on_quit()

    return BoulitoMenuTarget


_Target = None


class MenuBar:
    """Assistant icon at the top of the screen: state, model, listening mode, language, name, Quit."""

    def __init__(self, key_label: Callable[[], str], on_model: Callable[[str], None], on_name: Callable[[str], None],
                 on_language: Callable[[str], None], on_quit: Callable[[], None],
                 on_mode: Callable[[str], None] | None = None, on_key: Callable[[str], None] | None = None,
                 on_key_pause: Callable[[bool], None] | None = None,
                 on_understood: Callable[[list[str]], None] | None = None) -> None:
        global _Target, _menubar
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSStatusBar

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        use_app_icon()  # alerts and ⓘ windows: Boulito's icon, not Python's
        _Target = _Target or _target_class()
        self.target = _Target.alloc().init()
        self.target.menubar = self
        self.key_label = key_label
        self.on_model, self.on_name, self.on_language, self.on_quit = on_model, on_name, on_language, on_quit
        self.on_mode, self.on_key, self.on_key_pause = on_mode, on_key, on_key_pause
        self.on_understood = on_understood
        self.on_echo: Callable[[], None] | None = None  # echo cancellation checked or unchecked: the mic changes at once
        self.on_download: Callable[[str], None] | None = None  # "Download" button of a model (setup window)
        self.download_status: Callable[[], tuple] = lambda: (None, 0)  # (tier in progress, percentage)
        self.keys_active = True  # keyboard listening hooked up (Input Monitoring granted at launch)
        self.restart: Callable[[], None] | None = None
        self.speech_ready: Callable[[], bool] = lambda: True  # speech recognition downloaded and loaded
        self.erase_all: Callable[[], None] | None = None  # "Erase everything" (.dmg version)
        self.update_info: Callable[[], dict | None] = lambda: None  # update available (.dmg version)
        self.update_status: Callable[[], dict] = lambda: {"state": "idle", "pct": 0, "checked": False}
        self.on_update: Callable[[], None] | None = None
        self.on_check_update: Callable[[], None] | None = None
        self.state = "loading"
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)  # variable length
        self.build()
        _menubar = self

    def build(self) -> None:
        """(Re)build the menu in the chosen language."""
        from AppKit import NSMenu

        from .i18n import LANGUAGES, decimal, language, t
        from .llm import MODELS, TIERS, local_path

        cfg = config.load()
        name = assistant_name()
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.state_item = self._add(menu, "", enabled=False)
        available = self.update_info()
        if available and self.on_update:  # .dmg version: a new version is out
            self._add(menu, t("update.menu", version=available["version"]), "updateNow:",
                      enabled=self.update_status()["state"] == "idle")
        menu.addItem_(self._separator())
        self._add(menu, t("menu.talk", key=self.key_label()), enabled=False)
        if cfg["trigger"].get("mode") == "open":
            self._add(menu, t("menu.talk_open", name=name), enabled=False)
        menu.addItem_(self._separator())

        models = self._submenu(menu, t("menu.model"))
        self.model_items = {}
        for key in TIERS:
            ok = bool(local_path(MODELS[key]))
            item = self._add(models, model_label(key, ok), "chooseModel:", enabled=model_usable(key, ok), represented=key)
            item.setState_(int(key == cfg["llm"]["model"]))
            self.model_items[key] = item

        modes = self._submenu(menu, t("menu.listening"))
        for mode in MODES:
            item = self._add(modes, t(f"mode.{mode}", name=name), "chooseMode:", enabled=self.on_mode is not None,
                             represented=mode)
            item.setState_(int(mode == cfg["trigger"].get("mode", "push_to_talk")))

        from .hotkey import Hotkey

        self._add(menu, t("menu.key_current", key=Hotkey.parse(cfg["trigger"]["key"]).label()), "chooseKey:",
                  enabled=self.on_key is not None)

        from .router import understood_languages

        understood = self._submenu(menu, t("menu.understood"))
        for code, label in LANGUAGES.items():
            self._add(understood, label, "toggleUnderstood:", represented=code).setState_(int(code in understood_languages()))

        languages = self._submenu(menu, t("menu.language"))
        for code, label in LANGUAGES.items():
            self._add(languages, label, "chooseLanguage:", represented=code).setState_(int(code == language()))

        # Name, open at login, ignoring the Mac's own sound, announcements: in the setup window only (light menu)
        self._add(menu, t("menu.duck"), "toggleDuck:").setState_(int(cfg["audio"].get("duck", True)))
        self._add(menu, t("menu.conversation", s=decimal(cfg["audio"].get("conversation_s", 5))), "toggleConversation:").setState_(int(cfg["audio"].get("conversation", True)))
        self._add(menu, t("menu.setup"), "setup:")
        menu.addItem_(self._separator())
        self._add(menu, t("menu.quit", name=APP_NAME), "quit:", key="q")
        menu.setDelegate_(self.target)  # on each opening: models downloaded in the meantime
        self.item.setMenu_(menu)
        self.set_state(self.state)

    def _submenu(self, menu, title: str):
        from AppKit import NSMenu

        sub = NSMenu.alloc().init()
        sub.setAutoenablesItems_(False)
        self._add(menu, title).setSubmenu_(sub)
        return sub

    def _add(self, menu, title: str, action: str | None = None, enabled: bool = True,
             represented: str | None = None, key: str = ""):
        from AppKit import NSMenuItem

        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        if action:
            item.setTarget_(self.target)
        if represented:
            item.setRepresentedObject_(represented)
        item.setEnabled_(enabled)
        menu.addItem_(item)
        return item

    @staticmethod
    def _separator():
        from AppKit import NSMenuItem

        return NSMenuItem.separatorItem()

    def set_state(self, state: str) -> None:
        from AppKit import NSImage

        from .i18n import t

        self.state = state
        shown = state  # fixed icon if the user chose so; loading and error always stay visible
        if not config.load()["feedback"].get("icon_states", True) and state not in ("loading", "error"):
            shown = "idle"
        image = brand_image("menubar") if shown == "idle" else None  # at rest: Boulito's silhouette
        if image is None:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(STATE_SYMBOLS[shown], APP_NAME)
            image.setTemplate_(True)  # adapts to light or dark mode
        self.item.button().setImage_(image)
        self.state_item.setTitle_(getattr(self, "note_text", None)
                                  or t("state.line", name=APP_NAME, state=t(f"state.{state}")))

    def set_note(self, text: str | None) -> None:
        from .i18n import t

        self.note_text = t("state.line", name=APP_NAME, state=text) if text else None
        self.set_state(self.state)

    def refresh_models(self) -> None:
        from .llm import MODELS, local_path

        for key, item in self.model_items.items():
            if not item.isEnabled() and model_usable(key, bool(local_path(MODELS[key]))):
                item.setTitle_(model_label(key, True))
                item.setEnabled_(True)

    def choose_model(self, key: str) -> None:
        if key != config.load()["llm"]["model"]:
            for k, item in self.model_items.items():
                item.setState_(int(k == key))
            self.on_model(key)

    def toggle_conversation(self) -> None:
        config.set_value("audio", "conversation", not config.load()["audio"].get("conversation", True))
        self.build()

    def toggle_duck(self) -> None:
        config.set_value("audio", "duck", not config.load()["audio"].get("duck", True))
        self.build()

    def capture_key(self, after: Callable[[], None] | None = None) -> None:
        """Window where the user presses the wanted key or key combination."""
        from . import keycapture

        def chosen(key: str) -> None:
            self.choose_key(key)
            if after:
                after()

        keycapture.show(chosen, self.on_key_pause or (lambda paused: None))

    def choose_key(self, key: str) -> None:
        if key != config.load()["trigger"]["key"]:
            self.on_key(key)  # saves the key and hooks it up live
            self.build()

    def choose_mode(self, mode: str) -> None:
        if mode != config.load()["trigger"].get("mode", "push_to_talk"):
            config.set_value("trigger", "mode", mode)
            self.build()
            self.on_mode(mode)

    def toggle_understood(self, code: str) -> None:
        """Check or uncheck an understood language (at least one always remains)."""
        from .router import understood_languages

        codes = understood_languages()
        codes = [c for c in codes if c != code] if code in codes else codes + [code]
        if not codes:
            return
        config.set_value("feedback", "understood", codes)
        self.build()
        if self.on_understood:
            self.on_understood(codes)

    def choose_language(self, code: str) -> None:
        if code != config.load()["feedback"].get("language", "en"):
            config.set_value("feedback", "language", code)
            self.build()
            self.on_language(code)

    def ask_name(self) -> None:
        """Window to rename the assistant (it is also its wake word)."""
        from AppKit import NSAlert, NSApplication, NSMakeRect, NSTextField

        from .i18n import t

        current = assistant_name()
        alert = new_alert()
        alert.setMessageText_(t("rename.title"))
        alert.setInformativeText_(t("rename.info"))
        alert.addButtonWithTitle_(t("rename.ok"))
        alert.addButtonWithTitle_(t("rename.cancel"))
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 24))
        field.setStringValue_(current)
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() != 1000:  # NSAlertFirstButtonReturn
            return
        name = " ".join(str(field.stringValue()).split())
        if not name or len(name) > 24 or not all(c.isalpha() or c in " -'" for c in name) or name == current:
            return
        if syllables(name) < 2:  # "Max", "Bob": likely false triggers in open listening
            warning = new_alert()
            warning.setMessageText_(t("rename.short.title", name=name))
            warning.setInformativeText_(t("rename.short.info"))
            warning.addButtonWithTitle_(t("rename.keep"))
            warning.addButtonWithTitle_(t("rename.cancel"))
            if warning.runModal() != 1000:
                return
        config.set_value("trigger", "wake_word", name)
        self.build()
        self.on_name(name)
