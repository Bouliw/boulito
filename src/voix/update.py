"""App updates (.dmg version) from GitHub Releases.

Check: shortly after launch, then once a day (checkbox in the setup window), one request to the GitHub API,
with no account or credentials; a notification announces a newer version, only once.
Install (button in the setup window or the menu): the .dmg is downloaded, its SHA-256 digest compared with
the one GitHub publishes, then the app inside is accepted only if it is signed by Boulito's release
certificate (RELEASE_REQUIREMENT): a damaged file or a fake version is rejected. It then replaces
this one and relaunches; settings, models and permissions are kept.
The source version (project folder) does not update this way: git pull && ./install.sh.
"""

import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from . import config

REPO = "Bouliw/boulito"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
# Only an app signed by the release certificate (./app/signing.sh release) can replace this one
RELEASE_REQUIREMENT = ('identifier "io.github.bouliw.boulito" and '
                       'certificate leaf = H"a051a5d4913d0e92541973afa53fc30ae4664665"')
STATE_FILE = config.CACHE_DIR / "update.json"  # last version announced: a single notification per version
STAGED_NAME = ".Boulito-update.app"
MAX_DMG_BYTES = 1 << 30  # the .dmg is about 280 MB: a larger one announced or received is refused


class UpdateError(Exception):
    """reason: network, size, digest, signature, version, location, source"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def current_version() -> str:
    if config.PACKAGED:
        with (config.APP_PATH / "Contents" / "Info.plist").open("rb") as f:
            return str(plistlib.load(f).get("CFBundleShortVersionString", "0"))
    import tomllib

    return tomllib.loads((config.PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def parse(version: str) -> tuple[int, ...]:
    """Turns "0.2.10" into (0, 2, 10): comparable, unlike the text."""
    return tuple(int(n) for n in re.findall(r"\d+", version)[:4]) or (0,)


def _open(url: str, accept: str, timeout: float):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"Boulito/{current_version()}"})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except OSError as e:  # offline, GitHub unreachable, timeout
        raise UpdateError("network") from e


def latest() -> dict | None:
    """Latest published version: {"version", "url", "size", "digest", "page"}; None if it has no .dmg."""
    with _open(LATEST, "application/vnd.github+json", 15) as response:
        release = json.load(response)
    assets = [a for a in release.get("assets", []) if str(a.get("name", "")).endswith(".dmg")]
    if not assets or release.get("draft") or release.get("prerelease"):
        return None
    asset = assets[0]
    return {"version": str(release.get("tag_name", "")).lstrip("v"), "url": str(asset.get("browser_download_url", "")),
            "size": int(asset.get("size") or 0), "digest": str(asset.get("digest") or ""),
            "page": str(release.get("html_url", ""))}


def available() -> dict | None:
    """The latest version if it is newer than this one, otherwise None."""
    info = latest()
    return info if info and parse(info["version"]) > parse(current_version()) else None


def should_notify(version: str) -> bool:
    """True the first time a version is found (no notification on every launch)."""
    try:
        notified = json.loads(STATE_FILE.read_text()).get("notified")
    except (OSError, ValueError):
        notified = None
    if notified == version:
        return False
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"notified": version}))
    return True


def when_notifications_decided(state: Callable[[], bool | None], wait: Callable[[float], None] | None = None,
                               limit: int = 24 * 60) -> None:
    """Waits (at most limit minutes) until macOS has allowed or denied notifications (state() is no longer None)."""
    import time

    for _ in range(limit):
        if state() is not None:
            return
        (wait or time.sleep)(60)


def download(info: dict, progress: Callable[[int], None] | None = None) -> Path:
    """Downloads the .dmg to the cache and checks its SHA-256 digest (the one published by GitHub).

    Its size must be the one GitHub announces, at most MAX_DMG_BYTES: the download stops as soon as it is exceeded
    (the digest is only known at the end, and the disk could fill up in the meantime)."""
    if not info["url"].startswith(DOWNLOADS):  # only files from Boulito's Releases
        raise UpdateError("signature")
    expected = info["digest"].removeprefix("sha256:").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):  # no published digest: nothing is installed
        raise UpdateError("digest")
    size = info["size"]
    if not 0 < size <= MAX_DMG_BYTES:
        raise UpdateError("size")
    folder = config.CACHE_DIR / "update"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    target = folder / "Boulito.dmg"
    digest, done = hashlib.sha256(), 0
    with _open(info["url"], "application/octet-stream", 60) as response, target.open("wb") as f:
        while True:
            try:
                chunk = response.read(1 << 20)
            except OSError as e:
                raise UpdateError("network") from e
            if not chunk:
                break
            done += len(chunk)
            if done > size:
                break
            f.write(chunk)
            digest.update(chunk)
            if progress:
                progress(min(99, done * 100 // size))
    if done > size:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("size")
    if digest.hexdigest() != expected:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("digest")
    return target


def verify(app: Path) -> None:
    """The app is intact, signed by Boulito's release certificate, and newer than this one."""
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "-R", "=" + RELEASE_REQUIREMENT,
                             str(app)], capture_output=True, timeout=600)
    if result.returncode != 0:
        raise UpdateError("signature")
    try:
        with (app / "Contents" / "Info.plist").open("rb") as f:
            info = plistlib.load(f)
    except (OSError, ValueError) as e:
        raise UpdateError("signature") from e
    if info.get("CFBundleIdentifier") != config.BUNDLE_ID:
        raise UpdateError("signature")
    if parse(str(info.get("CFBundleShortVersionString", "0"))) <= parse(current_version()):
        raise UpdateError("version")


def prepare(dmg: Path) -> Path:
    """Opens the .dmg, checks the app inside and copies it next to this one (same folder: instant swap)."""
    app = config.APP_PATH
    if not config.PACKAGED:
        raise UpdateError("source")
    if "/AppTranslocation/" in str(app) or not os.access(app.parent, os.W_OK):
        raise UpdateError("location")  # app launched from the .dmg or Downloads, or protected folder
    mount = Path(tempfile.mkdtemp(prefix="boulito-update-"))
    staged = app.parent / STAGED_NAME
    try:
        subprocess.run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-quiet",
                        "-mountpoint", str(mount), str(dmg)], check=True, timeout=180)
        try:
            verify(mount / "Boulito.app")
            shutil.rmtree(staged, ignore_errors=True)
            subprocess.run(["/usr/bin/ditto", str(mount / "Boulito.app"), str(staged)], check=True, timeout=900)
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", str(mount)], timeout=120)
    except subprocess.SubprocessError as e:
        shutil.rmtree(staged, ignore_errors=True)
        raise UpdateError("other") from e
    finally:
        dmg.unlink(missing_ok=True)  # the .dmg is no longer needed; no other file is ever deleted
        if dmg.parent == config.CACHE_DIR / "update":
            shutil.rmtree(dmg.parent, ignore_errors=True)
        try:
            mount.rmdir()
        except OSError:
            pass
    try:
        verify(staged)  # once more, on the copy that will be launched
    except UpdateError:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return staged


def switch_after_exit(staged: Path) -> None:
    """Once Boulito has quit: the new app takes the old one's place (restored if that fails), then opens."""
    app = config.APP_PATH
    old = app.parent / ".Boulito-old.app"
    parent = os.environ.get("BOULITO_APP_PID") or str(os.getppid())  # the launcher: wait until it is gone
    script = ('for i in $(seq 150); do kill -0 "$1" 2>/dev/null || break; sleep 0.2; done; '
              'rm -rf "$4"; '
              'if mv "$2" "$4"; then if mv "$3" "$2"; then rm -rf "$4"; else mv "$4" "$2"; fi; fi; '
              'open "$2"')
    subprocess.Popen(["/bin/sh", "-c", script, "sh", parent, str(app), str(staged), str(old)],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
