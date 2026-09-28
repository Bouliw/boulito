"""Apple Music: play an artist, an album, a song or a playlist from the library.

Through AppleScript (osascript): the requested text is passed as an argument, never pasted into the
script, so no injection is possible. Asks once for the Automation → Music permission.

Only the library is reachable (added or purchased tracks), not the whole Apple Music catalog.
To play an artist's or an album's tracks in a row, Boulito fills its own playlist,
"Boulito queue" (created if needed, emptied each time): it never touches any other playlist.
"""

import difflib
import random
import subprocess
import unicodedata

QUEUE = "Boulito queue"
MAX_TRACKS = 150

SEARCH = '''on run argv
  set q to item 1 of argv
  set scope to item 2 of argv
  tell application "Music"
    if scope is "artists" then
      set found to search library playlist 1 for q only artists
    else if scope is "albums" then
      set found to search library playlist 1 for q only albums
    else if scope is "songs" then
      set found to search library playlist 1 for q only songs
    else
      set found to search library playlist 1 for q
    end if
    set n to count of found
    if n > 300 then set n to 300
    set out to {}
    repeat with i from 1 to n
      set tr to item i of found
      set end of out to (persistent ID of tr) & tab & (name of tr) & tab & (artist of tr) & tab & (album of tr) & tab & ((disc number of tr) as text) & tab & ((track number of tr) as text)
    end repeat
    set AppleScript's text item delimiters to linefeed
    return out as text
  end tell
end run'''

PLAYLISTS = '''tell application "Music" to set playlistNames to name of every user playlist
set AppleScript's text item delimiters to linefeed
return playlistNames as text'''

PLAY_PLAYLIST = '''on run argv
  tell application "Music"
    set shuffle enabled to ((item 2 of argv) is "1")
    play user playlist (item 1 of argv)
  end tell
end run'''

PLAY_TRACKS = '''on run argv
  set pname to item 1 of argv
  tell application "Music"
    if not (exists user playlist pname) then make new user playlist with properties {name:pname}
    set p to user playlist pname
    delete every track of p
    repeat with i from 3 to count of argv
      duplicate (every track of library playlist 1 whose persistent ID is (item i of argv)) to p
    end repeat
    set shuffle enabled to ((item 2 of argv) is "1")
    play p
  end tell
end run'''

PLAY_ONE = '''on run argv
  tell application "Music" to play (first track of library playlist 1 whose persistent ID is (item 1 of argv))
end run'''

NOW_PLAYING = '''if application "Music" is running then
  tell application "Music"
    if player state is playing then return (name of current track) & tab & (artist of current track) & tab & (album of current track)
  end tell
end if
return ""'''


class MusicError(Exception):
    pass


def osascript(script: str, *args: str, timeout: float = 20) -> str:
    out = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        error = out.stderr.strip()
        if "-1743" in error or "Not authorized" in error:
            raise MusicError("not_allowed")
        raise MusicError(error.splitlines()[-1] if error else "osascript")
    return out.stdout.strip()


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    return " ".join("".join(c if c.isalnum() else " " for c in text if unicodedata.category(c) != "Mn").split())


def similar(a: str, b: str) -> float:
    a, b = fold(a), fold(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9 if min(len(a), len(b)) >= 4 else 0.6
    return difflib.SequenceMatcher(None, a, b).ratio()


def search(query: str, scope: str = "all") -> list[dict]:
    lines = osascript(SEARCH, query, scope).splitlines()
    tracks = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 6:
            pid, name, artist, album, disc, number = parts
            tracks.append({"id": pid, "name": name, "artist": artist, "album": album,
                           "disc": int(disc or 0), "number": int(number or 0)})
    return tracks


def best_playlist(query: str) -> str | None:
    names = [n.strip() for n in osascript(PLAYLISTS).splitlines() if n.strip() and n.strip() != QUEUE]
    scored = sorted(((similar(query, n), n) for n in names), reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0.8 else None


def play(query: str, kind: str = "any", shuffle: bool = False) -> dict:
    """Plays the best match. Returns {"kind", "name", "artist", "count"}."""
    query = " ".join(query.split())[:100]
    if not query:
        raise MusicError("empty")
    if kind in ("playlist", "any"):
        name = best_playlist(query)
        if name:
            osascript(PLAY_PLAYLIST, name, "1" if shuffle else "0")
            return {"kind": "playlist", "name": name, "artist": "", "count": 0}
        if kind == "playlist":
            raise MusicError("not_found")
    scope = {"artist": "artists", "album": "albums", "song": "songs"}.get(kind, "all")
    tracks = search(query, scope)
    if not tracks:
        raise MusicError("not_found")
    by_artist = [x for x in tracks if similar(query, x["artist"]) >= 0.8]
    by_album = [x for x in tracks if similar(query, x["album"]) >= 0.8]
    by_name = sorted(tracks, key=lambda x: similar(query, x["name"]), reverse=True)
    if kind == "artist" or (kind == "any" and by_artist and len(by_artist) >= len(by_album)):
        chosen = by_artist or tracks
        if not shuffle:
            random.shuffle(chosen)  # an artist: varied order, not always the same first track
        return _queue(chosen, "artist", chosen[0]["artist"], shuffle)
    if kind == "album" or (kind == "any" and by_album):
        chosen = sorted(by_album or tracks, key=lambda x: (x["album"], x["disc"], x["number"]))
        return _queue(chosen, "album", chosen[0]["album"], shuffle, artist=chosen[0]["artist"])
    song = by_name[0]
    osascript(PLAY_ONE, song["id"])
    return {"kind": "song", "name": song["name"], "artist": song["artist"], "count": 1}


def _queue(tracks: list[dict], kind: str, name: str, shuffle: bool, artist: str = "") -> dict:
    tracks = tracks[:MAX_TRACKS]
    osascript(PLAY_TRACKS, QUEUE, "1" if shuffle else "0", *[x["id"] for x in tracks], timeout=60)
    return {"kind": kind, "name": name, "artist": artist, "count": len(tracks)}


def now_playing() -> dict | None:
    out = osascript(NOW_PLAYING)
    if not out:
        return None
    name, artist, album = (out.split("\t") + ["", "", ""])[:3]
    return {"name": name, "artist": artist, "album": album}
