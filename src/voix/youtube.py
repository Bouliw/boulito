"""YouTube actions and state reading, through the youtube.js code run in Safari.

Each function matches a tool on the allowlist (safety.TOOLS) and can be tested
on its own from the command line: ./voix yt …
Text read from the page (titles, channels) is written by strangers: it is data.
"""

import base64
import json
import re
import threading
import time
import unicodedata
import urllib.parse
from pathlib import Path

from . import safari
from .i18n import t

SOURCE = Path(__file__).with_name("youtube.js").read_text(encoding="utf-8")
VERSION = int(re.search(r"const VERSION = (\d+);", SOURCE).group(1))

PAGES = {  # page → path on youtube.com
    "home": "",
    "subscriptions": "feed/subscriptions",
    "history": "feed/history",
    "watch_later": "playlist?list=WL",
    "playlists": "feed/playlists",
    "you": "feed/you",
}

# Search filters: YouTube's "sp" parameter (base64-encoded protobuf)
SORT = {"relevance": 0, "rating": 1, "date": 2, "views": 3}
UPLOAD = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
KIND = {"video": 1, "channel": 2, "playlist": 3}
DURATION = {"short": 1, "long": 2, "medium": 3}  # under 4 min, over 20 min, 4 to 20 min

QUALITIES = {"4k": "hd2160", "2160p": "hd2160", "1440p": "hd1440", "1080p": "hd1080", "720p": "hd720",
             "480p": "large", "360p": "medium", "240p": "small", "144p": "tiny", "auto": "auto"}


class YouTubeError(Exception):
    pass


class Cancelled(YouTubeError):
    pass


CANCEL = threading.Event()  # emergency stop (safety.cancel)


class ConfirmationRequired(YouTubeError):
    pass


def call(name: str, *args):
    """Calls a youtube.js function in the YouTube tab and returns its value.

    Short call if the code is already in the page (same version), otherwise sends the full code.
    """
    run = f"window.__voix.run({json.dumps(name)}, {json.dumps(list(args))})"
    raw = safari.run_js(f"window.__voix && window.__voix.version === {VERSION} ? {run} : 'missing'")
    if raw == "missing":
        raw = safari.run_js(SOURCE + f"\n;window.__voix ? {run} : JSON.stringify({{ok: false, error: 'not_youtube'}})")
    result = json.loads(raw or '{"ok": false, "error": "empty"}')
    if not result["ok"]:
        raise YouTubeError(result["error"])
    return result["value"]


def wait_for(condition, timeout: float = 8.0) -> dict:
    """Re-reads the page's light state until the condition is true."""
    deadline = time.monotonic() + timeout
    while True:
        if CANCEL.is_set():
            raise Cancelled(t("yt.cancelled"))
        try:
            status = call("status")
            if condition(status):
                return status
        except (YouTubeError, safari.NoYouTubeTab):
            pass  # page still loading
        if time.monotonic() > deadline:
            raise YouTubeError(t("yt.not_loaded"))
        time.sleep(0.1)


def go(function: str, args: list, path: str, done) -> dict:
    """YouTube's internal navigation; if nothing moves, a regular load of the URL."""
    if not safari.has_youtube_tab():
        safari.open_url(safari.YOUTUBE + path)
        return wait_for(done, timeout=15)
    safari.show_youtube()
    call(function, *args)
    try:
        return wait_for(done, timeout=2.5)
    except YouTubeError:
        safari.open_url(safari.YOUTUBE + path)
        return wait_for(done, timeout=15)


def listed(status: dict) -> bool:
    """Page shown, navigation finished and at least one thumbnail."""
    return status["ready"] and status["navigated"] is not False and status["items"] > 0


# --- Tools ------------------------------------------------------------------

def state() -> dict:
    """Page type, visible videos numbered, player state."""
    return call("state")


def open_page(page: str) -> dict:
    if page not in PAGES:
        raise YouTubeError(t("yt.unknown_page", page=page, choices=", ".join(PAGES)))
    return go("open_page", [page], PAGES[page], lambda s: s["page"] == page and listed(s))


def search_params(sort: str | None = None, upload: str | None = None,
                  duration: str | None = None, kind: str | None = None) -> str:
    filters = b""
    if upload:
        filters += bytes([0x08, UPLOAD[upload]])
    if kind:
        filters += bytes([0x10, KIND[kind]])
    if duration:
        filters += bytes([0x18, DURATION[duration]])
    message = bytes([0x08, SORT[sort]]) if sort else b""
    if filters:
        message += bytes([0x12, len(filters)]) + filters
    return base64.b64encode(message).decode() if message else ""


def search(query: str, sort: str | None = None, upload: str | None = None,
           duration: str | None = None, kind: str | None = None) -> dict:
    params = search_params(sort, upload, duration, kind)
    path = "results?" + urllib.parse.urlencode({"search_query": query, **({"sp": params} if params else {})})
    return go("search", [query, params], path, lambda s: s["page"] == "results" and listed(s))


def fold(text: str) -> str:
    """Lowercase, without accents or punctuation: "HugoDécrypte" → "hugodecrypte"."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if c.isalnum())


def open_channel(name: str) -> dict:
    """A channel's "Videos" page: among the channels found, the one whose name matches best."""
    search(name, kind="channel")
    channels = [i for i in state()["items"] if i["kind"] == "channel" and i.get("url")]
    if not channels:
        raise YouTubeError(t("yt.channel_not_found", name=name))
    wanted = fold(name)
    channel = min(channels, key=lambda c: (fold(c["name"]) != wanted,
                                           not fold(c["name"]).startswith(wanted), c["index"]))
    return open_channel_url(channel["url"])


def open_channel_url(url: str) -> dict:
    """A channel's "Videos" page, from its address on YouTube ("/@hugodecrypte")."""
    if not url.startswith("/"):
        raise YouTubeError(t("yt.bad_channel_url", url=url))
    safari.open_url(safari.YOUTUBE + url.strip("/") + "/videos")
    return wait_for(lambda s: s["page"] == "channel" and listed(s), timeout=15)


def latest_video(channel: str) -> dict:
    open_channel(channel)
    return play(index=1)


def play(index: int | None = None, video_id: str | None = None) -> dict:
    if video_id:
        chosen = {"id": video_id}
        go("watch", [video_id], f"watch?v={video_id}", lambda s: s["page"] == "watch" and s["ready"] and s["player"])
    else:
        chosen = call("play_index", int(index))
        wait_for(lambda s: s["page"] in ("watch", "shorts", "channel") and s["ready"] and s["navigated"] is not False)
    return chosen


def nav(action: str):
    before = call("status")
    value = call("nav", action)
    if action in ("back", "forward", "next", "previous"):
        wait_for(lambda s: s["url"] != before["url"] and s["ready"])
    return value


def player(action: str, value=None):
    if action == "quality" and value is not None:
        value = QUALITIES.get(str(value).lower(), value)
    result = call("player", action, value)
    if action == "fullscreen":  # Safari may refuse fullscreen requested by a script: check it
        deadline = time.monotonic() + 0.6
        while not call("fullscreen_state"):
            if time.monotonic() > deadline:
                raise YouTubeError(t("yt.fullscreen_refused"))
            time.sleep(0.03)
    return result


def account(action: str, confirmed: bool = False):
    """Like, remove like, watch later; subscribe and unsubscribe after confirmation."""
    if action in ("subscribe", "unsubscribe"):
        if not confirmed:
            raise ConfirmationRequired(action)
        return call("account", action)
    return call("account", action)


def poll(function, timeout: float, step: float = 0.15):
    """Calls function again until it returns a truthy value; None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if CANCEL.is_set():
            raise Cancelled(t("yt.cancelled"))
        value = function()
        if value:
            return value
        time.sleep(step)
    return None


def transcript(timeout: float = 12) -> dict:
    """Transcript of the current video: {"source", "segments": [{"t": seconds, "text"}]}.

    Opens YouTube's "Transcript" panel, waits until the list is complete (same number
    of segments twice in a row), reads it and closes the panel if it was closed.
    """
    call("transcript", "open")
    try:
        seen = {"count": -1}

        def loaded():
            r = call("transcript", "read")
            done = r["count"] > 0 and r["count"] == seen["count"]
            seen["count"] = r["count"]
            return r if done else None

        result = poll(loaded, timeout, step=0.4)
    finally:
        call("transcript", "close")
    if not result:
        raise YouTubeError("no_transcript")
    return {"source": "panel", "segments": result["segments"]}


def comment_prepare(text: str) -> None:
    """Writes the text in the current video's comment box, without posting it."""
    wanted = " ".join(text.split())
    if not poll(lambda: call("comment", "load"), timeout=6):
        raise YouTubeError(t("comment.no_section"))
    if poll(lambda: call("comment", "fill", text), timeout=4) != wanted:
        call("comment", "cancel")
        raise YouTubeError(t("comment.not_filled"))


def comment_submit(text: str) -> None:
    """Posts the comment written by comment_prepare, then checks that it shows up."""
    wanted = " ".join(text.split())
    call("comment", "submit")
    published = poll(lambda: call("comment", "first") == wanted, timeout=8, step=0.3)
    call("nav", "top")
    if not published:
        raise YouTubeError(t("comment.not_visible"))


def comment_cancel() -> None:
    call("comment", "cancel")


def probe() -> dict:
    """Diagnostics: what the code finds in the current page (selectors, player API)."""
    return call("probe")
