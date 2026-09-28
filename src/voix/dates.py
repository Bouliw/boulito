"""Dates and times spoken aloud (French and English), for reminders and the calendar.

extract(text, now) finds the date or time expression anywhere in the sentence
(« demain à 9 h », « lundi prochain à 14 h 30 », « dans une heure et quart », « on the 3rd of october »)
and returns it with the rest of the sentence, accents and case preserved. spoken() restates the
understood moment for the voice confirmation. Tests: tests/dates.toml, replayed by run_tests().

The rules work on a folded text (lowercase, no accents, punctuation → space) in which each
character keeps its position in the original sentence, so the original can be sliced.
"""

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .router import NUMBER_WORDS, number, timer_seconds

TESTS = Path(__file__).resolve().parents[2] / "tests" / "dates.toml"


@dataclass
class When:
    at: datetime          # understood moment (local time, naive)
    has_time: bool        # False if only the date was said (the time is then default_hour)
    text: str             # part of the original sentence recognized as date/time
    rest: str             # the original sentence without that part, whitespace cleaned up


# --- Vocabulary ----------------------------------------------------------------

WEEKDAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
          "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
          "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
          "september": 9, "october": 10, "november": 11, "december": 12}
ORDINALS_EN = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
               "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
               "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
               "twentieth": 20, "thirtieth": 30}

DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]

# Times of day: default time (in minutes) when no time is said
PERIODS = {"matin": "morning", "matinee": "morning", "morning": "morning",
           "apres midi": "afternoon", "aprem": "afternoon", "afternoon": "afternoon",
           "soir": "evening", "soiree": "evening", "evening": "evening", "tonight": "evening",
           "nuit": "night", "night": "night", "midi": "noon", "noon": "noon"}
PERIOD_DEFAULT = {"morning": 9 * 60, "afternoon": 15 * 60, "evening": 20 * 60, "night": 22 * 60, "noon": 12 * 60}
# Relative days: (offset in days, or None = today if still ahead, time of day)
RELATIVE_DAYS = {
    "aujourd'hui": (0, None), "aujourd hui": (0, None), "today": (0, None),
    "demain": (1, None), "tomorrow": (1, None),
    "apres demain": (2, None), "the day after tomorrow": (2, None), "day after tomorrow": (2, None),
    "ce soir": (0, "evening"), "tonight": (0, "evening"), "tonite": (0, "evening"), "this evening": (0, "evening"),
    "ce matin": (0, "morning"), "this morning": (0, "morning"),
    "cet apres midi": (0, "afternoon"), "cette apres midi": (0, "afternoon"), "cet aprem": (0, "afternoon"),
    "this afternoon": (0, "afternoon"), "ce midi": (0, "noon"), "cette nuit": (0, "night"),
    "in the morning": (None, "morning"), "in the afternoon": (None, "afternoon"), "in the evening": (None, "evening"),
    "dans la matinee": (None, "morning"), "dans l'apres midi": (None, "afternoon"), "dans la soiree": (None, "evening"),
}


def _alt(words) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


# Numbers: digits, or words (« quatorze », « vingt et une », « forty five »)
_W = _alt(w for w in NUMBER_WORDS if w not in ("cent", "hundred"))
_WM = _alt(w for w in NUMBER_WORDS if w not in ("cent", "hundred", "un", "une", "one"))  # minutes: not « une »
WORDNUM = rf"(?:(?:{_W})\b(?: (?:et )?(?:{_W})\b)?)"
# Hour in words: two words only for real compounds (« nine thirty » = 9:30, not 39)
HOUR = (rf"(?:\d{{1,2}}(?!\d)|(?:vingt et une?|vingt (?:deux|trois|quatre)|dix (?:sept|huit|neuf)|"
        rf"twenty (?:one|two|three|four)|{_W})\b)")
MIN = rf"(?:\d{{1,2}}(?!\d)|(?:{_WM})\b(?: (?:{_WM})\b)?)"
ORD_EN = rf"(?:(?:(?:twenty|thirty) )?(?:{_alt(ORDINALS_EN)})\b)"
MONTH = rf"(?:{_alt(MONTHS)})\b"
WEEKDAY_ALT = rf"(?:{_alt(WEEKDAYS)})\b"
NOT_CLOCK = r"(?![\d:/])(?! ?(?:heures?|h)(?![a-z]))"
DAYNUM = rf"(?:\d{{1,2}}(?:er|e|st|nd|rd|th)?{NOT_CLOCK}|premier\b|{ORD_EN}|{WORDNUM}{NOT_CLOCK})"
YEAR = r"(?: (?P<y>\d{4})\b(?!:))?"

# Time of day right after a day: « demain soir », « lundi matin », « tomorrow at noon »
_PERIOD = (r"(?: (?:in the |at |dans la |dans l'|en |au |le |la )?"
           r"(?P<period>apres midi|aprem|matinee|matin|soiree|soir|nuit|midi|morning|afternoon|evening|night|noon)\b)?")
_BEFORE_DAY = r"(?:(?:pour|for|d'ici|by|until|jusqu'a) )?"
# Qualifier after a time: « du soir », « de l'après-midi », « in the morning »
PART = (r"(?: (?P<part>du matin|du soir|de l'apres midi|de l'aprem|de la nuit|le matin|le soir|"
        r"in the morning|in the afternoon|in the evening|at night))?")
PARTS = {"du matin": "morning", "le matin": "morning", "in the morning": "morning",
         "de l'apres midi": "afternoon", "de l'aprem": "afternoon", "in the afternoon": "afternoon",
         "du soir": "evening", "le soir": "evening", "in the evening": "evening",
         "de la nuit": "night", "at night": "night"}

DAY_PATTERNS = [
    ("rel", re.compile(rf"\b{_BEFORE_DAY}(?P<rel>{_alt(RELATIVE_DAYS)})\b{_PERIOD}")),
    ("weekday", re.compile(rf"\b{_BEFORE_DAY}(?:(?:le|ce|on|this|next|coming) ){{0,2}}(?P<wd>{WEEKDAY_ALT})"
                           rf"(?: (?P<next>prochain|qui vient))?{_PERIOD}")),
    ("in_days", re.compile(rf"\b(?:dans|in) (?P<n>\d+|{WORDNUM}|an|a) (?P<u>jours?|semaines?|days?|weeks?)\b{_PERIOD}")),
]
_DATE_BEFORE = r"(?:(?:pour|for|d'ici|by|until|jusqu'au|jusqu'a|au|du|le|on|the) ){0,2}"
DATE_PATTERNS = [
    # « le 3 octobre », « 3 octobre 2027 », « on the 3rd of october »
    re.compile(rf"\b{_DATE_BEFORE}(?P<d>{DAYNUM}) (?:of )?(?P<mo>{MONTH}){YEAR}"),
    # « october 3 », « october 3rd 2027 », « on october the 3rd »
    re.compile(rf"\b(?:(?:for|by|on|until) )?(?P<mo>{MONTH}) (?:the )?(?P<d>{DAYNUM}){YEAR}"),
    # « le 3/10 », « le 3/10/2027 » (day then month)
    re.compile(r"\b(?:(?:pour|le|du|au|on|the) ){0,2}(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{4}|\d{2}))?\b"),
    # « le 15 », « on the 15th », « on the fifteenth »: day of the month alone (« le 31 février »: nothing)
    re.compile(rf"\b(?:(?:pour|jusqu'au|au|du) )?le (?P<d>\d{{1,2}}(?:er|e)?)(?![\d:/])(?! (?:of )?{MONTH})"
               r"(?! ?(?:h|heures?|minutes?|min|mn|secondes?|s|%|pour ?cent|ans|jours?|fois|euros?|mois|semaines?|"
               r"premieres?|premiers?|dernieres?|derniers?|videos?)\b)"),
    re.compile(rf"\b(?:(?:on|by|for|until) )?the (?P<d>\d{{1,2}}(?:st|nd|rd|th)(?![\d:/])|(?<=on the ){ORD_EN})"
               rf"(?! (?:of )?{MONTH})"),
    # « lundi 28 », « monday the 28th »: the weekday is read separately, only the number counts here
    re.compile(rf"\b{WEEKDAY_ALT} (?P<from>(?:the )?(?P<d>\d{{1,2}}(?:er|e|st|nd|rd|th)?{NOT_CLOCK}))(?! (?:of )?{MONTH})"),
]

CLOCK_PATTERNS = [
    # « 9 h », « 14h30 », « 14 h 30 », « neuf heures et demie », « 10 h moins le quart », « 8 h du soir »
    ("fr", re.compile(rf"\b(?P<h>{HOUR}) ?(?:heures?|h)(?![a-z'])(?: ?(?P<m>{MIN})(?: minutes?)?"
                      rf"| (?P<frac>et demie?|et quart|moins le quart|moins quart|moins (?P<minus>{MIN})(?: minutes?)?))?{PART}")),
    # « 9 am », « 2:30 pm », « 14:30 », « 9 o'clock », « at 9 », « at nine thirty »
    ("en", re.compile(rf"\b(?P<h>{HOUR})(?::(?P<m>\d\d)(?!\d)| (?P<mw>{MIN}))?"
                      rf"(?: ?(?P<ampm>[ap]) ?m\b| (?P<oc>o'?clock)\b)?{PART}")),
    # « half past nine », « quarter to ten »
    ("past", re.compile(rf"\b(?:a )?(?P<q>half|quarter) (?P<dir>past|after|to|till|before) (?P<h>{HOUR})"
                        rf"(?: ?(?P<ampm>[ap]) ?m\b| (?P<oc>o'?clock)\b)?{PART}")),
    # « midi », « minuit et quart », « midi et demi », « noon », « midnight »
    ("noon", re.compile(rf"(?<!apres )(?<!l'apres )\b(?P<w>midi|minuit|noon|midnight)\b"
                        rf"(?: (?P<frac>et demie?|et quart|moins le quart|moins quart|moins (?P<minus>{MIN}))| (?P<m>{MIN}))?")),
]
# Word right before a time: a preposition kept with it, or a hint that it is a duration
PREP = re.compile(r"(?:^|\s)(?P<p>a partir de|aux alentours de|autour de|jusqu'a|a|vers|pour|avant|at|around|about|by|"
                  r"for|before|until|till|from|de|entre|between|dans|pendant|durant|en|depuis|les|environ|in)\s$"
                  r"|(?P<d>d')$")
POSITIVE = {"a partir de", "aux alentours de", "autour de", "jusqu'a", "a", "vers", "pour", "avant", "at", "around",
            "about", "by", "for", "before", "until", "till", "from"}
RANGE = {"de", "from", "entre", "between"}
DURATION_BEFORE = {"de", "d'", "entre", "between", "dans", "pendant", "durant", "en", "depuis", "les", "environ", "in"}
DURATION_AFTER = re.compile(r"^ (?:avant|apres|plus tard|plus tot|de retard|d'avance|de plus|de moins|before|after|"
                            r"later|earlier|ago|early|late)\b")
UNITS_AFTER = re.compile(r"^ ?(?:minutes?|mins?|seconds?|secs?|hours?|hrs?|h|heures?|percent|%|times|days?|weeks?|"
                         r"months?|years?|people|persons?|euros?|dollars?|pounds?|x)\b")
RANGE_LINK = re.compile(r" (?:a|to|et|and|jusqu'a|until|till) ")

# Relative: « dans 20 minutes », « in an hour and a half », « d'ici un quart d'heure »
RELATIVE = re.compile(r"\b(?:dans|in|d'ici) (?:(?:environ|about|around|approximately|a peu pres|genre) )?")
DURATION_WORDS = set(NUMBER_WORDS) | {
    "et", "and", "demi", "demie", "quart", "quarts", "moins", "le", "a", "an", "half", "quarter", "quarters", "of",
    "d'heure", "d'heures", "heure", "heures", "h", "hour", "hours", "hr", "hrs", "minute", "minutes", "min", "mins",
    "mn", "seconde", "secondes", "sec", "secs", "s", "second", "seconds"}
DURATION_TOKEN = re.compile(r"\d+(?:\.\d+)?(?:h\d*|min|mn|s)?")


@dataclass
class _Part:
    kind: str      # "day", "date" or "clock"
    start: int     # positions in the folded text
    end: int
    info: dict


# --- Folded text -------------------------------------------------------------

def _normalize(text: str) -> tuple[str, list[int]]:
    """Lowercase, no accents, punctuation → space, single spaces; plus each character's original position."""
    out, pos = [], []
    for i, c in enumerate(text):
        if unicodedata.category(c) == "Mn":
            continue
        c = unicodedata.normalize("NFD", c.lower())[:1]
        digits = 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()
        if c in "’‘`´":
            c = "'"
        elif c == "," and digits:
            c = "."                       # 1,5 → 1.5
        elif c in ":/." and digits:
            pass                          # 14:30, 3/10, 1.5
        elif c != "'" and not c.isalnum():
            c = " "                       # dashes, commas, periods…
        if c == " " and (not out or out[-1] == " "):
            continue
        out.append(c)
        pos.append(i)
    if out and out[-1] == " ":
        out.pop()
        pos.pop()
    return "".join(out), pos


def _int(words: str) -> int | None:
    value = number(words.strip())
    return int(value) if value is not None and value == int(value) else None


def _day_number(word: str) -> int | None:
    """« 3 », « 1er », « 3rd », « premier », « third », « twenty first », « trois » → day of the month."""
    word = word.strip()
    m = re.fullmatch(r"(\d{1,2})(?:er|e|st|nd|rd|th)?", word)
    if m:
        return int(m[1])
    if word in ("premier", "first"):
        return 1
    parts = word.split()
    if parts[-1] in ORDINALS_EN:
        return ORDINALS_EN[parts[-1]] + (20 if parts[0] == "twenty" else 30 if parts[0] == "thirty" else 0)
    return _int(word)


# --- Recognized parts --------------------------------------------------------

def _relative(norm: str) -> tuple[int, int, int] | None:
    """« dans une heure et quart » → (start, end, seconds)."""
    for m in RELATIVE.finditer(norm):
        tokens = []
        for t in re.finditer(r"\S+", norm[m.end():]):
            if t.group() in DURATION_WORDS or DURATION_TOKEN.fullmatch(t.group()):
                tokens.append(t)
            else:
                break
        for k in range(len(tokens), 0, -1):  # the longest readable duration
            if tokens[k - 1].group() in ("et", "and", "le", "a", "of", "moins"):
                continue
            words = norm[m.end():m.end() + tokens[k - 1].end()]
            secs = timer_seconds(re.sub(r"(\d)h(\d)", r"\1 h \2", words))
            if secs:
                return m.start(), m.end() + tokens[k - 1].end(), secs
    return None


def _days(norm: str, today: date) -> list[_Part]:
    parts = []
    for kind, pattern in DAY_PATTERNS:
        for m in pattern.finditer(norm):
            period = PERIODS.get(m["period"]) if m["period"] else None
            if kind == "rel":
                offset, own = RELATIVE_DAYS[m["rel"]]
                period = own if own and not period else period
            elif kind == "weekday":
                offset = (WEEKDAYS[m["wd"]] - today.weekday()) % 7 or 7  # always strictly after today
            else:
                n = 1 if m["n"] in ("a", "an") else _int(m["n"])
                if not n:
                    continue
                offset = n * (7 if m["u"].startswith(("semaine", "week")) else 1)
            parts.append(_Part("day", m.start(), m.end(), {"offset": offset, "period": period}))
    return parts


def _dates(norm: str, today: date) -> list[_Part]:
    parts = []
    for pattern in DATE_PATTERNS:
        for m in pattern.finditer(norm):
            g = m.groupdict()
            day = _day_number(g["d"])
            month = MONTHS[g["mo"]] if g.get("mo") else int(g["m"]) if g.get("m") else None
            year = int(g["y"]) if g.get("y") else None
            if year is not None and year < 100:
                year += 2000
            found = _calendar_day(today, day, month, year)
            if found:
                start = m.start("from") if "from" in pattern.groupindex else m.start()
                parts.append(_Part("date", start, m.end(), {"date": found}))
    return parts


def _calendar_day(today: date, day: int | None, month: int | None, year: int | None) -> date | None:
    """Full date: next year (or next month) if it has already passed."""
    if not day or not 1 <= day <= 31:
        return None
    if month is None:  # « le 15 »: this month, or the next one if it has passed
        y, mo = today.year, today.month
        for _ in range(13):
            try:
                found = date(y, mo, day)
            except ValueError:
                found = None
            if found and found >= today:
                return found
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return None
    for y in ([year] if year else [today.year, today.year + 1]):
        try:
            found = date(y, month, day)
        except ValueError:
            continue
        if year or found >= today:
            return found
    return None


def _read_clock(kind: str, m: re.Match) -> dict | None:
    """Minutes since midnight (midnight = 1440: the end of the day) and what is known about the time said."""
    g = m.groupdict()
    info = {"ambiguous": False, "fixed": False, "words": False, "ampm": g.get("ampm"), "bare": False}
    if kind == "noon":
        minutes = 720 if g["w"] in ("midi", "noon") else 1440
        info["fixed"] = True
    else:
        h = _int(g["h"])
        if h is None or h > 24:
            return None
        info["words"] = not g["h"][0].isdigit()
        if kind == "past":
            q = 30 if g["q"] == "half" else 15
            minutes = h * 60 + (q if g["dir"] in ("past", "after") else -q)
        else:
            mins = _int(g.get("m") or g.get("mw") or "0")
            if mins is None or mins >= 60:
                return None
            minutes = h * 60 + mins
        if kind == "en" and not (g["ampm"] or g["oc"] or g["m"] or g["part"]):
            info["bare"] = True  # « at 9 »: accepted only after « at »
        if g.get("ampm"):
            if h > 12:
                return None
            minutes = minutes % 720 + (720 if g["ampm"] == "p" else 0)
            info["fixed"] = True
        elif g.get("part"):
            minutes = _shift(minutes, PARTS[g["part"]])
            info["fixed"] = True
        elif kind in ("en", "past") and 1 <= h <= 11:
            info["ambiguous"] = True  # English without am/pm: morning or evening, decided later
    if g.get("frac"):
        minutes += {"et demi": 30, "et demie": 30, "et quart": 15, "moins le quart": -15, "moins quart": -15}.get(g["frac"], 0)
    if g.get("minus"):
        minus = _int(g["minus"])
        if minus is None or minus >= 60:
            return None
        minutes -= minus
    if kind == "noon" and g.get("m"):
        extra = _int(g["m"])
        if extra is None or extra >= 60:
            return None
        minutes += extra
    info["minutes"] = minutes
    return info


def _shift(minutes: int, period: str | None) -> int:
    """« 8 h » in the evening → 20:00; « 3 h » in the afternoon → 15:00; « 2 h » at night stays 2:00."""
    if period in ("afternoon", "evening") and 60 <= minutes < 720:
        return minutes + 720
    if period == "night" and 360 <= minutes < 720:
        return minutes + 720
    return minutes


def _clocks(norm: str, text: str, pos: list[int]) -> list[_Part]:
    raw = []
    for kind, pattern in CLOCK_PATTERNS:
        for m in pattern.finditer(norm):
            info = _read_clock(kind, m)
            if info:
                info["kind"] = kind
                raw.append(_Part("clock", m.start(), m.end(), info))
    raw.sort(key=lambda p: (p.start, p.start - p.end))
    parts, used = [], set()
    for c in raw:
        if id(c) in used:
            continue
        before = PREP.search(norm[:c.start])
        prep = (before["p"] or before["d"]) if before else None
        prep_start = (before.start("p") if before["p"] else before.start("d")) if before else c.start
        if prep == "a" and c.info["kind"] not in ("fr", "noon") and text[pos[prep_start]] not in "àÀ":
            prep = None  # English « a » (article), not « à »
        # Range « de 14 h à 16 h », « from 2 to 4 pm »: keep the start
        if prep in RANGE:
            link = RANGE_LINK.match(norm, c.end)
            second = link and next((o for o in raw if o.start == link.end() and o is not c), None)
            if second:
                used.add(id(second))
                info = dict(c.info)
                if info["ambiguous"] and second.info["ampm"] == "p":
                    info["minutes"] = info["minutes"] % 720 + 720
                    info["ambiguous"] = False
                info["bare"] = False
                parts.append(_Part("clock", prep_start, second.end, info))
                continue
        if prep in DURATION_BEFORE and not c.info["ampm"]:
            continue  # « pendant 2 heures », « d'une heure », « toutes les 2 heures »
        if prep not in POSITIVE and DURATION_AFTER.match(norm, c.end):
            continue  # « une heure avant »
        if c.info["bare"] and (prep not in ("at", "around", "by") or UNITS_AFTER.match(norm[c.end:])):
            continue
        info = dict(c.info)
        info["prep"] = prep in POSITIVE
        parts.append(_Part("clock", prep_start if prep in POSITIVE else c.start, c.end, info))
    return parts


# --- Assembly ------------------------------------------------------------------

def _choose(parts: list[_Part], norm: str) -> list[_Part]:
    """At most one day, one date and one time, no overlap; leftmost first, then longest."""
    days = [p for p in parts if p.kind != "clock"]
    chosen = []
    for p in sorted(parts, key=lambda p: (p.start, p.start - p.end)):
        if any(p.start < o.end and o.start < p.end for o in chosen) or any(p.kind == o.kind for o in chosen):
            continue
        if p.kind == "clock" and p.info["words"] and not p.info["prep"]:
            # « neuf heures » without « à »: only right next to a day (« demain neuf heures »)
            if not any(re.fullmatch(r"\s*", norm[min(p.end, d.end):max(p.start, d.start)]) for d in days):
                continue
        chosen.append(p)
    return sorted(chosen, key=lambda p: p.start)


def _cut(text: str, pos: list[int], spans: list[tuple[int, int]]) -> tuple[str, str]:
    """Recognized text and rest of the original sentence, from the spans of the folded text."""
    merged = []
    for s, e in sorted((pos[s], pos[e - 1] + 1) for s, e in spans):
        if merged and re.fullmatch(r"[\s,]*", text[merged[-1][1]:s]):
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    said = " ".join(text[s:e] for s, e in merged)
    rest, last = [], 0
    for s, e in merged:
        rest.append(text[last:s])
        last = e
    rest.append(text[last:])
    rest = re.sub(r"\s+", " ", " ".join(rest))
    rest = re.sub(r"\s+,", ",", rest)
    rest = re.sub(r",\s*,", ",", rest)
    rest = re.sub(r"\s+\.$", ".", rest)
    return said, rest.strip(" ,;")


def extract(text: str, now: datetime, default_hour: int = 9) -> When | None:
    """Find the date or time said in the sentence; None if there is none."""
    text = unicodedata.normalize("NFC", text)
    norm, pos = _normalize(text)
    today = now.date()

    relative = _relative(norm)
    if relative:
        s, e, secs = relative
        said, rest = _cut(text, pos, [(s, e)])
        return When((now + timedelta(seconds=secs)).replace(microsecond=0), True, said, rest)

    chosen = _choose(_days(norm, today) + _dates(norm, today) + _clocks(norm, text, pos), norm)
    if not chosen:
        return None
    day = next((p.info for p in chosen if p.kind == "day"), None)
    found = next((p.info["date"] for p in chosen if p.kind == "date"), None)
    clock = next((p.info for p in chosen if p.kind == "clock"), None)
    period = day["period"] if day else None
    if found is None and day and day["offset"] is not None:
        found = today + timedelta(days=day["offset"])

    steps = (0, 1440)  # no day said: today, or tomorrow if the time has passed
    if clock:
        minutes, has_time = clock["minutes"], True
        if clock["ambiguous"]:
            # English without am/pm (« at 9 »): the time of day decides; with a day, 7-11 is morning
            # and 1-6 afternoon; without a day, the next time the clock shows that hour
            if period:
                minutes = _shift(minutes, period)
            elif found:
                minutes = minutes if minutes >= 7 * 60 else minutes + 720
            else:
                steps = (0, 720, 1440)
        elif not clock["fixed"]:
            minutes = _shift(minutes, period)
    elif period:
        minutes, has_time = PERIOD_DEFAULT[period], period == "noon"
    else:
        minutes, has_time = default_hour * 60, False

    at = datetime.combine(found or today, datetime.min.time()) + timedelta(minutes=minutes)
    if found is None:
        at = next((o for o in (at + timedelta(minutes=k) for k in steps) if o > now), at + timedelta(days=1))
    said, rest = _cut(text, pos, [(p.start, p.end) for p in chosen])
    return When(at, has_time, said, rest)


# --- Voice confirmation -------------------------------------------------------

def spoken(at: datetime, now: datetime, language: str) -> str:
    """French: « demain à 9 h », « lundi 28 septembre à 14 h 30 »; English: "tomorrow at 9 AM", "Monday, September 28 at 2:30 PM"."""
    fr = language == "fr"
    midnight = at.hour == 0 and at.minute == 0
    day = (at - timedelta(days=1)).date() if midnight else at.date()  # midnight: the end of the previous day
    delta = (day - now.date()).days
    if delta == 0:
        when = ("ce soir" if fr else "tonight") if midnight else ("aujourd'hui" if fr else "today")
    elif delta == 1:
        when = "demain" if fr else "tomorrow"
    elif delta == -1:
        when = "hier" if fr else "yesterday"
    elif fr:
        when = f"{DAYS_FR[day.weekday()]} {'1er' if day.day == 1 else day.day} {MONTHS_FR[day.month - 1]}"
        when += f" {day.year}" if day.year != now.year else ""
    else:
        when = f"{DAYS_EN[day.weekday()]}, {MONTHS_EN[day.month - 1]} {day.day}"
        when += f", {day.year}" if day.year != now.year else ""
    if fr:
        clock = "minuit" if midnight else "midi" if (at.hour, at.minute) == (12, 0) else \
            f"{at.hour} h" + (f" {at.minute:02d}" if at.minute else "")
        return f"{when} à {clock}"
    if midnight or (at.hour, at.minute) == (12, 0):
        clock = "midnight" if midnight else "noon"
    else:
        clock = f"{at.hour % 12 or 12}" + (f":{at.minute:02d}" if at.minute else "") + (" PM" if at.hour >= 12 else " AM")
    return f"{when} at {clock}"


# --- Tests ------------------------------------------------------------------------

def _describe(at: datetime | None, has_time: bool | None, rest: str | None) -> str:
    if at is None:
        return "nothing"
    text = f"{at:%Y-%m-%d %H:%M}"
    if has_time is not None:
        text += " (time given)" if has_time else " (no time)"
    if rest is not None:
        text += f", rest “{rest}”"
    return text


def run_tests(verbose: bool = False) -> tuple[int, int]:
    """Replay tests/dates.toml: each sentence must give the expected moment (and rest)."""
    data = tomllib.loads(TESTS.read_text(encoding="utf-8"))
    now, cases, ok = data["now"], data["case"], 0
    for case in cases:
        got = extract(case["text"], now)
        if case.get("none"):
            good = got is None
            expected = "nothing"
        else:
            good = got is not None and got.at == case["at"] \
                and case.get("has_time", got.has_time) == got.has_time and case.get("rest", got.rest) == got.rest
            expected = _describe(case["at"], case.get("has_time"), case.get("rest"))
        obtained = _describe(got.at, got.has_time, got.rest) if got else "nothing"
        ok += good
        if not good or verbose:
            print(f"  {'✓' if good else '✗'} “{case['text']}” → got {obtained}" + ("" if good else f", expected {expected}"))
    print(f"Dates: {ok}/{len(cases)} correct")
    return ok, len(cases)
