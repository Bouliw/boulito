"""First-launch setup assistant: permissions (live status), name, listening mode, language, startup.

Opens on the first launch of Boulito.app, then from the menu ("Setup…").
Each permission has a button that opens the right System Settings pane: Boulito does not
bypass anything, the user ticks the box themselves.
"""

import subprocess
from pathlib import Path
from typing import Callable

import objc

from . import audio, autostart, config, keys
from .ui import APP_NAME, new_alert
from .i18n import LANGUAGES, decimal, language, t

SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
WIDTH = 560
BRAND_RGB = (1.00, 0.55, 0.18)  # orange of the ball (app/icon/draw_icon.py)
INFO_TOPICS = ("conversation", "echo")  # options explained by an ⓘ button



def recommended_model(ram_gb: float) -> str:
    """The most accurate model that fits in the Mac's memory: High from 16 GB, otherwise Fast."""
    return "9b" if ram_gb >= 15 else "4b"


def brand_color():
    from AppKit import NSColor

    return NSColor.colorWithSRGBRed_green_blue_alpha_(*BRAND_RGB, 1.0)
SCREEN_HEIGHT = None  # forced screen height (layout tests); otherwise the screen's own

_window = None  # one window at a time, kept alive while it is open


def microphone_ok() -> bool:
    return audio.microphone_status() == "granted"


# Signature of the reply block of the microphone access request (void (^)(BOOL)): without it, PyObjC cannot
# call the function and the "Allow" button failed silently. Declared before any call.
objc.registerMetaDataForSelector(b"AVCaptureDevice", b"requestAccessForMediaType:completionHandler:", {
    "arguments": {3: {"callable": {"retval": {"type": b"v"}, "arguments": {0: {"type": b"^v"}, 1: {"type": b"Z"}}}}}})


def request_microphone() -> None:
    objc.loadBundle("AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework")
    device = objc.lookUpClass("AVCaptureDevice")
    if device.authorizationStatusForMediaType_("soun") == 0:  # never asked: macOS dialog
        device.requestAccessForMediaType_completionHandler_("soun", lambda granted: None)
    else:  # already denied or granted: the Settings pane, where the box is ticked or unticked
        subprocess.run(["open", SETTINGS + "Microphone"])


ASKED_FILE = config.CACHE_DIR / "permissions-asked.json"


def first_request(key: str) -> bool:
    """True the first time: macOS then shows its own dialog (with a button to Settings). After that, it no longer
    shows it: open the right pane directly. Never both at once (the request looked doubled)."""
    import json

    try:
        asked = set(json.loads(ASKED_FILE.read_text()))
    except (OSError, ValueError):
        asked = set()
    if key in asked:
        return False
    ASKED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ASKED_FILE.write_text(json.dumps(sorted(asked | {key})))
    return True


def request_input_monitoring() -> None:
    if first_request("input"):
        audio.request_input_monitoring()  # macOS dialog: "… would like to receive keystrokes"
    else:
        subprocess.run(["open", SETTINGS + "ListenEvent"])


def request_accessibility() -> None:
    if first_request("accessibility"):
        keys.accessibility_allowed(prompt=True)  # macOS dialog: "… would like to control this computer"
    else:
        subprocess.run(["open", SETTINGS + "Accessibility"])


def calendar_ok() -> bool:
    from . import agenda

    return agenda.status() == "granted"


def request_calendar() -> None:
    from . import agenda

    if agenda.status() == "not_determined":
        agenda.request_access()  # macOS dialog
    else:
        subprocess.run(["open", SETTINGS + "Calendars"])


def notifications_ok() -> bool | None:
    """State read from .cache/notifications, written every 3 s by the app (the launcher); None if unknown."""
    try:
        state = (config.CACHE_DIR / "notifications").read_text().strip()
    except OSError:
        return None
    return {"authorized": True, "denied": False}.get(state)


def open_notifications() -> None:
    """System Settings → Notifications → Boulito ("Allow notifications")."""
    subprocess.run(["open", f"x-apple.systempreferences:com.apple.Notifications-Settings.extension?id={config.BUNDLE_ID}"])


# (identifier, check, button action); texts in i18n: setup.<id>.title / .text / .button
PERMISSIONS: list[tuple[str, Callable[[], bool | None], Callable[[], None]]] = [
    ("microphone", microphone_ok, request_microphone),
    ("input", audio.input_monitoring_allowed, request_input_monitoring),
    ("accessibility", lambda: keys.accessibility_allowed(), request_accessibility),
    ("safari", lambda: None, lambda: subprocess.run(["open", "-a", "Safari"])),
    ("calendar", calendar_ok, request_calendar),
    ("notify", notifications_ok, open_notifications),
]


def done() -> bool:
    return bool(config.load().get("app", {}).get("setup_done", False))


def _target_class():
    from Foundation import NSObject

    class BoulitoSetupTarget(NSObject):
        def permission_(self, sender):
            key, _, action = PERMISSIONS[sender.tag()]
            print(f"· setup: button {key}", flush=True)
            try:
                action()
            except Exception as e:  # an error here must never go unnoticed
                import traceback

                print(f"· setup: button {key} error: {e!r}", flush=True)
                traceback.print_exc()

        def rename_(self, sender):
            self.setup.menubar.ask_name()
            self.setup.refresh_(None)

        def mode_(self, sender):
            self.setup.menubar.choose_mode(("push_to_talk", "open")[sender.indexOfSelectedItem()])

        def key_(self, sender):
            self.setup.menubar.capture_key(after=lambda: self.setup.close(reopen=True))

        def language_(self, sender):
            self.setup.menubar.choose_language(list(LANGUAGES)[sender.indexOfSelectedItem()])
            self.setup.close(reopen=True)  # texts in the new language

        def understood_(self, sender):
            self.setup.menubar.toggle_understood(list(LANGUAGES)[sender.tag()])
            self.setup.refresh_understood()

        def login_(self, sender):
            autostart.enable() if sender.state() else autostart.disable()
            self.setup.menubar.build()

        def timerHelp_(self, sender):
            from AppKit import NSAlert

            from .reminders import SHORTCUT_SOURCE, install_shortcuts, timer_shortcut

            if timer_shortcut() and not SHORTCUT_SOURCE.exists():  # already there, nothing to update
                self.setup.refresh_timer()
                return
            import threading

            from PyObjCTools import AppHelper

            def manual_steps() -> None:  # main thread: window with the manual steps
                subprocess.run(["open", "-a", "Shortcuts"])
                alert = new_alert()
                alert.setMessageText_(t("setup.timer.title"))
                alert.setInformativeText_(t("setup.timer.steps"))
                alert.runModal()
                self.setup.refresh_timer()

            def install() -> None:  # signing goes through Apple (up to 60 s): never on the main thread
                names = {"start": t("timer.shortcut_name"), "control": t("timer.control_name")}
                if install_shortcuts(names) or timer_shortcut():
                    return  # Shortcuts offers to add them (one click each; "Replace" for an update)
                AppHelper.callAfter(manual_steps)

            threading.Thread(target=install, name="voix-shortcut-install", daemon=True).start()

        def conversation_(self, sender):
            config.set_value("audio", "conversation", bool(sender.state()))
            self.setup.menubar.build()

        def duck_(self, sender):
            config.set_value("audio", "duck", bool(sender.state()))
            self.setup.menubar.build()

        def echo_(self, sender):
            config.set_value("audio", "echo_cancel", bool(sender.state()))
            if self.setup.menubar.on_echo:
                self.setup.menubar.on_echo()

        def announce_(self, sender):
            config.set_value("feedback", "announce", bool(sender.state()))

        def sounds_(self, sender):
            config.set_value("feedback", "sounds", bool(sender.state()))

        def notifications_(self, sender):
            config.set_value("feedback", "notifications", bool(sender.state()))

        def icon_(self, sender):
            config.set_value("feedback", "icon_states", bool(sender.state()))
            menubar = self.setup.menubar
            menubar.set_state(menubar.state)  # the icon follows the setting right away

        def model_(self, sender):
            from .llm import MODELS, TIERS, local_path

            key = list(TIERS)[sender.tag()]
            menubar = self.setup.menubar
            print(f"· setup: model {key}", flush=True)
            if local_path(MODELS[key]):
                menubar.on_model(key)  # already there: Boulito switches to it
                menubar.build()
            elif menubar.on_download:
                menubar.on_download(key)  # downloaded in the background, then used
            self.setup.refresh_models()

        def speech_(self, sender):
            print("· setup: speech recognition download", flush=True)
            if self.setup.menubar.on_download:
                self.setup.menubar.on_download("speech")  # downloaded in the background, then listening starts
            self.setup.refresh_models()

        def updateButton_(self, sender):
            menubar = self.setup.menubar
            if menubar.update_info() and menubar.on_update:
                menubar.on_update()  # downloads, checks, replaces the app and relaunches it
            elif menubar.on_check_update:
                menubar.on_check_update()
            self.setup.refresh_update()

        def updatesAuto_(self, sender):
            config.set_value("app", "check_updates", bool(sender.state()))

        def eraseAll_(self, sender):
            from AppKit import NSAlert

            alert = new_alert()
            alert.setMessageText_(t("setup.erase.title", name=APP_NAME))
            alert.setInformativeText_(t("setup.erase.info", name=APP_NAME, size=decimal(data_size_gb())))
            alert.addButtonWithTitle_(t("setup.erase.ok")).setHasDestructiveAction_(True)
            alert.addButtonWithTitle_(t("rename.cancel"))
            if alert.runModal() == 1000 and self.setup.menubar.erase_all:  # first button: "Erase"
                print("· setup: erase everything", flush=True)
                self.setup.menubar.erase_all()

        def windowWillClose_(self, notification):
            if _window is self.setup:
                self.setup.close()

        def info_(self, sender):
            from AppKit import NSAlert

            topic = INFO_TOPICS[sender.tag()]
            alert = new_alert()
            alert.setMessageText_(t(f"info.{topic}.title"))
            seconds = decimal(config.load()["audio"].get("conversation_s", 5))
            alert.setInformativeText_(t(f"info.{topic}", name=menubar_name(), s=seconds))
            alert.runModal()

        def finish_(self, sender):
            config.set_value("app", "setup_done", True)
            self.setup.close()
            if self.setup.needs_restart() and self.setup.menubar.restart:
                self.setup.menubar.restart()

        def refresh_(self, timer):
            self.setup.refresh()

    return BoulitoSetupTarget


_Target = None


class SetupWindow:
    def __init__(self, menubar) -> None:
        global _Target
        from AppKit import (NSApplication, NSBackingStoreBuffered, NSMakeRect, NSTimer, NSWindow,
                            NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)

        _Target = _Target or _target_class()
        self.menubar = menubar
        self.target = _Target.alloc().init()
        self.target.setup = self
        self.status_labels = []
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, 620), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        self.window.setTitle_(t("setup.window", name=APP_NAME))
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self.target)  # red button: the window really closes (timer stopped)
        self.build()
        self.window.center()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self.target, "refresh:", None, True)  # live permission states
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    # --- building ---

    def build(self) -> None:
        from AppKit import NSStackView, NSUserInterfaceLayoutOrientationVertical, NSEdgeInsetsMake

        stack = NSStackView.alloc().init()
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(1)  # NSLayoutAttributeLeading
        stack.setSpacing_(14)
        stack.setEdgeInsets_(NSEdgeInsetsMake(22, 24, 22, 24))
        name = menubar_name()
        from AppKit import NSImageView

        from .ui import brand_image

        logo = NSImageView.imageViewWithImage_(brand_image("app"))
        logo.widthAnchor().constraintEqualToConstant_(72).setActive_(True)
        logo.heightAnchor().constraintEqualToConstant_(72).setActive_(True)
        heading = NSStackView.alloc().init()
        heading.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        heading.setAlignment_(1)
        heading.setSpacing_(2)
        heading.addArrangedSubview_(self._label(t("setup.title", name=APP_NAME), size=20, bold=True))
        heading.addArrangedSubview_(self._label(t("setup.tagline"), size=13, secondary=True))
        header = self._row(logo, heading)
        header.setSpacing_(14)
        stack.addArrangedSubview_(header)
        stack.addArrangedSubview_(self._label(t("setup.intro", name=APP_NAME), wrap=True, secondary=True))
        stack.addArrangedSubview_(self._label(t("setup.permissions"), size=14, bold=True))
        for i, (key, _, _) in enumerate(PERMISSIONS):
            stack.addArrangedSubview_(self._permission_row(i, key))
        # AI model: each user picks theirs, downloaded in one click
        from .llm import TIERS, mac_memory_gb

        ram = mac_memory_gb()
        self.speech_row = self._speech_row()  # speech recognition first: without it, nothing is heard
        stack.addArrangedSubview_(self._label(t("setup.speech.title"), size=14, bold=True))
        stack.addArrangedSubview_(self.speech_row["view"])
        stack.addArrangedSubview_(self._label(t("setup.models"), size=14, bold=True))
        stack.addArrangedSubview_(self._label(t("setup.models.text", name=APP_NAME, ram=round(ram)), wrap=True, secondary=True, size=11))
        self.model_rows = [self._model_row(i, key, key == recommended_model(ram)) for i, key in enumerate(TIERS)]
        for row in self.model_rows:
            stack.addArrangedSubview_(row["view"])
        stack.addArrangedSubview_(self._label(t("setup.preferences"), size=14, bold=True))
        stack.addArrangedSubview_(self._row(self._label(t("setup.name", name=name)), self._button(t("setup.rename"), "rename:")))
        stack.addArrangedSubview_(self._label(t("setup.name_tip"), wrap=True, secondary=True, size=11))
        mode = config.load()["trigger"].get("mode", "push_to_talk")
        stack.addArrangedSubview_(self._row(self._label(t("menu.listening")), self._popup(
            [t("mode.push_to_talk"), t("mode.open", name=name)], ("push_to_talk", "open").index(mode), "mode:")))
        from .hotkey import Hotkey

        key = Hotkey.parse(config.load()["trigger"]["key"]).label()
        stack.addArrangedSubview_(self._row(self._label(t("setup.key", key=key)), self._button(t("setup.rename"), "key:")))
        stack.addArrangedSubview_(self._row(self._label(t("menu.language")), self._popup(
            list(LANGUAGES.values()), list(LANGUAGES).index(language()), "language:")))
        from .router import understood_languages

        self.understood_boxes = []
        for i, (code, label) in enumerate(LANGUAGES.items()):
            box = self._checkbox(label, code in understood_languages(), "understood:")
            box.setTag_(i)
            self.understood_boxes.append(box)
        stack.addArrangedSubview_(self._label(t("menu.understood")))
        for i in range(0, len(self.understood_boxes), 3):  # two rows of three checkboxes
            row = self._row(*self.understood_boxes[i:i + 3])
            row.setSpacing_(24)
            stack.addArrangedSubview_(row)
        stack.addArrangedSubview_(self._label(t("setup.section.listening"), size=13, bold=True))
        stack.addArrangedSubview_(self._checkbox(t("menu.duck"), config.load()["audio"].get("duck", True), "duck:"))
        stack.addArrangedSubview_(self._row(
            self._checkbox(t("menu.echo"), config.load()["audio"].get("echo_cancel", True), "echo:"), self._info("echo")))
        stack.addArrangedSubview_(self._row(
            self._checkbox(t("menu.conversation", s=decimal(config.load()["audio"].get("conversation_s", 5))),
                           config.load()["audio"].get("conversation", True), "conversation:"),
            self._info("conversation")))
        feedback = config.load()["feedback"]
        stack.addArrangedSubview_(self._label(t("setup.section.feedback"), size=13, bold=True))
        stack.addArrangedSubview_(self._checkbox(t("menu.announce"), feedback.get("announce", True), "announce:"))
        stack.addArrangedSubview_(self._checkbox(t("setup.sounds"), feedback.get("sounds", True), "sounds:"))
        stack.addArrangedSubview_(self._checkbox(t("setup.notifications"), feedback.get("notifications", True), "notifications:"))
        stack.addArrangedSubview_(self._checkbox(t("setup.icon"), feedback.get("icon_states", True), "icon:"))
        stack.addArrangedSubview_(self._label(t("setup.section.startup"), size=13, bold=True))
        stack.addArrangedSubview_(self._checkbox(t("menu.login"), autostart.enabled(), "login:"))
        # Clock timer (optional): "Boulito Timer" shortcut to create once
        self.timer_status = self._label("", size=15)
        texts = self._label(t("setup.timer.text"), wrap=True, secondary=True, size=11)
        texts.setPreferredMaxLayoutWidth_(WIDTH - 250)
        from AppKit import NSStackView, NSUserInterfaceLayoutOrientationVertical

        column = NSStackView.alloc().init()
        column.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        column.setAlignment_(1)
        column.setSpacing_(2)
        column.addArrangedSubview_(self._label(t("setup.timer.title"), bold=True))
        column.addArrangedSubview_(texts)
        column.widthAnchor().constraintEqualToConstant_(WIDTH - 250).setActive_(True)
        self.timer_button = self._button(t("setup.timer.button"), "timerHelp:")
        stack.addArrangedSubview_(self._row(self.timer_status, column, self.timer_button))
        self.timer_duplicates = self._label("", wrap=True, size=11)  # "Boulito Timer 1": Keep Both instead of Replace
        self.timer_duplicates.setHidden_(True)
        stack.addArrangedSubview_(self.timer_duplicates)
        self.refresh_timer()
        if config.PACKAGED:  # .dmg version: updates from GitHub, in one click
            stack.addArrangedSubview_(self._label(t("setup.section.updates"), size=13, bold=True))
            self.update_label = self._label("", size=12)
            self.update_button = self._button(t("setup.update.check"), "updateButton:")
            stack.addArrangedSubview_(self._row(self.update_label, self.update_button))
            stack.addArrangedSubview_(self._checkbox(t("setup.update.auto"), config.load()["app"].get("check_updates", True),
                                                     "updatesAuto:"))
        if config.PACKAGED:  # .dmg version: everything it created is erased in one click (source version: delete the folder)
            stack.addArrangedSubview_(self._label(t("setup.section.data"), size=13, bold=True))
            place = str(config.DATA_DIR).replace(str(Path.home()), "~", 1)
            stack.addArrangedSubview_(self._label(t("setup.erase.text", path=place, size=decimal(data_size_gb())),
                                                  wrap=True, secondary=True, size=11))
            stack.addArrangedSubview_(self._button(t("setup.erase.button"), "eraseAll:"))
        self.restart_hint = self._label(t("setup.restart_hint"), wrap=True, size=11)
        self.restart_hint.setHidden_(True)
        self.finish = self._button(t("setup.finish"), "finish:")
        self.finish.setKeyEquivalent_("\r")
        self.finish.setBezelColor_(brand_color())  # Boulito orange
        self._layout(stack)
        self.refresh()

    def _layout(self, stack) -> None:
        """Content in a scrolling area, capped at the screen height; at the bottom, outside the scroll,
        "Done" / "Restart Boulito" always stays visible (13-inch MacBook screen included)."""
        from AppKit import (NSEdgeInsetsMake, NSMakePoint, NSMakeRect, NSScreen, NSScrollView, NSStackView,
                            NSUserInterfaceLayoutOrientationVertical)

        stack.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        stack.layoutSubtreeIfNeeded()
        content_height = stack.fittingSize().height
        stack.setTranslatesAutoresizingMaskIntoConstraints_(True)
        stack.setFrame_(NSMakeRect(0, 0, WIDTH, content_height))

        footer = NSStackView.alloc().init()
        footer.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        footer.setAlignment_(1)
        footer.setSpacing_(8)
        footer.setEdgeInsets_(NSEdgeInsetsMake(12, 24, 18, 24))
        footer.addArrangedSubview_(self.restart_hint)
        footer.addArrangedSubview_(self.finish)
        footer.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)

        screen = self.window.screen() or NSScreen.mainScreen()
        available = SCREEN_HEIGHT or (screen.visibleFrame().size.height if screen else 800)
        footer_height = footer.fittingSize().height + 40  # + the hidden "Restart" button and the help text
        visible = max(240, min(content_height, available - footer_height - 40))  # 40: title bar and margin

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, visible))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setDocumentView_(stack)
        scroll.heightAnchor().constraintEqualToConstant_(visible).setActive_(True)
        scroll.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)

        root = NSStackView.alloc().init()
        root.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        root.setSpacing_(0)
        root.addArrangedSubview_(scroll)
        root.addArrangedSubview_(footer)
        self.window.setContentView_(root)
        root.layoutSubtreeIfNeeded()
        self.window.setContentSize_(root.fittingSize())
        # Top of the content first (the view is not "flipped": the origin is at the bottom)
        top = 0 if stack.isFlipped() else max(0, content_height - visible)
        scroll.contentView().scrollToPoint_(NSMakePoint(0, top))
        scroll.reflectScrolledClipView_(scroll.contentView())

    def _label(self, text: str, size: float = 13, bold: bool = False, wrap: bool = False, secondary: bool = False):
        from AppKit import NSColor, NSFont, NSTextField

        field = NSTextField.wrappingLabelWithString_(text) if wrap else NSTextField.labelWithString_(text)
        field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        if secondary:
            field.setTextColor_(NSColor.secondaryLabelColor())
        if wrap:
            field.setPreferredMaxLayoutWidth_(WIDTH - 60)
        return field

    def _button(self, title: str, action: str, tag: int = 0):
        from AppKit import NSButton

        button = NSButton.buttonWithTitle_target_action_(title, self.target, action)
        button.setTag_(tag)
        return button

    def _popup(self, items: list[str], selected: int, action: str):
        from AppKit import NSMakeRect, NSPopUpButton

        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 260, 26), False)
        popup.addItemsWithTitles_(items)
        popup.selectItemAtIndex_(selected)
        popup.setTarget_(self.target)
        popup.setAction_(action)
        return popup

    def _checkbox(self, title: str, on: bool, action: str):
        from AppKit import NSButton

        box = NSButton.checkboxWithTitle_target_action_(title, self.target, action)
        box.setState_(int(on))
        return box

    def _info(self, topic: str):
        """Small ⓘ button: longer explanation of the option, in a dialog."""
        from AppKit import NSButton, NSImage

        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("info.circle", t(f"info.{topic}.title"))
        button = NSButton.buttonWithImage_target_action_(image, self.target, "info:")
        button.setBordered_(False)
        button.setTag_(INFO_TOPICS.index(topic))
        return button

    def _row(self, *views):
        from AppKit import NSStackView

        row = NSStackView.stackViewWithViews_(list(views))
        row.setSpacing_(10)
        return row

    def _model_row(self, index: int, key: str, recommended: bool) -> dict:
        from AppKit import NSStackView, NSUserInterfaceLayoutOrientationVertical

        from .i18n import decimal
        from .llm import DISK_GB, TIERS

        status = self._label("", size=15)
        texts = NSStackView.alloc().init()
        texts.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        texts.setAlignment_(1)
        texts.setSpacing_(2)
        title = t(f"tier.{key}") + (" · " + t("setup.model.recommended") if recommended else "")
        texts.addArrangedSubview_(self._label(title, bold=True))
        _, model, ram, _ = TIERS[key]
        text = self._label(t(f"setup.model.desc.{key}") + "\n" + t("setup.model.line", model=model, size=decimal(DISK_GB[key]), ram=ram),
                           wrap=True, secondary=True, size=11)
        text.setPreferredMaxLayoutWidth_(WIDTH - 250)
        texts.addArrangedSubview_(text)
        texts.widthAnchor().constraintEqualToConstant_(WIDTH - 250).setActive_(True)
        button = self._button(t("setup.model.download"), "model:", tag=index)
        return {"key": key, "status": status, "button": button, "view": self._row(status, texts, button)}

    def _speech_row(self) -> dict:
        from AppKit import NSStackView, NSUserInterfaceLayoutOrientationVertical

        from .i18n import decimal
        from .stt import DISK_GB

        status = self._label("", size=15)
        texts = NSStackView.alloc().init()
        texts.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        texts.setAlignment_(1)
        text = self._label(t("setup.speech.text", size=decimal(DISK_GB)), wrap=True, secondary=True, size=11)
        text.setPreferredMaxLayoutWidth_(WIDTH - 250)
        texts.addArrangedSubview_(text)
        texts.widthAnchor().constraintEqualToConstant_(WIDTH - 250).setActive_(True)
        button = self._button(t("setup.model.download"), "speech:")
        return {"status": status, "button": button, "view": self._row(status, texts, button)}

    def refresh_speech(self, busy, pct) -> None:
        from .audio import vad_downloaded
        from .stt import is_downloaded

        if "speech" not in self.downloaded or self.ticks % 3 == 0:
            self.downloaded["speech"] = self.menubar.speech_ready() or (is_downloaded() and vad_downloaded())
        if busy == "speech":
            mark, title, enabled = "⏳", t("setup.model.progress", pct=pct), False
        elif self.downloaded["speech"]:
            mark, title, enabled = "✅", t("setup.speech.installed"), False
        else:
            mark, title, enabled = "⬇️", t("setup.model.download"), busy is None
        row = self.speech_row
        if row["status"].stringValue() != mark:
            row["status"].setStringValue_(mark)
        if row["button"].title() != title:
            row["button"].setTitle_(title)
        if bool(row["button"].isEnabled()) != enabled:
            row["button"].setEnabled_(enabled)

    def refresh_update(self) -> None:
        """Version, update available or in progress; the button checks, or updates."""
        from .update import current_version

        if not hasattr(self, "version"):
            self.version = current_version()
        status, info = self.menubar.update_status(), self.menubar.update_info()
        state, v = status["state"], self.version
        if state == "checking":
            text, title, enabled = t("setup.update.status.checking", version=v), t("setup.update.check"), False
        elif state in ("downloading", "installing"):
            latest = info["version"] if info else "…"
            text = t("setup.update.status.available", version=v, latest=latest)
            title, enabled = (t("setup.model.progress", pct=status["pct"]) if state == "downloading" else "…"), False
        elif info:
            text = t("setup.update.status.available", version=v, latest=info["version"])
            title, enabled = t("setup.update.button", version=info["version"]), True
        else:
            text = t("setup.update.status.uptodate" if status["checked"] else "setup.update.status", version=v)
            title, enabled = t("setup.update.check"), True
        if self.update_label.stringValue() != text:
            self.update_label.setStringValue_(text)
        if self.update_button.title() != title:
            self.update_button.setTitle_(title)
        if bool(self.update_button.isEnabled()) != enabled:
            self.update_button.setEnabled_(enabled)

    def refresh_models(self) -> None:
        """For each model: ✅ in use, ☑️ downloaded, ⏳ in progress (with the %), ⬇️ to download."""
        from .llm import MODELS, local_path

        busy, pct = self.menubar.download_status()
        self.refresh_speech(busy, pct)
        current = config.load()["llm"]["model"]
        for row in self.model_rows:
            key = row["key"]
            if key not in self.downloaded or self.ticks % 3 == 0:
                self.downloaded[key] = bool(local_path(MODELS[key]))
            if busy == key:
                mark, title, enabled = "⏳", t("setup.model.progress", pct=pct), False
            elif self.downloaded[key] and key == current:
                mark, title, enabled = "✅", t("setup.model.in_use"), False
            elif self.downloaded[key]:
                mark, title, enabled = "☑️", t("setup.model.use"), True
            else:
                mark, title, enabled = "⬇️", t("setup.model.download"), busy is None
            if row["status"].stringValue() != mark:
                row["status"].setStringValue_(mark)
            if row["button"].title() != title:
                row["button"].setTitle_(title)
            if bool(row["button"].isEnabled()) != enabled:
                row["button"].setEnabled_(enabled)

    def _permission_row(self, index: int, key: str):
        from AppKit import NSStackView, NSUserInterfaceLayoutOrientationVertical

        status = self._label("", size=15)
        self.status_labels.append(status)
        texts = NSStackView.alloc().init()
        texts.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        texts.setAlignment_(1)
        texts.setSpacing_(2)
        texts.addArrangedSubview_(self._label(t(f"setup.{key}.title"), bold=True))
        text = self._label(t(f"setup.{key}.text"), wrap=True, secondary=True, size=11)
        text.setPreferredMaxLayoutWidth_(WIDTH - 250)
        texts.addArrangedSubview_(text)
        texts.widthAnchor().constraintEqualToConstant_(WIDTH - 250).setActive_(True)  # aligned buttons
        return self._row(status, texts, self._button(t(f"setup.{key}.button"), "permission:", tag=index))

    # --- refresh ---

    def needs_restart(self) -> bool:
        """Input Monitoring granted after launch: it only takes effect after a restart."""
        return not getattr(self.menubar, "keys_active", True) and audio.input_monitoring_allowed()

    def refresh(self) -> None:
        """Live states; the window is only touched if something changed (no needless relayout)."""
        for label, (_, check, _) in zip(self.status_labels, PERMISSIONS):
            state = check()
            mark = "✅" if state else "⚪️" if state is None else "❌"
            if label.stringValue() != mark:
                label.setStringValue_(mark)
        self.ticks = getattr(self, "ticks", 0) + 1
        if not hasattr(self, "downloaded"):
            self.downloaded: dict = {}
        self.refresh_models()
        if hasattr(self, "update_label"):
            self.refresh_update()
        if self.ticks % 3 == 0 and hasattr(self, "timer_button"):  # every 3 s: shortcuts list (~20 ms)
            self.refresh_timer()
        restart = self.needs_restart()
        if self.restart_hint.isHidden() == restart:
            self.restart_hint.setHidden_(not restart)
            self.finish.setTitle_(t("setup.restart") if restart else t("setup.finish"))

    def refresh_timer(self) -> None:
        from .reminders import SHORTCUT_SOURCE, control_shortcut, duplicates, timer_shortcut

        copies = duplicates()
        self.timer_duplicates.setStringValue_(t("setup.timer.duplicates", names=", ".join(t("quoted", text=n) for n in copies)) if copies else "")
        self.timer_duplicates.setHidden_(not copies)
        installed = bool(timer_shortcut()) and bool(control_shortcut())
        self.timer_status.setStringValue_("✅" if installed else "⚪️")
        # Installed: "Update" (project version); otherwise "Install", or the manual steps
        self.timer_button.setHidden_(installed and not SHORTCUT_SOURCE.exists())
        title = "setup.timer.update" if installed else "setup.timer.install" if SHORTCUT_SOURCE.exists() else "setup.timer.button"
        self.timer_button.setTitle_(t(title))

    def refresh_understood(self) -> None:
        from .router import understood_languages

        for box, code in zip(self.understood_boxes, LANGUAGES):
            box.setState_(int(code in understood_languages()))  # the last language cannot be unticked

    def close(self, reopen: bool = False) -> None:
        global _window
        self.timer.invalidate()
        self.window.setDelegate_(None)
        self.window.orderOut_(None)
        _window = None
        if reopen:
            show(self.menubar)


def data_size_gb() -> float:
    """Size of the data folder (mostly models), in GB."""
    total = 0
    for f in config.DATA_DIR.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return round(total / 1e9, 1)


def menubar_name() -> str:
    from .ui import assistant_name

    return assistant_name()


def show(menubar) -> None:
    """Opens the setup window (or brings it to the front if it is already open)."""
    global _window
    from AppKit import NSApplication

    if _window is None:
        from .reminders import forget_shortcuts

        forget_shortcuts()  # shortcuts added or removed by hand in the meantime: fresh state on opening
        _window = SetupWindow(menubar)
    else:
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        _window.window.makeKeyAndOrderFront_(None)
