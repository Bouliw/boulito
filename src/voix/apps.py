"""Open, quit and switch between apps, without Apple Events (no Automation permission).

- open an app or switch to it: `open -a` (LaunchServices), which brings it to the front;
- quit: NSRunningApplication.terminate(), the same request as "Quit" in the Dock
  (the app may offer to save; nothing is forced).
"""

import difflib
import subprocess
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import config
from .i18n import t

APP_DIRS = [
    Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
    Path("/System/Applications/Utilities"), Path.home() / "Applications",
]
EXTRA_APPS = [Path("/System/Library/CoreServices/Finder.app")]
# Terminal runs Voix during development; the Finder cannot be quit
NEVER_QUIT = {"com.apple.Terminal", "com.apple.finder"}
ARTICLES = ("l'application ", "l'appli ", "l'app ", "application ", "appli ", "app ",
            "le ", "la ", "les ", "l'", "the ", "mes ", "mon ", "ma ", "my ")
# Common words that only mean an app when they are its exact name (« la vidéo » ≠ Prime Video)
GENERIC = {"video", "videos", "page", "pages", "fichier", "fichiers", "document", "documents", "lien",
           "onglet", "fenetre", "chaine", "film", "films", "photo", "image", "images", "truc", "file",
           "window", "tab", "link", "movie", "movies", "son", "sound", "volume", "app", "apps", "appli",
           "application", "applications", "ecran", "screen", "lecteur", "player", "mac", "ordi", "ordinateur"}


class AppError(Exception):
    pass


def fold(name: str) -> str:
    """Comparable name: lowercase, without accents, spaces or punctuation."""
    name = unicodedata.normalize("NFD", name.lower())
    return "".join(c for c in name if c.isalnum())


@dataclass
class App:
    name: str               # display name (localized: « Réglages Système »)
    path: Path
    keys: frozenset[str]    # folded names: file, display name, aliases
    parts: frozenset[str] = frozenset()  # name starts and ends in whole words: « finalcut », « word » (Microsoft Word)


def word_parts(names: list[str]) -> frozenset[str]:
    """« Final Cut Pro » → final, finalcut, pro, cutpro…; never a piece of a word (« son » is not « Maison »)."""
    import re

    parts = set()
    for name in names:
        words = [fold(w) for w in re.split(r"[\s\-–_.]+", name) if fold(w)]
        for i in range(1, len(words)):
            parts.add("".join(words[:i]))
            parts.add("".join(words[i:]))
    return frozenset(p for p in parts if len(p) >= 3)


def french_names(path: Path) -> list[str]:
    """French names of the app (« Calculatrice », « Réglages Système »), even when macOS is in English."""
    from Foundation import NSBundle

    bundle = NSBundle.bundleWithPath_(str(path))
    if bundle is None:
        return []
    names = []
    for key in ("CFBundleDisplayName", "CFBundleName"):
        value = bundle.localizedStringForKey_value_table_localizations_(key, "", "InfoPlist", ["fr"])
        if value and str(value) != key:
            names.append(str(value))
    return names


@cache
def installed() -> list[App]:
    from Foundation import NSFileManager

    fm = NSFileManager.defaultManager()
    aliases: dict[str, list[str]] = {}
    for spoken, target in config.load().get("apps", {}).get("aliases", {}).items():
        aliases.setdefault(fold(target), []).append(spoken)
    apps = []
    for path in [p for d in APP_DIRS if d.exists() for p in sorted(d.glob("*.app"))] + EXTRA_APPS:
        display = str(fm.displayNameAtPath_(str(path))).removesuffix(".app")
        names = [path.stem, display, *french_names(path)] + aliases.get(fold(display), []) + aliases.get(fold(path.stem), [])
        apps.append(App(display, path, frozenset(fold(n) for n in names if n), word_parts([n for n in names if n])))
    return apps


# Fixed index for tests (./voix route --test, and the GitHub test): results do not depend on the apps
# installed on the machine. « Maison » (Home) and « Apps » are traps for fuzzy matching.
TEST_APPS = {"Final Cut Pro": [], "Notes": [], "Safari": [], "Calculator": ["Calculatrice"],
             "System Settings": ["Réglages Système"], "Preview": ["Aperçu"], "Music": ["Musique"], "Messages": [],
             "Discord": [], "WhatsApp": [], "Telegram": [], "Spotify": [], "Visual Studio Code": [], "Photos": [],
             "Terminal": [], "Mail": [], "Calendar": ["Calendrier"], "Reminders": ["Rappels"], "Clock": ["Horloge"],
             "Shortcuts": ["Raccourcis"], "Home": ["Maison"], "Apps": [], "TextEdit": [], "Google Chrome": [],
             "Prime Video": [], "Pages": [], "QuickTime Player": [], "App Store": []}


def test_index() -> list[App]:
    aliases: dict[str, list[str]] = {}
    for spoken, target in config.load().get("apps", {}).get("aliases", {}).items():
        aliases.setdefault(fold(target), []).append(spoken)
    index = []
    for name, others in TEST_APPS.items():
        names = [name, *others, *aliases.get(fold(name), [])]
        index.append(App(name, Path(f"/Applications/{name}.app"), frozenset(fold(n) for n in names), word_parts(names)))
    return index


def find(spoken: str, strict: bool = False) -> App | None:
    """The installed app that best matches the spoken name, or None.

    strict (to quit an app): exact name, or start or end of the name in whole words, never a fuzzy
    match (« Ferme Photoshop » must not quit Photos).
    """
    spoken = spoken.strip().rstrip(".!?").lower()
    for article in ARTICLES:
        if spoken.startswith(article):
            spoken = spoken[len(article):]
            break
    wanted = fold(spoken)
    if len(wanted) < 2:
        return None
    apps = installed()
    exact = [a for a in apps if wanted in a.keys]
    if exact:
        return exact[0]
    if wanted in GENERIC:
        return None
    # « Final Cut » → « Final Cut Pro »; « Word » → « Microsoft Word »: whole words only
    partial = [a for a in apps if wanted in a.parts]
    if partial:
        return min(partial, key=lambda a: len(a.name))
    if strict:
        return None
    # Partial word (« calcul » → Calculator), on a one-word name
    started = [(len(k), a) for a in apps for k in a.keys if len(wanted) >= 5 and k.startswith(wanted)]
    if started:
        return min(started, key=lambda t: t[0])[1]
    # Transcription error (« pnote » for « Notes »): strong similarity, similar length
    scored = [(difflib.SequenceMatcher(None, wanted, k).ratio(), a) for a in apps for k in a.keys
              if abs(len(k) - len(wanted)) <= max(1, len(k) // 5)]
    if not scored:
        return None
    score, best = max(scored, key=lambda t: t[0])
    return best if len(wanted) >= 4 and score >= 0.78 else None


def running(app: App):
    """Running instances of the app (NSRunningApplication)."""
    from AppKit import NSWorkspace

    target = str(app.path.resolve())
    return [r for r in NSWorkspace.sharedWorkspace().runningApplications()
            if r.bundleURL() is not None and str(Path(r.bundleURL().path()).resolve()) == target]


def require(spoken: str, strict: bool = False) -> App:
    app = find(spoken, strict)
    if app is None:
        raise AppError(t("app.not_found", name=spoken))
    return app


def wait_front(app: App, timeout: float = 6.0) -> bool:
    """Waits until the app is frontmost: what follows (« … puis écris … ») must target it, not the previous app."""
    import time

    from AppKit import NSWorkspace

    target = str(app.path.resolve())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and front.bundleURL() is not None and str(Path(front.bundleURL().path()).resolve()) == target:
            time.sleep(0.15)  # time for its window to take keyboard focus
            return True
        time.sleep(0.05)
    return False


def open_app(name: str) -> str:
    app = require(name)
    subprocess.run(["open", "-a", str(app.path)], check=True, timeout=10)
    wait_front(app)
    return t("app.opened", app=app.name)


def switch_app(name: str) -> str:
    app = require(name)
    was_running = bool(running(app))
    subprocess.run(["open", "-a", str(app.path)], check=True, timeout=10)
    wait_front(app)
    return t("app.switched", app=app.name) if was_running else t("app.opened", app=app.name)


def quit_app(name: str) -> str:
    app = require(name, strict=True)
    instances = running(app)
    if not instances:
        return t("app.not_running", app=app.name)
    if any(r.bundleIdentifier() in NEVER_QUIT for r in instances):
        raise AppError(t("app.protected", app=app.name))
    for r in instances:
        r.terminate()
    return t("app.quit", app=app.name)
