"""Executor: runs only allowlisted tools (TOOLS, below), never a free-form shell.

- Subscribe and unsubscribe: confirmation required (a spoken « oui »).
- Emergency stop: cancel() interrupts the current action (Esc when not speaking).
- Text read from the YouTube page (titles, channels) is never treated as an instruction.
"""

import datetime
import inspect
import os
import re
import time
from dataclasses import dataclass
from typing import Callable

import subprocess

from . import apps, i18n, keys, messaging, safari, system, youtube
from .i18n import ACCOUNT, NAV, PAGES, PLAYER, label, spoken_duration, t
from .router import CHAINED, Call, fold

SENSITIVE = {("youtube_account", "subscribe"), ("youtube_account", "unsubscribe")}
CHANGES_PAGE = {"youtube_open", "youtube_search", "youtube_play", "youtube_nav"}
FOLLOW_UP = "\nNow do the rest of the command, if anything is left. Otherwise reply with no tool call."
FOLLOW_UP_BLOCKED = {"youtube_search", "youtube_open"}  # no new page in the follow-up round



class ToolError(Exception):
    pass


@dataclass
class Result:
    message: str               # notification: what was done
    spoken: str | None = None  # short spoken reply (say)


def clock(seconds: float) -> str:
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --- Tools --------------------------------------------------------------------

def open_app(name: str) -> Result:
    return Result(apps.open_app(name))


def quit_app(name: str) -> Result:
    return Result(apps.quit_app(name))


def switch_app(name: str) -> Result:
    return Result(apps.switch_app(name))


def youtube_open(page: str, channel: str | None = None) -> Result:
    if page == "channel":
        if not channel:
            raise ToolError(t("yt.channel_missing"))
        youtube.open_channel(channel)
        return Result(t("channel", name=channel))
    youtube.open_page(page)
    return Result(t("page", page=label(PAGES, page)))


def youtube_search(query: str, sort: str | None = None, upload: str | None = None,
                   duration: str | None = None, kind: str | None = None) -> Result:
    youtube.search(query, sort, upload, duration, kind)
    return Result(t("search", query=query))


def youtube_state() -> Result:
    return Result(t("state_read"))


def youtube_play(index: int | None = None, video_id: str | None = None, latest_from: str | None = None,
                 channel_url: str | None = None, title: str | None = None) -> Result:
    """title: title already known (list shown to the LLM), for the notification."""
    if channel_url:
        youtube.open_channel_url(channel_url)
        return Result(t("channel", name=channel_url.strip("/")))
    if latest_from:
        chosen = youtube.latest_video(latest_from)
    else:
        chosen = youtube.play(index=index, video_id=video_id)
    if title:
        chosen["title"] = title
    title = chosen.get("title") or chosen.get("name") or chosen.get("id") or ""
    return Result(t("playing", title=title))


def youtube_nav(action: str) -> Result:
    youtube.nav(action)
    return Result(label(NAV, action))


PLAYER_NUMBER = {"seek_by", "seek_back", "seek_to", "seek_fraction", "speed", "volume"}
MAC_VOLUME = {"volume": ("set",), "volume_up": ("up",), "volume_down": ("down",), "mute": ("mute",), "unmute": ("unmute",)}
PLAYER_SWITCH = {"theater", "captions", "autoplay", "loop"}


def coerce(action: str, value):
    """Value of a player action, as the LLM writes it ("30", "12:30", "on")."""
    if value is None or value == "":
        return None
    if action in PLAYER_NUMBER and isinstance(value, str):
        v = value.strip().rstrip("%sx").strip().replace(",", ".")
        if ":" in v:
            return sum(float(n) * 60**i for i, n in enumerate(reversed(v.split(":"))))
        return float(v)
    if action in PLAYER_SWITCH and isinstance(value, str):
        return value.strip().lower() in ("on", "true", "oui", "yes", "1", "active", "activé")
    return value


def player(action: str, value=None) -> Result:
    if action not in PLAYER:
        raise ToolError(t("error.player_action", action=action))
    try:
        value = coerce(action, value)
    except ValueError:
        raise ToolError(t("error.bad_value", action=action, value=value))
    try:
        result = youtube.player(action, value)
    except (youtube.YouTubeError, safari.SafariError) as e:
        # No YouTube video: « pause » goes through the media keys
        if action in ("play", "pause") and ("no_player" in str(e) or isinstance(e, safari.NoYouTubeTab)):
            return media("play_pause")
        raise
    name = label(PLAYER, action)
    if action in ("seek_by", "seek_back", "seek_to", "seek_fraction"):
        return Result(f"{name} → {clock(result)}")
    if action in ("speed", "faster", "slower"):
        return Result(f"{name} x{result:g}")
    if action in ("volume", "volume_up", "volume_down"):
        return Result(f"{name} {result} %")
    if action in ("theater", "captions", "autoplay", "loop"):
        return Result(f"{name} {t('on') if result else t('off')}")
    if action in ("chapter", "chapter_next", "chapter_previous", "captions_language", "quality"):
        return Result(t("label.value", label=name, value=result))
    if action == "skip_ad":
        return Result(t({"skipped": "ad.skipped", "no_ad": "ad.none", "not_skippable_yet": "ad.wait"}[result]))
    return Result(name)


def youtube_account(action: str, confirmed: bool = False) -> Result:
    youtube.account(action, confirmed=confirmed)
    return Result(label(ACCOUNT, action))


def youtube_comment(text: str, confirm: Callable[[str], bool] | None = None) -> Result:
    """Dictated comment, word for word: written in the box, read back, posted only after « oui »."""
    text = text.strip()
    if not text:
        raise ToolError(t("comment.empty"))
    youtube.comment_prepare(text)
    if confirm is None or not confirm(t("confirm.comment", text=text)):
        youtube.comment_cancel()
        return Result(t("confirm.nothing_sent"), spoken=t("confirm.nothing_sent"))
    youtube.comment_submit(text)
    return Result(t("comment.posted", text=text))


def youtube_info(what: str) -> Result:
    from . import timers

    try:
        p = youtube.state()["player"]
    except (youtube.YouTubeError, safari.SafariError):
        p = None
    clock_timer = what == "remaining" and (timers.active() or timers.running_in_clock())
    if not p and clock_timer:  # « il reste combien ? » with no video: the timer
        return timer("status")
    if p and clock_timer:  # a video and a timer: give both rather than picking one
        video_left = max(0, p["duration"] - p["position"])
        timer_text = timer("status").message
        text = t("remaining.both", video=spoken_duration(video_left), timer=timer_text)
        return Result(text, spoken=text)
    if not p:
        return Result(t("no_video"), spoken=t("no_video.say"))
    if what == "remaining":
        left = max(0, p["duration"] - p["position"])
        return Result(t("remaining", clock=clock(left)), spoken=t("remaining.say", duration=spoken_duration(left)))
    return Result(t("title.shown", title=p["title"], channel=p["channel"]), spoken=t("title.say", title=p["title"], channel=p["channel"]))


# Music search too vague (« mets de la musique »): just resume playback
VAGUE_MUSIC = {"musique", "de la musique", "music", "some music", "chanson", "chansons", "une chanson", "song", "songs",
               "a song", "son", "du son", "quelque chose", "something", "morceau", "un morceau", "titre"}

# Long dictation: wired in by the assistant (./voix start); without it (./voix run), unavailable
DICTATION: dict = {"start": None, "stop": None}


def dictation(action: str, text: str = "") -> Result:
    """Long dictation: each sentence is typed into the frontmost app, until « fin de dictée » or Esc.

    Same safeguards as « Écris »: never in a terminal or a password field, never Return.
    No spoken reply: it would be heard, then typed.
    """
    if action == "stop":
        if DICTATION["stop"]:
            DICTATION["stop"]()
        return Result(t("dictation.ended"))
    if action != "start":
        raise ToolError(t("error.unknown_action", action=action))
    if not DICTATION["start"]:
        raise ToolError(t("dictation.unavailable"))
    need_keyboard()
    keys.check_can_type()  # terminal or password: clear refusal before starting
    DICTATION["start"](str(text or ""))
    return Result(t("dictation.started"))


# Wired in by the assistant (./voix start): the already loaded LLM, and a spoken holding message
ENGINE: Callable[[], "llm.Engine"] | None = None
ON_WAIT: Callable[[str], None] | None = None


def youtube_summary(at=None) -> Result:
    """Summary of the current video (or of the part around at), read aloud.

    The LLM reads the transcript with no tools: text planted in the video can't trigger anything.
    """
    from . import llm

    try:
        p = youtube.state()["player"]
    except (youtube.YouTubeError, safari.SafariError):
        p = None
    if not p:
        return Result(t("no_video"), spoken=t("no_video.say"))
    at = coerce("seek_to", at) if at not in (None, "") else None
    if ON_WAIT:
        ON_WAIT(t("summary.wait"))
    try:
        segments = youtube.transcript()["segments"]
    except youtube.YouTubeError:
        return Result(t("summary.no_transcript"), spoken=t("summary.no_transcript"))
    if ENGINE is not None:  # in Boulito: the already loaded model, never a second copy
        engine, temporary = ENGINE(), False
        if engine is None or not engine.running():
            return Result(t("ask.not_ready"), spoken=t("ask.not_ready"))
    else:  # ./voix run: loaded just for this
        engine, temporary = llm.Engine(), True
        engine.start()
    try:
        text = engine.write(llm.summary_messages(p["title"], p["channel"], segments, at))
    finally:
        if temporary:
            engine.stop()
    text = " ".join(text.split())[:700]
    return Result(text, spoken=text)


def calculate(expression: str, unit: str = "") -> Result:
    """Exact calculation (« combien font 15 % de 80 » → 12): done in code, never by the LLM."""
    from . import calc

    try:
        value, rounded = calc.result(calc.evaluate(str(expression)))
    except calc.CalcError as e:
        text = t("calc.zero") if str(e) == "zero" else t("calc.error")
        return Result(text, spoken=text)
    text = t("calc.about" if rounded else "calc.result", value=f"{value} {unit}".strip())
    return Result(text, spoken=text)


def ask(question: str) -> Result:
    """Simple question (general knowledge, language, definition): spoken answer from the local AI.

    The model answers with no tools: it can't trigger anything on the Mac. A calculation it asks for
    ("CALC: 24*60*60") is done exactly in code (calculate).
    """
    from . import llm

    if ENGINE is not None:  # in Boulito: the already loaded model, never a second copy
        engine, temporary = ENGINE(), False
        if engine is None or not engine.running():
            return Result(t("ask.not_ready"), spoken=t("ask.not_ready"))
    else:  # ./voix run: loaded just for this
        engine, temporary = llm.Engine(), True
        engine.start()
    try:
        answer = engine.write(llm.question_messages(question), max_tokens=140)
    finally:
        if temporary:
            engine.stop()
    m = re.match(r"^\s*`?CALC\s*:\s*([\d\s.+\-*/()^]+)(.*)$", answer.split("\n")[0], re.IGNORECASE)
    if m:  # "CALC: 10*1.609 kilometers": the calculation done in code, the unit (letters only) spoken with it
        unit = m[2].strip(" .`")
        return calculate(m[1].strip(), unit if re.fullmatch(r"[^\W\d_][^\d+*/=()]{0,30}", unit) else "")
    answer = llm.spoken_answer(answer) or t("ask.no_answer")
    return Result(answer, spoken=answer)


def speak(text: str) -> Result:
    """Short reply. Never the raw page content (the LLM must not read it aloud)."""
    text = " ".join(str(text).split())[:220]
    if "youtube_state" in text.lower():
        raise ToolError(t("not_asked"))  # never the raw page content read aloud
    return Result(text, spoken=text)


# --- Mac: sound, display, sites, Shortcuts, music, typing, messages ----------------------

def need_keyboard() -> None:
    """Accessibility missing: Boulito adds itself to the System Settings list, opens the pane and says so."""
    if keys.accessibility_allowed():
        return
    if os.environ.get("__CFBundleIdentifier") != "local.boulito":
        raise ToolError(t("need.app_bundle"))  # never an Accessibility request for Terminal
    keys.accessibility_allowed(prompt=True)
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
    raise ToolError(t("need.accessibility"))


def system_volume(action: str, value=None) -> Result:
    if action not in ("up", "down", "set", "mute", "unmute"):
        raise ToolError(t("error.unknown_action", action=action))
    level = system.volume(action, coerce("volume", value) if value is not None else None)
    if action == "mute":
        return Result(t("mac.muted"))
    if action == "unmute":
        return Result(t("mac.unmuted"))
    return Result(t("mac.volume", v=level))


def screen(action: str) -> Result:
    if action == "lock":
        need_keyboard()
        system.lock_screen()
        return Result(t("screen.lock"))
    system.sleep_display()
    return Result(t("screen.sleep"))


def open_url(url: str) -> Result:
    return Result(t("url.opened", host=system.open_url(url)))


def run_shortcut(name: str) -> Result:
    return Result(t("shortcut.ran", name=system.run_shortcut(name)))


def media(action: str) -> Result:
    if action == "pause":  # « arrête la musique »: a real pause, never a restart (the media key toggles)
        paused = system.pause_players()
        return Result(t("media.pause") if paused else t("media.nothing"))
    if action not in keys.MEDIA:
        raise ToolError(t("error.unknown_action", action=action))
    need_keyboard()
    system.media(action)
    return Result(t(f"media.{action}"))


def timer(action: str, seconds=None, label: str = "", confirm=None) -> Result:
    """Timer or reminder: start (seconds, optional label), cancel, status.

    With a text (« rappelle-moi dans 20 minutes de… ») or beyond 24 h, it is a reminder in the Reminders app,
    created only after confirmation.
    """
    from . import timers

    from . import reminders

    if action == "start":
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise ToolError(t("timer.bad_duration"))
        if not 1 <= seconds <= timers.MAX_SECONDS:
            raise ToolError(t("timer.bad_duration"))
        duration = spoken_duration(seconds, precise=True)
        at = (datetime.datetime.now() + datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds")
        if label:  # reminder: in the Reminders app, notified by macOS (like Siri), after confirmation
            return reminder_add(label, at, confirm=confirm)
        if seconds > reminders.CLOCK_MAX_SECONDS:  # 24 h or more: Clock refuses, a reminder takes over
            when = i18n.when_label(datetime.datetime.fromisoformat(at), True, datetime.datetime.now())
            if confirm is None or not confirm(t("timer.long_confirm", when=when)):
                return Result(t("confirm.nothing_created"), spoken=t("confirm.nothing_created"))
            return reminder_add(t("timer.long_label"), at, confirm=lambda question: True)
        others = timers.running_in_clock()  # timers already running in Clock (according to Boulito's list)
        if reminders.start_native_timer(seconds):  # Clock timer, via the "Minuteur Boulito" shortcut
            timers.NATIVE[0] = True
            timers.launched(seconds)
            if reminders.control_shortcut():
                timers.cancel()  # a single source of truth: Clock; no more internal timer
            text = t("timer.native", duration=duration)
            if others:
                left = min(end for end, _ in timers.LAUNCHED[:-1]) - time.monotonic() if timers.LAUNCHED[:-1] else 0
                if left >= 1:
                    text += " " + t("timer.another", left=i18n.natural_duration(left))
            return Result(text, spoken=text)
        timers.start(seconds)  # otherwise, Boulito's own timer
        text = t("timer.started", duration=duration)
        return Result(text, spoken=text)
    if action == "cancel":
        if reminders.control_shortcut() and not timers.active():  # a single source of truth: Clock
            left = reminders.native_remaining()
            if left is not None and round(left) < 1:
                text = t("timer.none")
            elif not reminders.cancel_native_timer():
                text = t("timer.native_elsewhere")
            else:
                timers.forget_closest(left or 0)
                after = reminders.native_remaining()  # is another timer still running?
                text = t("timer.native_cancelled")
                if after and round(after) >= 1:
                    text += " " + t("timer.still_another", left=i18n.natural_duration(after))
        else:
            n = timers.cancel()
            text = (t("timer.cancelled") if n == 1 else t("timer.cancelled_many", n=n) if n
                    else t("timer.native_elsewhere") if timers.NATIVE[0] else t("timer.none"))
        return Result(text, spoken=text)
    if action == "status":
        if reminders.control_shortcut() and not timers.active():  # internal timer (launch shortcut missing): that one counts
            left = reminders.native_remaining()
            if left is None:
                text = t("timer.native_elsewhere")
            elif round(left) < 1:  # "0 sec": no timer
                text = t("timer.none")
            elif timers.running_in_clock() >= 2:  # the shortcut only reads the current timer: say so
                text = t("timer.several", n=timers.running_in_clock(), left=i18n.natural_duration(left))
            else:
                text = t("timer.left", left=i18n.natural_duration(left))
        elif timers.active():
            text = timers.describe()
        else:
            text = t("timer.native_elsewhere") if timers.NATIVE[0] else t("timer.none")
        return Result(text, spoken=text)
    raise ToolError(t("error.unknown_action", action=action))


def music_play(query: str, kind: str = "any", shuffle=False) -> Result:
    from . import music

    if kind not in ("any", "artist", "album", "song", "playlist"):
        kind = "any"
    try:
        r = music.play(str(query), kind, shuffle in (True, "true", "on", 1))
    except music.MusicError as e:
        if str(e) == "not_allowed":
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"])
            raise ToolError(t("music.not_allowed"))
        if str(e) == "not_found":
            return Result(t("music.not_found", query=query), spoken=t("music.not_found", query=query))
        raise ToolError(t("music.error", error=e))
    who = f" — {r['artist']}" if r["artist"] else ""
    return Result(t(f"music.{r['kind']}", name=r["name"], count=r["count"]) + who)


def music_info() -> Result:
    from . import music

    try:
        now = music.now_playing()
    except music.MusicError as e:
        raise ToolError(t("music.not_allowed") if str(e) == "not_allowed" else t("music.error", error=e))
    if not now:
        return Result(t("music.nothing"), spoken=t("music.nothing"))
    text = t("music.now", name=now["name"], artist=now["artist"])
    return Result(text, spoken=text)


def _moment(when: str, now: datetime.datetime) -> tuple[datetime.datetime, bool] | None:
    """Requested time: ISO (rules) or as spoken (« demain à 9 h », LLM). (moment, time given?)"""
    from . import dates

    try:
        moment = datetime.datetime.fromisoformat(str(when))
        return moment, not (moment.hour == 0 and moment.minute == 0 and "T" not in str(when))
    except ValueError:
        found = dates.extract(str(when), now)
        return (found.at, found.has_time) if found else None


def _reminders_call(function, *args):
    from . import reminders

    try:
        return function(*args)
    except reminders.ReminderError as e:
        if str(e) == "not_allowed":
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"])
            raise ToolError(t("reminder.not_allowed"))
        raise ToolError(t("reminder.error", error=e))


def reminder_add(text: str, when: str, confirm=None) -> Result:
    """Reminder in the Reminders app (alert at the given time), after read-back and « oui »."""
    from . import reminders

    now = datetime.datetime.now()
    found = _moment(when, now)
    if not found or found[0] <= now:
        raise ToolError(t("agenda.no_date"))
    moment, has_time = found
    if not has_time:
        moment = moment.replace(hour=9, minute=0, second=0)
    label = " ".join(str(text).split()) or t("reminder.default")
    when_text = i18n.when_label(moment, True, now)
    if confirm is None or not confirm(t("reminder.confirm", label=label, when=when_text)):
        return Result(t("confirm.nothing_created"), spoken=t("confirm.nothing_created"))
    seconds = (moment - datetime.datetime.now()).total_seconds()  # recomputed after the « oui »
    _reminders_call(reminders.add_reminder, label, max(1, seconds))
    done = t("reminder.added", label=label, when=when_text)
    return Result(done, spoken=done)


def reminder_list() -> Result:
    from . import reminders

    items = _reminders_call(reminders.list_reminders)
    if not items:
        return Result(t("reminder.none"), spoken=t("reminder.none"))
    now = datetime.datetime.now()
    parts = [r["name"] + (f" ({i18n.when_label(r['due'], True, now)})" if r["due"] else "") for r in items[:5]]
    if len(items) > 5:
        parts.append(t("agenda.more", n=len(items) - 5))
    text = t("reminder.list", items=", ".join(parts))
    return Result(text, spoken=text)


def calendar_list(when: str = "", days: int = 1) -> Result:
    """Events of one day (or of the next 7 days), read aloud. Read-only."""
    from . import agenda

    now = datetime.datetime.now()
    found = _moment(when, now) if when else (now, False)
    if not found:
        raise ToolError(t("agenda.no_date"))
    days = 7 if int(days or 1) > 1 else 1
    start = found[0].replace(hour=0, minute=0, second=0, microsecond=0)
    if days == 7:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        items = agenda.events(start, start + datetime.timedelta(days=days))
    except agenda.AgendaError:
        raise ToolError(t("agenda.not_allowed"))
    label = t("day.week") if days == 7 else i18n.day_label(start.date(), now.date())
    if not items:
        return Result(t("agenda.none", day=label), spoken=t("agenda.none", day=label))
    parts = []
    for e in items[:6]:
        day = f"{i18n.day_label(e['start'].date(), now.date())}, " if days == 7 else ""
        parts.append(day + (t("agenda.all_day_item", title=e["title"]) if e["all_day"]
                            else f"{i18n.clock_label(e['start'].hour, e['start'].minute)}, {e['title']}"))
    if len(items) > 6:
        parts.append(t("agenda.more", n=len(items) - 6))
    text = t("agenda.list", day=label[:1].upper() + label[1:], items=" ; ".join(parts))
    return Result(text, spoken=text)


def calendar_add(title: str, when: str, confirm=None) -> Result:
    """Event in the default calendar, after read-back (title, day, time) and « oui »."""
    from . import agenda

    now = datetime.datetime.now()
    found = _moment(when, now)
    if not found or found[0].date() < now.date():
        raise ToolError(t("agenda.no_date"))
    moment, has_time = found
    title = " ".join(str(title).split()) or t("agenda.default_title")
    when_text = i18n.when_label(moment, has_time, now)
    if agenda.status() != "granted":
        raise ToolError(t("agenda.not_allowed"))
    if confirm is None or not confirm(t("agenda.confirm", title=title, when=when_text)):
        return Result(t("confirm.nothing_created"), spoken=t("confirm.nothing_created"))
    start = moment if has_time else moment.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        calendar = agenda.add_event(title, start, all_day=not has_time)
    except agenda.AgendaError as e:
        raise ToolError(t("agenda.not_allowed") if str(e) == "not_allowed" else t("agenda.error", error=e))
    done = t("agenda.added", title=title, calendar=calendar, when=when_text)
    return Result(done, spoken=done)


def local_info(what: str) -> Result:
    if what not in ("time", "date", "battery"):
        raise ToolError(t("error.unknown_action", action=what))
    text = system.local_info(what)
    return Result(text, spoken=text)


def app_shortcut(action: str) -> Result:
    if action not in system.APP_SHORTCUTS:
        raise ToolError(t("error.unknown_action", action=action))
    need_keyboard()
    app = system.app_shortcut(action)
    return Result(t(f"shortcut.{action}", app=app))


LAST_TYPED = {"app": "", "at": 0.0, "text": ""}  # to separate two texts dictated in a row


def type_text(text: str) -> Result:
    """Types the dictated text into the frontmost app (never Return, never in a shell or a password field).

    Two « marque … » in a row in the same app (less than 2 min apart): a space between them.
    """
    need_keyboard()
    app = keys.frontmost()[0]
    follows = LAST_TYPED["app"] == app and time.monotonic() - LAST_TYPED["at"] < 120 and not LAST_TYPED["text"][-1:].isspace()
    for i, line in enumerate(text.split("\n")):  # « saute une ligne »: ⇧ + Return, never Return alone
        if i:
            keys.newline()
        if line.strip():
            keys.type_text((" " if follows and i == 0 else "") + line)
    LAST_TYPED.update(app=app, at=time.monotonic(), text=text)
    return Result(t("typed", text=text))


def send_message(app: str, recipient: str, text: str, confirm: Callable[[str], bool] | None = None) -> Result:
    """Opens the chat, writes the message, reads it back; sends it only after « oui »."""
    need_keyboard()
    text, recipient = text.strip(), recipient.strip()
    if not text or not recipient:
        raise ToolError(t("message.incomplete"))
    try:
        app_name, verified = messaging.prepare(app, recipient, text)
    except messaging.MessagingError:
        raise ToolError(t("message.apps"))
    if verified is False:
        return Result(t("message.wrong_chat", recipient=recipient))
    question = t("message.confirm", recipient=recipient, app=app_name, text=text)
    if verified is None:
        question += t("message.unverified")
    if confirm is None or not confirm(question):
        try:
            messaging.clear()
        except messaging.MessagingError:
            pass  # the app changed: erase nothing elsewhere
        return Result(t("confirm.nothing_sent"), spoken=t("confirm.nothing_sent"))
    try:
        messaging.send()
    except messaging.MessagingError:  # chat no longer in front: nothing is sent elsewhere
        return Result(t("confirm.nothing_sent"), spoken=t("confirm.nothing_sent"))
    return Result(t("message.sent", recipient=recipient, app=app_name))


TOOLS: dict[str, Callable[..., Result]] = {
    "open_app": open_app, "quit_app": quit_app, "switch_app": switch_app,
    "youtube_open": youtube_open, "youtube_search": youtube_search, "youtube_state": youtube_state,
    "youtube_play": youtube_play, "youtube_nav": youtube_nav, "player": player,
    "youtube_account": youtube_account, "youtube_info": youtube_info, "speak": speak,
    "youtube_comment": youtube_comment,
    "system_volume": system_volume, "screen": screen, "open_url": open_url, "run_shortcut": run_shortcut,
    "media": media, "type_text": type_text, "send_message": send_message, "app_shortcut": app_shortcut,
    "timer": timer, "local_info": local_info, "dictation": dictation, "reminder_add": reminder_add, "reminder_list": reminder_list,
    "calendar_list": calendar_list, "calendar_add": calendar_add, "youtube_summary": youtube_summary, "music_play": music_play, "music_info": music_info,
    "calculate": calculate, "ask": ask,
}
# Text typed without confirmation: if it comes from the LLM, it must appear word for word in the spoken sentence
TEXT_FROM_COMMAND = {"type_text", "dictation"}
# Opening or showing an app: the LLM may pick one tool for the other (« passe sur Notes » → open_app), so both
# accept the same verbs, in the six languages Boulito understands (a video title cannot open an app on its own)
APP_INTENT = (r"\b(?:ouvre|ouvrir|ouvrez|lance|lancer|demarre|demarrer|passe|va|aller|bascule|basculer|affiche|afficher"
              r"|montre|reviens|retourne|open|launch|start|switch|go|show|bring|abre|abrir|abra|lanza|inicia|cambia"
              r"|offne|offnen|starte|wechsle|zeige|apri|aprire|avvia|passa|vai|mostra)\b")
# Actions with side effects: when they come from the LLM, the spoken sentence must express that intent
# (the LLM once answered « ferme Safari » to « supprime mon historique »)
INTENT = {
    "open_app": APP_INTENT,
    "switch_app": APP_INTENT,
    "quit_app": r"\b(?:ferme|fermer|quitte|quitter|close|quit|exit|kill|arrete|arreter)\b",
    "screen": r"\b(?:veille|verrouille|verrouiller|lock|sleep|dors|ecran|screen|display)\b",
    "send_message": r"\b(?:envoie|envoyer|dis|dire|message|send|tell|text|ecris|ecrire|reponds|write)\b",
    "type_text": r"\b(?:ecris|ecrire|ecrit|ecrivez|tape|taper|tapes|saisis|saisir|marque|marc|mark|note|dicte|write|type|enter)\b",
    "reminder_add": r"\b(?:rappelle|rappeler|rappel|previens|prevenir|reveille|remind|reminder|wake)\b",
    "dictation": r"\b(?:dicte|dicter|dictee|dictez|dictation|dictate)\b",
    "calendar_add": r"\b(?:ajoute|ajouter|cree|creer|note|noter|mets|programme|planifie|inscris|add|create|schedule|put|book)\b",
    "app_shortcut": r"\b(?:nouvelle|nouveau|nouvel|new|onglet|tab|ferme|fermer|close|cherche|chercher|find|search|recherche)\b",
}
# Same check, for a specific action of a tool
ACTION_INTENT = {
    ("app_shortcut", "close_tab"): r"\b(?:ferme|fermer|close)\b",
    # Subscribe and unsubscribe: never one for the other (the model has mixed them up before)
    ("youtube_account", "subscribe"): r"^(?!.*\b(?:desabonn|unsubscrib))(?=.*\b(?:abonn|subscrib))",
    ("youtube_account", "unsubscribe"): r"\b(?:desabonn|unsubscrib)",
}
# Tools that post or type the user's text: only their dictated words (level-0 rules),
# never a call from the LLM, which could make up the text or pull it from the page.
DICTATION_ONLY = {"youtube_comment"}
# Tools triggered only by a rule (level 0), never by the LLM that handles commands
RULE_ONLY = {"ask"}
# Tools that ask for confirmation themselves, at the right moment (after showing the text)
USER_WORDS = {"reminder_add": ("text",), "calendar_add": ("title",)}


def _said(text: str, source: str) -> bool:
    """Every word (longer than 2 letters) of the text appears in the spoken sentence."""
    said = set(fold(source).split())
    return all(w in said for w in fold(text).split() if len(w) > 2)


ASKS_CONFIRMATION = {"youtube_comment", "send_message", "timer", "reminder_add", "calendar_add"}


ERRORS = {  # one value per language, in i18n.LABEL_ORDER order: en, fr, es, de, it, pt
    "no_player": ("no video playing", "pas de vidéo en cours", "ningún vídeo en reproducción", "kein Video läuft",
                  "nessun video in riproduzione", "nenhum vídeo tocando"),
    "no_chapters": ("this video has no chapters", "cette vidéo n'a pas de chapitres", "este vídeo no tiene capítulos",
                    "dieses Video hat keine Kapitel", "questo video non ha capitoli", "este vídeo não tem capítulos"),
    "chapter_not_found": ("chapter not found", "chapitre introuvable", "capítulo no encontrado", "Kapitel nicht gefunden",
                          "capitolo non trovato", "capítulo não encontrado"),
    "no_such_item": ("no video with that number", "pas de vidéo à ce numéro", "no hay ningún vídeo con ese número",
                     "kein Video mit dieser Nummer", "nessun video con questo numero", "nenhum vídeo com esse número"),
    "no_video": ("no video playing", "pas de vidéo en cours", "ningún vídeo en reproducción", "kein Video läuft",
                 "nessun video in riproduzione", "nenhum vídeo tocando"),
    "no_channel": ("channel not found", "chaîne introuvable", "canal no encontrado", "Kanal nicht gefunden",
                   "canale non trovato", "canal não encontrado"),
    "captions_language_unavailable": ("no subtitles in that language", "pas de sous-titres dans cette langue",
                                      "no hay subtítulos en ese idioma", "keine Untertitel in dieser Sprache",
                                      "nessun sottotitolo in questa lingua", "sem legendas nesse idioma"),
    "not_youtube": ("the tab is not on YouTube", "l'onglet n'est pas sur YouTube", "la pestaña no está en YouTube",
                    "der Tab ist nicht auf YouTube", "la scheda non è su YouTube", "a aba não está no YouTube"),
}


def describe_error(e: Exception) -> str:
    """Readable error message for the notification."""
    text = str(e)
    if isinstance(e, keys.KeyboardError):
        if text == "accessibility":
            return t("need.accessibility")
        if text.startswith("shell:"):
            return t("no_shell", app=text.split(":", 1)[1])
        if text == "password":
            return t("no_password")
        if text == "moved":
            return t("keys.moved")
    if text in ERRORS:
        return label(ERRORS, text)
    if text.startswith("not_found:"):
        return t("element.not_found", what=text.split(":", 1)[1])
    return text.splitlines()[0] if text else type(e).__name__


def read_state() -> dict | None:
    """YouTube state for the LLM, or None if there is no YouTube tab."""
    try:
        return youtube.state()
    except Exception:
        return None


LATEST = re.compile(r"\b(?:derniere|dernier|nouvelle|nouveau|recente|recent|latest|last|newest|new)\b")
FULLSCREEN = re.compile(r"\b(?:plein ecran|full ?screen)\b")
NO_FULLSCREEN = re.compile(r"\b(?:quitte|quitter|sors|sortir|enleve|exit|leave|pas|sans|not|without|no)\b[\w ]{0,25}(?:plein ecran|full ?screen)")


def wants_fullscreen(text: str) -> bool:
    """« … en plein écran » requested, and not « … et quitte le plein écran » or « … mais pas en plein écran »."""
    said = fold(text)
    return bool(FULLSCREEN.search(said)) and not NO_FULLSCREEN.search(said)


FULLSCREEN_TAIL = re.compile(r"[\s,]*(?:et |and )?(?:mets?[- ]la |put it )?(?:en |in )?(?:plein écran|plein ecran|full ?screen)[\s.!?]*$",
                             re.IGNORECASE)


def adjust_calls(calls: list, text: str, state) -> list:
    """Fixes two measured quirks of the model with safe rules (tests/level1.toml):
    - « mets la vidéo de Micode »: if a listed video comes from that channel and « dernière » was not
      said, it is that one (the model used to pick the channel's latest video);
    - « mets la vidéo sur la Russie en plein écran »: the requested full screen always follows the launched video.
    """
    from . import llm

    said = fold(text)
    adjusted = []
    for name, args, call_id in calls:
        channel = args.get("latest_from") if name == "youtube_play" else None
        if channel and not LATEST.search(said):
            wanted = fold(str(channel)).replace(" ", "")
            for number, item in enumerate(llm.listed(state), start=1):
                if item.get("kind") != "channel" and wanted and wanted in fold(str(item.get("channel", ""))).replace(" ", ""):
                    args = {"video_id": item["id"], "title": item.get("title")}  # the video from the page shown to the model
                    break
        adjusted.append((name, args, call_id))
    names = [name for name, _, _ in adjusted]
    if wants_fullscreen(text) and "youtube_play" in names and not any(
            name == "player" and args.get("action") == "fullscreen" for name, args, _ in adjusted):
        adjusted.append(("player", {"action": "fullscreen"}, "call_fullscreen"))
    return adjusted


def declared_params(tool: str) -> set[str]:
    """Parameters a tool declares to the LLM; the others (Python-only ones) never come from it."""
    from . import llm

    for spec in llm.TOOLS:
        function = spec.get("function", spec)
        if function.get("name") == tool:
            return set(function.get("parameters", {}).get("properties", {}))
    return set()


def resolve_index(args: dict, videos: list[dict]) -> dict:
    """Number in the list shown to the LLM → video (or channel) ID, independent of scrolling."""
    try:
        item = videos[int(args["index"]) - 1]
    except (ValueError, IndexError):
        raise ToolError(t("error.no_item", n=args["index"]))
    if item["kind"] == "channel":
        return {"channel_url": item["url"]}
    return {"video_id": item["id"], "title": item.get("title")}


def run_level1(text: str, engine, confirm: Callable[[str], bool] | None = None, max_rounds: int = 3,
               execute_fn: Callable = None, state_fn: Callable = None, previous: Call | None = None,
               allowed: set | None = None) -> tuple[list[Result], dict]:
    """Level 1: the LLM picks the tools, the executor checks and runs them.

    If the LLM asks for youtube_state (after a search, to pick a video), it gets
    the new page back and continues, in at most max_rounds rounds.
    Returns the results and timings (ms): state read, LLM, actions.
    execute_fn and state_fn replace the executor and the page read (tests without Safari).
    previous: the simple command run just before (« encore », « baisse encore », « plus bas »).
    allowed: conversation mode (sentence spoken without the name): only these tools run, and no reply is spoken.
    """
    from . import llm

    execute_fn = execute_fn or execute
    read_state = state_fn or globals()["read_state"]
    timings = {"state": 0.0, "llm": 0.0, "actions": 0.0, "rounds": 0}
    t = time.perf_counter()
    state = read_state()
    timings["state"] = (time.perf_counter() - t) * 1000
    # « … en plein écran »: the model only sees the first part (it used to forget one of the two actions);
    # adjust_calls adds full screen after the launched video
    asked = (FULLSCREEN_TAIL.sub("", text).strip() or text) if wants_fullscreen(text) else text
    context = llm.PREVIOUS.format(call=llm.describe_call(previous.tool, previous.args)) if previous else ""
    if allowed is not None:
        context += "Said without your name: it may not be meant for you. If it is not clearly a command, reply with nothing.\n"
    messages = [{"role": "system", "content": llm.system_prompt()},
                {"role": "user", "content": f"{llm.describe_state(state)}\n{context}Command: {asked}"}]
    results: list[Result] = []
    stale = False  # the page changed since the state shown to the LLM
    chained = bool(CHAINED.search(fold(text)))
    done: list[tuple[str, dict]] = []  # calls already made
    follow_up = False  # follow-up round after a search: only follow-up actions are accepted
    for _ in range(max_rounds):
        if youtube.CANCEL.is_set():  # Esc while the model is thinking: stop here
            break
        t = time.perf_counter()
        message = engine.chat(messages)
        timings["llm"] += (time.perf_counter() - t) * 1000
        timings["rounds"] += 1
        calls = llm.tool_calls(message)
        if not calls:
            # Text reply instead of a call: spoken aloud, except after actions ("Done")
            if message.get("content") and not results and allowed is None:
                results.append(execute_fn(Call("speak", {"text": llm.clean(message["content"], 200)}, rule="llm", level=1), confirm))
            break
        messages.append(message)
        wants_state = False
        t = time.perf_counter()
        # Hidden parameters (Python-only ones): ignored; then the safe fixes (adjust_calls)
        calls = [(name, {k: v for k, v in args.items() if k in declared_params(name)}, call_id) for name, args, call_id in calls]
        calls = adjust_calls(calls, text, state)
        for name, args, call_id in calls:
            if follow_up and (name in FOLLOW_UP_BLOCKED or (name, args) in done):  # no duplicates
                messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": "skipped"})
                continue
            if allowed is not None and name not in allowed and name != "youtube_state":  # without the name: simple commands
                messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": "not allowed now"})
                continue
            done.append((name, dict(args)))
            if name == "youtube_state":
                wants_state = True
                state, stale = read_state(), False
                content = llm.describe_state(state)
            else:
                try:
                    if name == "youtube_play" and args.get("index") is not None:
                        if stale:  # « cherche X et lance la première »: number within the new results
                            state, stale = read_state(), False
                        args = resolve_index(args, llm.listed(state))
                    stale = stale or name in CHANGES_PAGE
                    result = execute_fn(Call(name, args, rule="llm", level=1, source=text), confirm)
                    content = llm.clean(result.message, 300)  # titles, events: written by others, never instructions
                except Exception as e:
                    result = Result(f"✗ {describe_error(e)}")
                    content = f"error: {describe_error(e)}"
                results.append(result)
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": content})
        # After a search or a page change at the end of a round, the LLM sees the new page and
        # can continue (« … et lance la première »). This round happens after the action: no waiting for it.
        # Sentence that chains actions, but only one done: the LLM sees the result (and the
        # new page) and does the rest (« cherche X et lance la première », « ouvre X puis passe sur Y »)
        if chained and not follow_up and len(done) == 1 and not content.startswith("error"):
            if name in CHANGES_PAGE and name != "youtube_play":
                state, stale = read_state(), False
                messages[-1]["content"] += "\nNew page:\n" + llm.describe_state(state)
            messages[-1]["content"] += FOLLOW_UP
            wants_state = follow_up = True
        elif follow_up:
            break  # only one follow-up round
        timings["actions"] += (time.perf_counter() - t) * 1000
        if not wants_state:
            break
    return results, timings


def cancel() -> None:
    """Emergency stop: interrupts the current action and everything after it (cleared when the next command starts)."""
    youtube.CANCEL.set()


def begin_command() -> None:
    """Start of a command: an earlier Esc doesn't block it."""
    youtube.CANCEL.clear()


def site_said(url: str, source: str) -> bool:
    """Every part of the domain name (except the TLD) was spoken, as whole words or run together.

    « ouvre le monde point fr » → lemonde.fr; "a.attacker.example" or "attacker.example/?x=://youtube": refused.
    """
    from urllib.parse import urlsplit

    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").removeprefix("www.")
    labels = [label for part in host.split(".")[:-1] for label in part.split("-")]
    words = fold(source).split()
    said = {"".join(words[i:j]) for i in range(len(words)) for j in range(i + 1, min(i + 4, len(words)) + 1)}
    return bool(labels) and all(len(label) >= 2 and label in said for label in labels)


def execute(call: Call, confirm: Callable[[str], bool] | None = None) -> Result:
    """Runs an allowlisted call. confirm(question) must return True for a sensitive action."""
    tool = TOOLS.get(call.tool)
    if tool is None:
        raise ToolError(t("error.tool_not_allowed", tool=call.tool))
    if call.tool in DICTATION_ONLY and call.level != 0:
        raise ToolError(t("comment.dictated_only"))
    if call.tool in RULE_ONLY and call.level != 0:
        raise ToolError(t("not_asked"))
    if youtube.CANCEL.is_set():  # Esc during the command: nothing else goes out
        raise ToolError(t("yt.cancelled"))
    args = dict(call.args)
    if call.tool == "open_url" and call.level != 0 and not site_said(str(args.get("url", "")), call.source):
        raise ToolError(t("not_asked"))  # a site from the LLM must have been spoken (booby-trapped video title)
    if call.tool in TEXT_FROM_COMMAND and call.level != 0:
        text = str(args.get("text", ""))
        if fold(text) not in fold(call.source) or (text and not re.search(r"\w", text)):
            raise ToolError(t("text.not_said"))
        if "\n" in text and not re.search(r"ligne|paragraphe|line|paragraph", fold(call.source)):
            raise ToolError(t("text.not_said"))  # line break (⇧ + Return) only if it was spoken
    if call.tool == "send_message" and call.level != 0:  # the message and the recipient: the spoken words
        if not (_said(str(args.get("text", "")), call.source) and _said(str(args.get("recipient", "")), call.source)):
            raise ToolError(t("text.not_said"))
    query = fold(str(args.get("query", ""))) if call.tool == "music_play" else ""
    if (call.tool == "player" and args.get("action") in MAC_VOLUME and call.level != 0
            and "youtube" not in fold(call.source)):  # YouTube sound only if « YouTube » is said
        action = args["action"]
        call, tool = Call("system_volume", {}, rule=call.rule, level=call.level, source=call.source), TOOLS["system_volume"]
        args = {"action": MAC_VOLUME[action][0], **({"value": args["value"]} if "value" in args else {})}
    for key in USER_WORDS.get(call.tool, ()):  # text made up by the LLM or pulled from a page: removed
        if call.level != 0 and args.get(key) and not _said(str(args[key]), call.source):
            args[key] = ""
    if call.tool == "music_play" and call.level != 0 and (query not in fold(call.source) or query in VAGUE_MUSIC):
        call, tool, args = Call("media", {"action": "play_pause"}, rule=call.rule, level=call.level, source=call.source), TOOLS["media"], {"action": "play_pause"}
    if call.tool == "timer" and call.level != 0 and args.get("label") and fold(str(args["label"])) not in fold(call.source):
        args["label"] = ""  # made-up reminder or one pulled from a page: timer with no text
    if call.tool in INTENT and call.level != 0 and not re.search(INTENT[call.tool], fold(call.source)):
        raise ToolError(t("not_asked"))
    wanted = ACTION_INTENT.get((call.tool, args.get("action")))
    if wanted and call.level != 0 and not re.search(wanted, fold(call.source)):
        raise ToolError(t("not_asked"))
    if call.tool in ASKS_CONFIRMATION:
        args["confirm"] = confirm
    if (call.tool, args.get("action")) in SENSITIVE:
        question = t("confirm.subscribe") if args["action"] == "subscribe" else t("confirm.unsubscribe")
        if confirm is None or not confirm(question):
            return Result(t("confirm.nothing_changed"), spoken=t("confirm.nothing_changed"))
        args["confirmed"] = True
    try:
        inspect.signature(tool).bind(**args)
    except TypeError as e:  # unknown or missing argument (malformed LLM reply)
        raise ToolError(t("error.bad_arguments", tool=call.tool, error=e))
    return tool(**args)
