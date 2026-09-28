"""Level-0 router: fast rules for apps, the player and simple YouTube navigation.

route(text) returns a call to an allowlisted tool, or None: the sentence then goes
to level 1 (LLM). Rules apply to the "folded" text: lowercase, no accents
or punctuation, decimals with a dot. They cover French and English, and known
Parakeet mix-ups (« Mais » for « Mets », « Pose » for « Pause »).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from . import apps


@dataclass
class Call:
    tool: str
    args: dict = field(default_factory=dict)
    rule: str = ""
    level: int = 0
    source: str = ""  # user's sentence (to check that typed text really comes from them)


# --- Text -------------------------------------------------------------------

# Known transcription mix-ups, fixed before the rules run
HOMOPHONES = [
    # Parakeet transcription errors (French)
    (r"\bplays\b", "play"), (r"\bpauses\b", "pause"),
    (r"^(?:me|mes|met|mets|mais|mai|made)play$", "mets play"),  # « mesplay », « Méplay »
    (r"^(?:me|mes|met|mais|made|mille) (?=play\b|pause\b)", "mets "),  # « Mes pauses », « Mille pauses »
    (r"^(?:best|beste|bess|baise|baisses) (?=le son\b|le volume\b|encore\b|un peu\b)", "baisse "),  # « Best le son »
    (r"\bmontent\b", "monte"), (r"\bbaissent\b", "baisse"), (r"\bcoupent\b", "coupe"),
    (r"\bcoupleson\b|\bcouple son\b|\bcouples on\b", "coupe le son"),
    (r"\bminutes? heures?\b|\bminute or\b|\bmini tour\b|\bminuteurs\b|\bminute eur\b", "minuteur"),
    (r"^mais ", "mets "),
    (r"^mes (?=le |la |les |en |sur |un |une )", "mets "),
    (r"\ble sont\b", "le son"),
    (r"^manque le (?:son|sang)\b", "monte le son"), (r"\b(monte|baisse|coupe|remets|remet) le sang\b", r"\1 le son"),
    (r"^(me|may|ma|mai) youtube$", "mets youtube"),
    (r"^pose$", "pause"),
    (r"\b(en|sur) pose$", r"\1 pause"),  # « mets la vidéo en pose »
    (r"^recul ", "recule "),
    (r"^coute le son$", "coupe le son"),
    (r"\blemac\b", "le mac"),
]


# Polite or lead-in phrases, stripped from the start of the sentence
LEAD = (r"^(?:(?:euh|bon|alors|ok|okay|allez|vas y|hey|dis|so|um|uh|please) )*"
        r"(?:est ce que tu (?:peux|pourrais)|tu (?:peux|pourrais)|(?:peux|pourrais) tu|"
        r"(?:je veux|je voudrais|j'aimerais)(?: que tu)?|can you|could you|would you|"
        r"i want to|i'd like to|i would like to|let's|go ahead and) ")
# After « tu peux… » the verb is an infinitive: turn it back into the imperative the rules use
INFINITIVES = {
    "mettre": "mets", "remettre": "remets", "couper": "coupe", "baisser": "baisse", "monter": "monte",
    "augmenter": "augmente", "diminuer": "diminue", "avancer": "avance", "reculer": "recule", "ouvrir": "ouvre",
    "fermer": "ferme", "quitter": "quitte", "lancer": "lance", "passer": "passe", "aller": "va",
    "enlever": "enleve", "retirer": "retire", "activer": "active", "desactiver": "desactive",
    "reprendre": "reprends", "chercher": "cherche", "afficher": "affiche", "sortir": "sors",
    "recommencer": "recommence", "arreter": "arrete", "revenir": "reviens", "descendre": "descends",
    "remonter": "remonte", "montrer": "montre", "jouer": "joue", "ajouter": "ajoute",
}
# Words around the command that don't change the action
AROUND = [
    (r"^(?:mets|met|passe|mettez) (?:toi|vous|moi) ", "mets "),
    (r"^met ", "mets "),
    (r"^(?:appuie|appuyer|clique|cliquer|press|hit)(?: sur)?(?: le bouton)? (?=play|pause|lecture)", ""),
    # « la vidéo » means the current one, unless it is described (« la vidéo sur la Russie »)
    (r"\b(?:la|cette|the) video\b(?! (?:suivante|precedente|numero|number|\d|sur|de|du|des|d'|qui|about|of|on|from|with|by))", ""),
    (r"\b(?:ca|it)\b(?= en | a | sur |$)", ""),
    (r"\b(?:le |en )?mode (?=plein ecran)", "en "),
    (r"\bthe (?=full ?screen)", ""),
]


def plain(text: str) -> str:
    """Lowercase, no accents or punctuation, nothing removed (fold also removes « merci », « la vidéo »…)."""
    t = unicodedata.normalize("NFD", text.lower().replace("’", "'"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", t.replace("-", " "))).strip()


def fold(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower().replace("’", "'"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)             # 1,5 → 1.5
    t = re.sub(r"(\d)\s*%", r"\1 %", t)
    t = re.sub(r"[^\w\s'.:%]", " ", t.replace("-", " "))
    t = re.sub(r"(?<!\d)[.:]|[.:](?!\d)", " ", t)      # dots and colons outside numbers
    t = re.sub(r"\bd'(?=un\b|une\b|\d)", "de ", t)       # « d'une minute » → « de une minute »
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\b(s'il te plait|s'il vous plait|stp|please|merci)\b", "", t).strip()
    for pattern, repl in HOMOPHONES:
        t = re.sub(pattern, repl, t)
    t = re.sub(LEAD, "", t)
    first, _, rest = t.partition(" ")
    if first in INFINITIVES:
        t = f"{INFINITIVES[first]} {rest}".strip()
    for pattern, repl in AROUND:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"\s+", " ", t).strip()
    for pattern, repl in HOMOPHONES:  # a second time, after the lead-in phrases are removed
        t = re.sub(pattern, repl, t)
    return t


# --- Numbers, durations, ordinals --------------------------------------------

NUMBER_WORDS = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
    "seize": 16, "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
    "cent": 100, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}
_WORD = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
# A number: digits, or words (« vingt cinq », « un virgule cinq », « one point five », « et demi »)
NUM = rf"(?:\d+(?:\.\d+)?|(?:{_WORD})(?:(?: et| and)? (?:{_WORD}|virgule|point))*(?: et demie?| and a half)?)"


def _integer(words: list[str]) -> int:
    """« vingt cinq » → 25, « soixante dix » → 70, « quatre vingt dix » → 90, « deux cent » → 200."""
    n = 0
    for w in words:
        v = NUMBER_WORDS[w]
        if v == 100:
            n = (n or 1) * 100
        elif v == 20 and n % 100 == 4:  # quatre vingt
            n += 76
        else:
            n += v
    return n


def number(text: str) -> float | None:
    text = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    half = 0.5 if re.search(r"(et demie?|and a half)$", text) else 0
    text = re.sub(r" ?(et demie?|and a half)$", "", text)
    words = [w for w in text.split() if w not in ("et", "and")]
    if not words or any(w not in NUMBER_WORDS and w not in ("virgule", "point") for w in words):
        return None
    if "virgule" in words or "point" in words:
        i = words.index("virgule" if "virgule" in words else "point")
        whole, decimals = words[:i], words[i + 1:]
        digits = "".join(str(_integer([d])) for d in decimals) if all(NUMBER_WORDS[d] < 10 for d in decimals) else str(_integer(decimals))
        return float(f"{_integer(whole) if whole else 0}.{digits or 0}")
    return _integer(words) + half


UNIT_S = r"(?:secondes?|sec|s|seconds?|secs?)"
UNIT_M = r"(?:minutes?|min|mn|mins?)"
UNIT_H = r"(?:heures?|h|hours?)"
# Duration: « 30 secondes », « 2 minutes », « 1 minute 30 », « une minute et demie », « 1 h 5 », « 90 » (seconds by default)
HALF = r" et demie?| and a half"
DURATION = (rf"(?:(?P<h>{NUM}) {UNIT_H}(?P<hh>{HALF})?(?: (?P<hm>{NUM})(?: {UNIT_M})?)?"
            rf"|(?P<m>{NUM}) {UNIT_M}(?P<mh>{HALF})?(?: (?P<ms>{NUM})(?: {UNIT_S})?)?|(?P<s>{NUM})(?: {UNIT_S})?)")


def seconds(m: re.Match, bare_unit: str = "s") -> float | None:
    """Duration in seconds from the DURATION groups; a bare number counts as seconds or minutes."""
    g = m.groupdict()
    if g.get("h"):
        return number(g["h"]) * 3600 + (1800 if g.get("hh") else 0) + (number(g["hm"]) * 60 if g.get("hm") else 0)
    if g.get("m"):
        return number(g["m"]) * 60 + (30 if g.get("mh") else 0) + (number(g["ms"]) if g.get("ms") else 0)
    if g.get("s"):
        n = number(g["s"])
        return None if n is None else n * (60 if bare_unit == "m" else 1)
    return None


ORDINALS = {
    "premiere": 1, "premier": 1, "1ere": 1, "1er": 1, "1re": 1, "first": 1, "1st": 1,
    "deuxieme": 2, "seconde": 2, "second": 2, "2eme": 2, "2e": 2, "2nd": 2, "2nde": 2,
    "troisieme": 3, "3eme": 3, "3e": 3, "third": 3, "3rd": 3,
    "quatrieme": 4, "4eme": 4, "4e": 4, "fourth": 4, "4th": 4,
    "cinquieme": 5, "5eme": 5, "5e": 5, "fifth": 5, "5th": 5,
    "sixieme": 6, "6eme": 6, "6e": 6, "sixth": 6, "6th": 6,
    "septieme": 7, "7eme": 7, "7e": 7, "seventh": 7, "7th": 7,
    "huitieme": 8, "8eme": 8, "8e": 8, "eighth": 8, "8th": 8,
    "neuvieme": 9, "9eme": 9, "9e": 9, "ninth": 9, "9th": 9,
    "dixieme": 10, "10eme": 10, "10e": 10, "tenth": 10, "10th": 10,
    "onzieme": 11, "eleventh": 11, "douzieme": 12, "twelfth": 12, "treizieme": 13, "thirteenth": 13,
    "quatorzieme": 14, "fourteenth": 14, "quinzieme": 15, "fifteenth": 15, "seizieme": 16, "sixteenth": 16,
    "dix septieme": 17, "seventeenth": 17, "dix huitieme": 18, "eighteenth": 18, "dix neuvieme": 19,
    "nineteenth": 19, "vingtieme": 20, "twentieth": 20,
}
ORD = "|".join(sorted(ORDINALS, key=len, reverse=True))
# Ordinal in digits: « 12e », « 11eme », « 21st »
ORD_DIGITS = r"\d{1,2}(?:e|eme|er|ere|re|st|nd|rd|th)"


def clock_words(text: str) -> int | None:
    """Position said like a clock time: « douze trente », « twelve thirty », « 12 30 » → 750 s (12:30).

    Only if the two numbers don't form a single number (« quatre vingt dix », « trente cinq »).
    """
    words = text.split()
    if len(words) == 2 and words[0].isdigit() and re.fullmatch(r"\d\d", words[1]):
        return int(words[0]) * 60 + int(words[1]) if int(words[1]) < 60 else None
    tens = {"dix", "onze", "douze", "treize", "quatorze", "quinze", "seize", "vingt", "trente", "quarante", "cinquante",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
            "twenty", "thirty", "forty", "fifty"}
    for i in range(1, len(words)):
        left, right = words[:i], words[i:]
        if right[0] not in tens or left[-1] in ("quatre", "soixante", "cent", "cents", "hundred", "et", "and"):
            continue  # « quatre vingt », « soixante dix », « cent vingt »: a single number
        if left[-1] in ("vingt", "vingts") and right[0] not in ("trente", "quarante", "cinquante"):
            continue  # « quatre vingt dix »
        a, b = number(" ".join(left)), number(" ".join(right))
        if a is not None and b is not None and a == int(a) < 100 and b == int(b) and 10 <= b < 60:
            return int(a) * 60 + int(b)
    return None


LANGUAGES = {"francais": "fr", "french": "fr", "anglais": "en", "english": "en", "espagnol": "es", "spanish": "es",
             "allemand": "de", "german": "de", "italien": "it", "italian": "it", "portugais": "pt",
             "portuguese": "pt", "arabe": "ar", "arabic": "ar", "chinois": "zh", "chinese": "zh",
             "japonais": "ja", "japanese": "ja", "russe": "ru", "russian": "ru"}
LANG = "|".join(LANGUAGES)

# Search filters the rules alone can't translate: left to the LLM
SEARCH_FILTER_WORDS = (r"\b(semaine|aujourd'hui|ce mois|cette annee|recent\w*|dernier\w*|nouvea\w*|par date|moins de|plus de|"
                       r"minutes|heures?|week|today|month|year|latest|newest|shorter|longer|under|over)\b")


# --- Rules ---------------------------------------------------------------------

UNIT_SECONDS = {"h": 3600, "heure": 3600, "heures": 3600, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
                "min": 60, "mn": 60, "mins": 60, "minute": 60, "minutes": 60,
                "s": 1, "sec": 1, "secs": 1, "seconde": 1, "secondes": 1, "second": 1, "seconds": 1}
_UNIT = r"heures?|hours?|hrs?|h|minutes?|mins?|mn|min|secondes?|seconds?|secs?|sec|s"
FRACTIONS = {"une demi": 0.5, "demi": 0.5, "une demie": 0.5, "un quart d'": 0.25, "un quart de": 0.25,
             "un quart d": 0.25, "trois quarts d'": 0.75, "trois quarts de": 0.75, "half an": 0.5, "half a": 0.5,
             "a quarter of an": 0.25, "an": 1, "a": 1}
TIMER_DURATION = re.compile(
    rf"\b(?P<n>{NUM}|une demie?|demi|un quart d'?|un quart de|trois quarts d'?|trois quarts de|half an?|a quarter of an|an|a)"
    rf"\s*(?P<u>{_UNIT})\b(?P<frac> et demie?| and a half| et quart| and a quarter| et trois quarts| and three quarters"
    rf"| moins le quart| moins quart)?(?:\s*(?:et )?(?P<rest>\d+|{_WORD}(?: {_WORD})?)\b(?!\s*(?:{_UNIT})\b))?")
# « une heure et quart »: fraction of the unit added (or subtracted for « moins le quart »)
FRACTION_AFTER = {"et demi": 0.5, "et demie": 0.5, "and a half": 0.5, "et quart": 0.25, "and a quarter": 0.25,
                  "et trois quarts": 0.75, "and three quarters": 0.75, "moins le quart": -0.25, "moins quart": -0.25}


def timer_seconds(text: str) -> int | None:
    """« 10 minutes », « une heure et demie », « 1 h 30 », « 2 min 30 », « un quart d'heure » → seconds (folded text)."""
    total, found = 0.0, False
    text = re.sub(r"(\d+)(h|min|mn|s)\b", r"\1 \2", text).replace("-", " ")
    for m in TIMER_DURATION.finditer(text):
        unit = UNIT_SECONDS[m["u"]]
        n = FRACTIONS.get(m["n"].strip())
        n = number(m["n"]) if n is None else n
        if n is None:
            continue
        found = True
        total += n * unit + (FRACTION_AFTER[m["frac"].strip()] * unit if m["frac"] else 0)
        if m["rest"] and unit > 1:  # « 1 h 30 », « 2 minutes 30 »
            rest = number(m["rest"])
            total += (rest or 0) * (60 if unit == 3600 else 1)
    return int(round(total)) if found and total > 0 else None


RULES: list[tuple[str, str, object]] = []


def rule(name: str, pattern: str):
    """Declares a rule: pattern over the whole folded text, function that builds the call."""
    def register(build):
        RULES.append((name, re.compile(rf"^(?:{pattern})$"), build))
        return build
    return register


def player(action, value=None):
    return Call("player", {"action": action} if value is None else {"action": action, "value": value})


# Account (like, watch later, subscription with confirmation)
rule("unlike", r"(?:enleve|retire|supprime|annule) (?:le|mon|ton) like|unlike(?: (?:it|this|the video))?|remove (?:the|my) like")(
    lambda m: Call("youtube_account", {"action": "unlike"}))
rule("like", r"(?:mets? |met |mettre |ajoute )?(?:un |le )?like(?: (?:la|cette|this|the) video| it| this)?|j'aime(?: (?:la|cette) video)?|i like (?:it|this)(?: video)?|like it")(
    lambda m: Call("youtube_account", {"action": "like"}))
# Subscription: fixed rule (the model has already mixed up « désabonne-toi » and « abonne-toi »); voice
# confirmation is still required (safety.SENSITIVE)
rule("unsubscribe", r"(?:desabonne|desabonner|desabonnez)(?: toi| moi| nous)?(?: (?:de|de la|a) (?:cette |la )?chaine)?|unsubscribe(?: (?:from )?(?:this|the) channel)?|je me desabonne")(
    lambda m: Call("youtube_account", {"action": "unsubscribe"}))
rule("subscribe", r"(?:abonne|abonner|abonnez)(?: toi| moi| nous)(?: (?:a|de) (?:cette |la )?chaine)?|subscribe(?: to (?:this|the) channel)?|je m'abonne")(
    lambda m: Call("youtube_account", {"action": "subscribe"}))
rule("watch_later_add", r"(?:ajoute|mets|garde|enregistre|save|add)(?: la| ca| cette video| it| this| this video)? (?:a|dans|en|to|for) (?:a regarder plus tard|watch later)")(
    lambda m: Call("youtube_account", {"action": "watch_later"}))

# Out of scope, refused at once: deleting history or files, shutting down or restarting
def _refuse(m):
    from .i18n import t

    return Call("speak", {"text": t("out_of_scope")})


rule("out_of_scope", r"(?:supprime|efface|vide|delete|clear|erase|remove)(?: tout)?(?: mon| l'| le| la| les| my| the)? ?(?:historique|history|fichiers?|files?|dossiers?|folders?).*"
     r"|(?:eteins|redemarre|arrete)(?: le| l')? ?(?:mac|ordi|ordinateur|macbook)|(?:shut ?down|restart|reboot|turn off)(?: the)? (?:mac|computer)")(_refuse)

# Mac: sound, display, music, Shortcuts
MAC = r"(?:du |de l'|de l |le |l'|l |de mon |de ce |mon |ce |my |the )?(?:mac|ordi|ordinateur|macbook|computer|system)"
# YouTube sound: only when « YouTube » is said (otherwise all sound goes through the Mac)
YT = r"(?:de |sur |du lecteur |dans |du )?(?:la video |le lecteur )?youtube"
# Verbs that mute or unmute the sound (« ferme le son », « éteins le son », « allume le son »)
SOUND_OFF = r"(?:coupe|enleve|desactive|ferme|eteins|arrete)"
SOUND_ON = r"(?:remets|reactive|rallume|allume|rends moi)"
rule("yt_mute", rf"{SOUND_OFF}(?: le)? son {YT}|mute (?:the )?youtube")(lambda m: player("mute"))
rule("yt_unmute", rf"{SOUND_ON}(?: le)? son {YT}|unmute (?:the )?youtube")(lambda m: player("unmute"))
rule("yt_volume_set", rf"(?:(?:mets|regle|baisse|diminue|descends|monte|augmente|remonte) )?(?:le )?(?:son|volume) {YT} (?:a |au |sur |to |at |jusqu'a )?(?P<n>{NUM})(?: ?%| pour ?cent| percent)?"
     rf"|(?:set )?(?:the )?youtube volume (?:to |at )?(?P<n2>{NUM})(?: ?%| percent)?")(
    lambda m: player("volume", number(m["n"] or m["n2"])))
# « Coupe le son de la vidéo », « mets le son de la vidéo à 30 »: fold removed « la vidéo », leaving « le son de »
rule("video_mute", rf"{SOUND_OFF}(?: le)? son (?:de|du lecteur)")(lambda m: player("mute"))
rule("video_unmute", rf"{SOUND_ON}(?: le)? son (?:de|du lecteur)")(lambda m: player("unmute"))
rule("video_volume_set", rf"(?:(?:mets|regle|baisse|diminue|descends|monte|augmente|remonte) )?(?:le )?(?:son|volume) (?:de|du lecteur) (?:a|au|sur|jusqu'a) (?P<n>{NUM})(?: ?%| pour ?cent)?")(
    lambda m: player("volume", number(m["n"])))
rule("yt_volume_up", rf"(?:monte|augmente|remonte)(?: un peu)?(?: le)? (?:son|volume)(?: un peu)? {YT}|(?:turn up|raise) (?:the )?youtube(?: volume)?|youtube volume up")(
    lambda m: player("volume_up"))
rule("yt_volume_down", rf"(?:baisse|diminue)(?: un peu)?(?: le)? (?:son|volume)(?: un peu)? {YT}|(?:turn down|lower) (?:the )?youtube(?: volume)?|youtube volume down")(
    lambda m: player("volume_down"))
rule("mac_mute", rf"{SOUND_OFF}(?: le)? son {MAC}|mute (?:the )?{MAC}")(lambda m: Call("system_volume", {"action": "mute"}))
rule("mac_unmute", rf"{SOUND_ON}(?: le)? son {MAC}|unmute (?:the )?{MAC}")(lambda m: Call("system_volume", {"action": "unmute"}))
rule("mac_volume_set", rf"(?:mets |regle )?(?:le )?(?:son|volume) {MAC} (?:a |sur |to |at )?(?P<n>{NUM})(?: ?%| pour ?cent| percent)?|(?:set )?(?:the )?{MAC} volume (?:to |at )?(?P<n2>{NUM})(?: ?%| percent)?")(
    lambda m: Call("system_volume", {"action": "set", "value": number(m["n"] or m["n2"])}))
# « Volume max », « mets le son à fond »
rule("volume_max", rf"(?:mets |monte |regle )?(?:le )?(?:son|volume)(?: {MAC})? (?:a fond|au max(?:imum)?|max(?:imum)?|a (?:100|cent) ?%?)"
     r"|(?:set |turn )?(?:the )?volume (?:to |all the way )?(?:max(?:imum)?|up to max)|max(?:imum)? volume|full volume")(
    lambda m: Call("system_volume", {"action": "set", "value": 100}))
rule("mac_volume_up", rf"(?:monte|augmente)(?: un peu)?(?: le)? (?:son|volume)(?: un peu)? {MAC}|(?:turn up the |raise the )?{MAC} volume(?: up)?")(lambda m: Call("system_volume", {"action": "up"}))
rule("mac_volume_down", rf"(?:baisse|diminue)(?: un peu)?(?: le)? (?:son|volume)(?: un peu)? {MAC}|(?:turn down the |lower the )?{MAC} volume down")(lambda m: Call("system_volume", {"action": "down"}))
rule("screen_sleep", rf"(?:mets |met )?(?:l'ecran|l ecran|ecran|{MAC}) en veille|eteins l'ecran|(?:put the )?(?:screen|display) (?:to sleep|off)|sleep (?:the )?(?:screen|display)"
     rf"|put {MAC} to sleep")(lambda m: Call("screen", {"action": "sleep"}))
rule("screen_lock", rf"verrouille(?: {MAC}| l'ecran| l ecran| la session)?|lock(?: the| my)? (?:mac|macbook|screen|computer)|lock")(lambda m: Call("screen", {"action": "lock"}))
# Timers and local info
def _timer(m):
    seconds = timer_seconds(m["d"])
    return Call("timer", {"action": "start", "seconds": seconds}) if seconds else None


rule("timer_start", r"(?:mets |met |lance |demarre |fais |programme |start |set )?(?:moi )?(?:un |une |a )?(?:minuteur|timer|minuterie|compte a rebours|countdown)"
     r"(?: de| pour| sur| of| for| dans)? (?P<d>.+)|(?P<d2>.+?) de minuteur")(
    lambda m: _timer({"d": m["d"] or m["d2"]}))
rule("timer_cancel", r"(?:annule|arrete|stoppe|supprime|enleve|coupe|cancel|stop|delete|remove)(?: le| les| mon| mes| the| my| all)? (?:minuteurs?|timers?|rappels?|reminders?)")(
    lambda m: Call("timer", {"action": "cancel"}))
rule("timer_status", r"(?:il reste combien(?: de temps)?|combien de temps(?: il)? reste(?: t il)?|ou en est|temps restant)(?: sur| au| du| pour| de)? (?:le |mon )?(?:minuteur|timer|rappel)"
     r"|(?:how much time (?:is )?left|time left|what(?:'s| is) left) on (?:the |my )?timer|timer status|(?:le |mon )?(?:minuteur|timer)")(
    lambda m: Call("timer", {"action": "status"}))
rule("info_time", r"(?:quelle heure (?:est il|il est|il est la)|il est quelle heure|c'est quelle heure|(?:(?:donne|dis) moi |me (?:dire|donner) )?l'heure|heure"
     r"|what time is(?: it)?|what's the time|what is the time|(?:tell me |give me )?the time|time)")(
    lambda m: Call("local_info", {"what": "time"}))
rule("info_date", r"(?:on est quel jour|quel jour (?:on est|sommes nous|est on|est il|on est aujourd'hui)|quelle est la date(?: d'aujourd'hui| aujourd'hui)?|quelle date (?:on est|sommes nous|est on)|c'est quoi la date|la date(?: d'aujourd'hui)?"
     r"|on est le combien|what day is it(?: today)?|what's the date(?: today)?|what is the date(?: today)?|what's today's date|today's date|date)")(
    lambda m: Call("local_info", {"what": "date"}))
rule("info_battery", r"(?:(?:il me reste |il reste )?combien de batterie(?: il reste| il me reste)?|(?:niveau|etat|pourcentage) (?:de la |de |du )?batterie|(?:la |ma )?batterie|j'ai combien de batterie"
     r"|how much battery(?: is left| do i have| left)?|battery(?: level| status| left)?|what's (?:my|the) battery(?: level)?)")(
    lambda m: Call("local_info", {"what": "battery"}))

def _summary(m):
    at = m.groupdict().get("at")
    if not at:
        return Call("youtube_summary", {})
    clock_match = re.fullmatch(r"(\d+):(\d\d)", at.strip())
    seconds = int(clock_match[1]) * 60 + int(clock_match[2]) if clock_match else timer_seconds(at)
    return Call("youtube_summary", {"at": seconds}) if seconds else None


rule("summary_at", r"(?:de quoi (?:ca|il|elle|ils|on) parle|(?:resume|raconte)(?: moi)? (?:le passage|la partie|ce qui se dit|ce qu'il dit)|what (?:are they|is he|is she|do they) (?:talking|saying) about|summarize (?:the part|what they say))"
     r" (?:a|vers|autour de|aux|at|around) (?:la minute )?(?P<at>\d+:\d\d|.+)")(_summary)
rule("summary", r"(?:resumer|fais(?: moi)? un resume(?: de)?|donne(?: moi)? un resume(?: de)?|c'est quoi le resume(?: de)?)(?: moi)?(?: cette| la| ce| le)?(?: video| contenu)?"
     r"|de quoi (?:ca|elle|il) parle(?: cette video| la video)?|de quoi parle(?: (?:cette|la) video)?|(?:summarize|summarise|sum up)(?: this| the)?(?: video)?"
     r"|(?:give me a |a )?summary(?: of (?:this|the) video)?|what(?:'s| is) (?:this|the) video about|tl ?dr")(_summary)
rule("dictation_start", r"(?:mode |lance la |lance le mode |active la |active le mode |commence la |commence a |demarre la )?(?:dictee|dicter|dicte)"
     r"|(?:start |begin )?dictation(?: mode)?|dictate|(?:modo )?dictado|diktat(?:modus)?|dettatura|ditado")(
    lambda m: Call("dictation", {"action": "start"}))
rule("dictation_stop", r"(?:fin de (?:la )?dictee|stop(?:pe)? (?:la )?dictee|arrete la dictee|termine la dictee|end (?:of )?dictation|stop dictation)")(
    lambda m: Call("dictation", {"action": "stop"}))
rule("reminder_list", r"(?:(?:quels sont|montre moi|lis moi|donne moi|dis moi) )?(?:mes|les) rappels(?: en cours)?|qu'est ce que j'ai comme rappels"
     r"|j'ai quoi comme rappels|what are my reminders|(?:list |show |read )?my reminders|what reminders do i have")(
    lambda m: Call("reminder_list", {}))
rule("music_info", r"(?:c'est quoi|c est quoi|quelle est|quel est|c'est quelle|c'est qui)(?: le nom de)? (?:cette|ce|la|le) (?:chanson|musique|morceau|titre|son)(?: qui (?:passe|joue))?"
     r"|(?:qui chante|qui c'est qui chante)(?: ca| cette chanson)?|what song is (?:this|playing)|what's (?:this song|playing)|what is playing|who sings (?:this|that)|what(?:'s| is) this song")(
    lambda m: Call("music_info", {}))
rule("music_next", r"(?:musique|chanson|morceau|titre|son) suivante?|(?:passe a la |mets la )?(?:chanson|musique) suivante|next (?:song|track)|skip (?:the |this )?(?:song|track)")(lambda m: Call("media", {"action": "next"}))
rule("music_previous", r"(?:musique|chanson|morceau|titre) precedente?|previous (?:song|track)")(lambda m: Call("media", {"action": "previous"}))
rule("music_pause", r"(?:mets |met )?(?:la )?musique en pause|pause (?:la |the )?(?:musique|music)"
     r"|(?:ferme|eteins) la musique|(?:coupe|arrete|stoppe|stop|turn off)(?: la| de la| the)? (?:musique|music)")(
    lambda m: Call("media", {"action": "pause"}))  # a real pause: if nothing is playing, nothing restarts
rule("music_toggle", r"(?:relance|remets|reprends|joue|lance|mets|met) (?:la |de la |un peu de )?musique|(?:play|resume) (?:the )?music")(
    lambda m: Call("media", {"action": "play_pause"}))
rule("app_new", r"(?:fais|cree|ouvre|mets|nouvelle|nouveau|new)(?: moi)?(?: une| un)?(?: nouvelle| nouveau)? (?:note|document|doc|fichier texte|fenetre|page)|(?:create |open )?(?:a )?new (?:note|document|window)")(
    lambda m: Call("app_shortcut", {"action": "new"}))
rule("app_new_tab", r"(?:ouvre |fais |mets )?(?:un )?nouvel onglet|(?:open )?(?:a )?new tab")(lambda m: Call("app_shortcut", {"action": "new_tab"}))
rule("app_find", r"(?:cherche|recherche|trouve) (?:dans|sur) (?:la|cette) page|(?:find|search)(?: on| in)? (?:the |this )?page")(
    lambda m: Call("app_shortcut", {"action": "find"}))
rule("app_close_tab", r"ferme(?: l'| cet | l )?onglet|close (?:the |this )?tab")(lambda m: Call("app_shortcut", {"action": "close_tab"}))
rule("shortcut", r"(?:lance|execute|demarre|run|start)(?: le| mon| the| my)? (?:raccourci|shortcut) (?P<name>.+)")(lambda m: Call("run_shortcut", {"name": m["name"]}))

# Ads
rule("skip_ad", r"(?:passe|saute|ignore|zappe|skip|zap)(?: la| les| cette| the| this)? (?:pub|pubs|publicite|ad|ads)|skip")(
    lambda m: player("skip_ad"))

# Chapters
rule("chapter_next", r"(?:passe au |va au |aller au )?chapitre suivant|(?:go to the |skip to the )?next chapter")(lambda m: player("chapter_next"))
rule("chapter_previous", r"(?:reviens au |va au |retourne au )?chapitre precedent|(?:go to the |go back to the )?previous chapter")(lambda m: player("chapter_previous"))
rule("chapter_name", r"(?:va|aller|passe|saute|go|jump|skip)(?: directement)? (?:au|a la|to(?: the)?) (?:chapitre|partie|chapter|section|part) (?:sur |qui parle de |about |on |called )?(?P<name>.+)")(
    lambda m: player("chapter", m["name"]))

# Display
rule("exit_fullscreen", r"(?:quitte|sors du|sors de|enleve le|desactive le|ferme|eteins|coupe|exit|leave|close|get out of|turn off)(?: le| the)? (?:plein ecran|full ?screen)|(?:full ?screen|plein ecran) off")(lambda m: player("exit_fullscreen"))
rule("fullscreen", r"(?:mets |mettre |passe |active )?(?:le |en |la video en )?plein ecran|(?:go |make it |put it )?full ?screen(?: mode| on)?")(lambda m: player("fullscreen"))
rule("theater_off", r"(?:quitte|sors du|enleve le|desactive le|ferme|eteins|coupe|exit|leave|close|turn off|get out of)(?: le| the)? (?:mode cinema|theat(?:er|re) mode)")(lambda m: player("theater", False))
rule("theater", r"(?:mets |mettre |passe |active )?(?:le |en )?mode cinema|(?:turn on )?theat(?:er|re) mode")(lambda m: player("theater", True))
rule("miniplayer", r"(?:mets |passe |active )?(?:le |en )?mini ?lecteur|mini ?player")(lambda m: player("miniplayer"))
rule("pip", r"(?:mets |passe |active )?(?:en )?image dans l'? ?image|picture in picture|pip")(lambda m: player("pip"))

# Subtitles
rule("captions_off", r"(?:enleve|retire|coupe|desactive|cache|vire|arrete|supprime|ferme|eteins)(?: les)? sous titres?|(?:turn off|disable|remove|hide|close)(?: the)? (?:subtitles|captions)|(?:subtitles|captions|sous titres) off")(
    lambda m: player("captions", False))
rule("captions_language", rf"(?:mets |active |affiche )?(?:les )?sous titres?(?: en| in)? (?P<lang>{LANG})|(?:turn on |show |put )?(?:the )?(?:subtitles|captions) in (?P<lang2>{LANG})"
     rf"|(?:turn on |show |put on |enable )?(?:the )?(?P<lang3>{LANG}) (?:subtitles|captions)")(
    lambda m: player("captions_language", LANGUAGES[m["lang"] or m["lang2"] or m["lang3"]]))
rule("captions_on", r"(?:(?:mets|active|affiche|remets)(?: les)? |les )?sous titres?|(?:turn on|enable|show)(?: the)? (?:subtitles|captions)|(?:subtitles|captions)(?: on)?")(
    lambda m: player("captions", True))

# Quality
rule("quality_auto", r"(?:mets |passe )?(?:la )?qualite (?:en |a )?auto(?:matique)?|(?:set )?(?:the )?quality (?:to )?auto(?:matic)?|auto quality")(lambda m: player("quality", "auto"))
rule("quality", r"(?:mets |passe |regle |set |change )?(?:la video |la qualite |(?:the )?quality )?(?:en |a |to |in )?(?P<q>4k|2160p?|1440p?|1080p?|720p?|480p?|360p?)(?: quality)?")(
    lambda m: player("quality", m["q"] if m["q"] == "4k" or m["q"].endswith("p") else m["q"] + "p"))

# Autoplay, loop
rule("autoplay_off", r"(?:desactive|coupe|enleve|arrete|stoppe)(?: la)? lecture auto(?:matique)?|(?:turn off|disable|stop) autoplay|autoplay off")(lambda m: player("autoplay", False))
rule("autoplay_on", r"(?:active|remets|rallume)(?: la)? lecture auto(?:matique)?|(?:turn on|enable) autoplay|autoplay on")(lambda m: player("autoplay", True))
rule("loop_off", r"(?:enleve|arrete|desactive|coupe|stoppe)(?: la| le)? (?:boucle|mode boucle)|stop (?:looping|repeating)|(?:turn off|disable) (?:loop|repeat)|loop off")(lambda m: player("loop", False))
rule("loop_on", r"(?:mets |lis |joue |passe )?(?:la video |la |le |le titre |la playlist |la chanson |le morceau |ce titre |cette playlist )?(?:en boucle|mode boucle)"
     r"|loop(?: (?:it|this|the video|this video))?|repeat(?: (?:it|this|the video|this video))?|loop on")(lambda m: player("loop", True))

# Sound
rule("unmute", rf"{SOUND_ON}(?: le)? son|unmute|sound on|turn (?:on the sound|the sound on)")(lambda m: Call("system_volume", {"action": "unmute"}))
rule("mute", rf"{SOUND_OFF}(?: le)? son|mute|silence|chut|sound off|turn (?:off the sound|the sound off)")(lambda m: Call("system_volume", {"action": "mute"}))
rule("volume_set", rf"(?:mets |regle )?(?:le )?(?:volume|son) (?:a |au |sur |to |at )?(?P<n>{NUM})(?: ?%| pour ?cent| percent)?|(?:(?:set|turn|put|bring)(?: the)? )?volume(?: up| down)? (?:to |at )?(?P<n2>{NUM})(?: ?%| percent)?")(
    lambda m: Call("system_volume", {"action": "set", "value": number(m["n"] or m["n2"])}))
# « Baisse le son à 30 », « monte le volume du Mac à 80 % », « lower the volume to 30 »: a value, not a step
rule("volume_to", rf"(?!.*youtube)(?:baisse|diminue|descends|monte|augmente|remonte|mets|regle|lower|raise|turn(?: it)?(?: down| up)?|bring(?: it)?(?: down| up)?)"
     rf"(?: le| the)?(?: (?:son|volume|sound))?(?: {MAC})?(?: down| up)? (?:a|au|jusqu'a|to|at) (?P<n>{NUM})(?: ?%| pour ?cent| percent)?")(
    lambda m: Call("system_volume", {"action": "set", "value": number(m["n"])}))
rule("volume_up", r"(?:monte|augmente|remonte)(?: encore)?(?: le| un peu le)? (?:son|volume)(?: encore)?(?: un peu(?: plus)?)?"
                  r"|monte(?: encore)?(?: un peu(?: plus)?)?|(?:encore )?plus fort|(?:turn (?:it|the volume) up|volume up|louder|turn up the volume)")(lambda m: Call("system_volume", {"action": "up"}))
rule("volume_down", r"(?:baisse|diminue)(?: encore)?(?: le| un peu le)? (?:son|volume)(?: encore)?(?: un peu(?: plus)?)?"
                    r"|(?:re)?baisse(?: encore)?(?: un peu(?: plus)?)?|(?:encore )?moins fort|(?:turn (?:it|the volume) down|volume down|quieter|turn down the volume)")(lambda m: Call("system_volume", {"action": "down"}))

# Speed
rule("speed_normal", r"(?:remets |mets )?(?:la )?vitesse normale|(?:normal|regular) speed|(?:en |a )?vitesse (?:x ?)?1")(lambda m: player("speed", 1))
rule("faster", r"(?:va |lis )?plus vite|accelere|speed (?:it )?up|faster")(lambda m: player("faster"))
rule("slower", r"(?:va |lis )?moins vite|(?:plus )?lentement|ralentis|slow (?:it )?down|slower")(lambda m: player("slower"))
rule("speed_double", r"(?:mets |passe |lis |play )?(?:la video )?(?:en |a |at )?(?:vitesse double|double vitesse|double speed)")(lambda m: player("speed", 2))
rule("speed_half", r"(?:mets |passe |lis |play )?(?:la video )?(?:en |a |at )?(?:demi vitesse|vitesse divisee par deux|half speed)")(lambda m: player("speed", 0.5))
rule("speed", rf"(?:mets |passe |lis )?(?:la video )?(?:en |a |sur )?(?:la )?(?:vitesse )?(?:en |a |sur )?(?:x ?|fois )(?P<n>{NUM})(?: ?x)?"
     rf"|(?:mets |passe )?(?:en |la )?vitesse (?:a |en |sur )?(?:x ?)?(?P<n2>{NUM})(?: ?x)?"
     rf"|(?:set )?(?:the )?(?:playback )?speed (?:to )?(?P<n3>{NUM})(?: ?x)?"
     rf"|(?:play )?(?:at )?(?P<n4>{NUM}) ?x(?: speed)?")(
    lambda m: player("speed", number(m["n"] or m["n2"] or m["n3"] or m["n4"])))

# Position
rule("restart", r"recommence|reprends (?:du|au|depuis le) debut|(?:reviens|retourne|va|remets)(?: la video)? au debut|restart|start (?:it )?over|(?:go )?back to the (?:beginning|start)|go to the (?:beginning|start)|from the (?:beginning|start)")(lambda m: player("restart"))
rule("half", r"(?:va|aller|saute|passe|go|jump|skip)(?: directement)? (?:a la moitie|au milieu|to the middle|halfway|to halfway)")(lambda m: player("seek_fraction", 0.5))
rule("seek_to_clock", r"(?:va|aller|saute|passe|reprends|go|jump|skip)(?: directement)? (?:a|au|to|at) (?P<clock>\d{1,2}:\d{2}(?::\d{2})?)")(
    lambda m: player("seek_to", sum(int(n) * 60**i for i, n in enumerate(reversed(m["clock"].split(":"))))))


def _seek_clock(m):
    position = clock_words(m["clock"])
    return player("seek_to", position) if position is not None else None


# « Va à douze trente », « go to twelve thirty », « va à 12 30 »: 12:30, not 42 minutes
rule("seek_to_words", rf"(?:va|aller|saute|passe|reprends|go|jump|skip)(?: directement)? (?:a|au|to|at) (?P<clock>\d{{1,2}} \d\d|(?:{_WORD})(?: (?:et |and )?(?:{_WORD}))+)")(_seek_clock)
rule("seek_to", rf"(?:va|aller|saute|passe|go|jump|skip)(?: directement)? (?:a|au|to|at)(?: la)?(?: minute)? {DURATION}")(
    lambda m: player("seek_to", seconds(m, bare_unit="m")))
rule("seek_forward", rf"(?:avance|avancer|saute|va en avant|forward|skip(?: ahead| forward)?|fast forward|go forward|jump ahead)(?: de| of| by)? {DURATION}(?: en avant)?|avance|forward|skip ahead")(
    lambda m: player("seek_by", seconds(m) if m.groupdict().get("s") or m.groupdict().get("m") or m.groupdict().get("h") else 10))
rule("seek_back", rf"(?:recule|reculer|rembobine|reviens|retourne|go back|rewind|skip back|jump back|back)(?: de| of| by)? {DURATION}(?: en arriere| back)?|recule|rembobine|rewind")(
    lambda m: player("seek_back", seconds(m) if m.groupdict().get("s") or m.groupdict().get("m") or m.groupdict().get("h") else 10))

# Play, pause
rule("pause", r"(?:mets |met )?(?:sur |en )?pause(?: la video| the video| it)?|stop|stoppe|arrete(?: la video)?|pause")(lambda m: player("pause"))
rule("play", r"(?:mets |remets )(?:le |en |sur )?(?:play|lecture)|lecture|play|reprends|reprise|continue|resume|relance(?: la video)?|remets la video|lance la lecture|joue|unpause|keep playing")(lambda m: player("play"))

# Navigation
rule("next_video", r"(?:passe a la |mets la |lance la )?(?:video )?suivante|(?:la )?video suivante|next(?: video| one)?|play the next(?: one| video)?")(lambda m: Call("youtube_nav", {"action": "next"}))
rule("previous_video", r"(?:reviens a la |remets la )?(?:video )?precedente|(?:la )?video precedente|previous(?: video| one)?")(lambda m: Call("youtube_nav", {"action": "previous"}))
rule("scroll_down", r"descends|defile|(?:va )?plus bas|scroll(?: down)?|go down")(lambda m: Call("youtube_nav", {"action": "scroll_down"}))
rule("scroll_up", r"remonte|(?:va )?plus haut|scroll up|go up")(lambda m: Call("youtube_nav", {"action": "scroll_up"}))
# « Encore »: repeats the last simple command (« baisse le son »… « encore »); otherwise, more videos (cli.py)
rule("again", r"(?:et )?(?:encore|again)(?: une fois| un peu| un coup| one more time| a bit(?: more)?)?|un peu plus|une fois de plus|a bit more|once more|one more time|(?:fais le|refais le|do it) (?:encore|again)|refais")(
    lambda m: Call("repeat", {}))
rule("more", r"montre m'en plus|montre moi plus|montre plus|(?:charge |affiche )?plus de videos|show (?:me )?more|load more|more videos")(lambda m: Call("youtube_nav", {"action": "more"}))
rule("back", r"retour|reviens en arriere|page precedente|go back|back")(lambda m: Call("youtube_nav", {"action": "back"}))

# Info (spoken answer)
rule("info_remaining", r"(?:il )?reste combien(?: de temps)?|combien de temps (?:il )?reste|c'est encore long|how (?:much time|much|long)(?: is)? left|how long is left|time left|what(?:'s| is) left")(
    lambda m: Call("youtube_info", {"what": "remaining"}))
rule("info_title", r"c'est quoi cette video|c'est quoi|(?:quelle|c'est quelle) (?:est cette )?video|qu'est ce que (?:je regarde|c'est)|what (?:is this video|am i watching|video is this)"
     r"|what's this video|(?:c'est quoi|quel est) le (?:titre|nom)(?: de(?: (?:la|cette) video| la chaine)?)?|(?:what's|what is) the (?:title|name)(?: of (?:the|this) video)?|(?:c'est )?(?:qui|quelle chaine)(?: a fait| fait)? (?:cette|la) video"
     # fold removed « cette vidéo »: « qui a fait cette vidéo ? » → « qui a fait », « quelle est cette vidéo ? » → « quelle est »
     r"|(?:c'est )?qui (?:a fait|fait|a publie|a poste|a mis en ligne)|(?:quelle|quel) est"
     # the channel: the title is spoken along with the channel name
     r"|(?:c'est )?(?:quelle|quoi|qui) (?:cette |la )?chaine|quelle est (?:cette |la )?chaine|c'est la chaine de qui"
     r"|(?:what|which) channel(?: is (?:this|it|that))?|what(?:'s| is) (?:this|the) channel|who (?:made|posted|uploaded)(?: this video| this| it)?")(
    lambda m: Call("youtube_info", {"what": "title"}))

# Pick a video from the displayed list
PICK = r"(?:lance|mets|joue|ouvre|clique sur|regarde|choisis|prends|play|open|put on|watch|click)"
rule("play_index", rf"{PICK}?(?: moi)? ?(?:la |le |the )?(?:video )?(?:numero |number |n )?(?P<ord>{ORD}|{ORD_DIGITS}|\d{{1,2}})(?: video| one)?")(
    lambda m: Call("youtube_play", {"index": ORDINALS.get(m["ord"]) or int(re.match(r"\d+", m["ord"])[0])}))


def _pick_number(m):
    n = number(m["n"])
    if n is None or n != int(n) or not 1 <= n <= 50 or (m["n"] == "one" and not m["numero"]):
        return None  # « play the one »: not a number
    return Call("youtube_play", {"index": int(n)})


# Number in words: « mets la trois », « lance la numéro trois », « play number four » (article or « numéro » required)
rule("play_number", rf"{PICK}?(?: moi)? ?(?:(?:la |le |the )(?:video )?(?P<numero>numero |number )?|(?:video )?(?P<numero2>numero |number ))"
     rf"(?P<n>(?:{_WORD})(?: et une?| (?:{_WORD}))?)(?: video)?")(
    lambda m: _pick_number({"n": m["n"], "numero": m["numero"] or m["numero2"]}))

# YouTube pages
rule("youtube_home", r"(?:mets|ouvre|lance|va sur|affiche|montre moi|retourne sur|open|go to|put on|show me)?(?: sur)? ?youtube|(?:la )?page d'accueil|accueil(?: youtube)?|(?:youtube )?home(?: page)?")(
    lambda m: Call("youtube_open", {"page": "home"}))
rule("youtube_subscriptions", r"(?:va sur |ouvre |affiche |montre moi |mets )?(?:mes |les )?abonnements|(?:go to |open |show )?(?:my )?subscriptions")(
    lambda m: Call("youtube_open", {"page": "subscriptions"}))
rule("youtube_history", r"(?:va sur |ouvre |affiche |montre moi )?(?:mon |l')?historique|(?:go to |open |show )?(?:my )?(?:watch )?history")(
    lambda m: Call("youtube_open", {"page": "history"}))
rule("youtube_watch_later", r"(?:va sur |ouvre |affiche |montre moi )?(?:ma liste |la liste )?(?:a regarder plus tard)|(?:go to |open |show )?(?:my )?watch later(?: list)?")(
    lambda m: Call("youtube_open", {"page": "watch_later"}))
rule("youtube_playlists", r"(?:va sur |ouvre |affiche |montre moi )?(?:mes |les )?playlists|(?:go to |open |show )?(?:my )?playlists")(
    lambda m: Call("youtube_open", {"page": "playlists"}))
rule("youtube_channel", r"(?:va|aller|ouvre|mets|affiche|montre moi|go|open|show me)(?: sur| to)? (?:la chaine|the channel)(?: youtube)? (?:de |d'|of )(?P<name>.+)|(?:go to |open )(?P<name2>.+)(?:'s| s) channel")(
    lambda m: Call("youtube_open", {"page": "channel", "channel": m["name"] or m["name2"]}))
rule("youtube_latest", r"(?:mets|lance|joue|montre moi|regarde|play|put on|show me)(?: moi)? (?:la derniere video|the latest video|the last video|the newest video) (?:de |d'|of |from |by )(?P<name>.+)")(
    lambda m: Call("youtube_play", {"latest_from": m["name"]}))


# Does the sentence chain one action after another? (« cherche X et lance la première »)
CHAINED = re.compile(r"\b(?:et|puis|ensuite|and|then)\b.*\b(?:lance|mets|met|joue|regarde|choisis|prends|clique|passe|"
                     r"ouvre|ferme|quitte|va|coupe|monte|baisse|avance|recule|cherche|like|ajoute|play|watch|put|pick|"
                     r"ecris|tape|envoie|dis|write|type|send|tell|"
                     r"open|close|quit|switch|go|mute|pause|celle|celui|premiere|deuxieme|troisieme|derniere|first|"
                     r"second|third|last)\b"
                     # shortcut aimed at another app: « nouvel onglet dans Safari » (switch to the app, then ⌘T)
                     r"|\b(?:onglet|tab|note|document|doc|fenetre|window)\b.*\b(?:dans|sur|in|on)\b")


def _search(m: re.Match) -> Call | None:
    query = re.sub(r"^(?:des |une |les |un |some |a )?(?:videos? |clips? )?(?:sur |de |about |on |for |of )?", "", m["q"])
    query = re.sub(r" (?:sur|on) youtube$", "", query).strip()
    if not query or re.search(SEARCH_FILTER_WORDS, query) or CHAINED.search(query):
        return None  # date or length filters, or a chained action: the LLM handles it
    if re.match(r"(?:dans|sur|in|on) (?:la |cette |the |this )?page\b", query):
        return None  # « cherche dans la page … »: not YouTube
    return Call("youtube_search", {"query": query})


rule("search", r"(?:cherche|recherche|trouve|search(?: for)?|find|look (?:for|up))(?: moi)? (?P<q>.+)")(_search)


# Apps: only if the name matches an installed app
def _app(tool: str):
    def build(m: re.Match) -> Call | None:
        app = apps.find(m["name"], strict=tool == "quit_app")  # quit: never a guessed app
        return Call(tool, {"name": app.name}) if app else None  # unknown: left to level 1
    return build


rule("quit_app", r"(?:ferme|quitte|close|quit|exit)(?: l'app(?:lication)?| l'appli| the app)? (?P<name>.+)")(_app("quit_app"))
rule("open_app", r"(?:ouvre|lance|demarre|open|launch|start)(?: moi)?(?: l'app(?:lication)?| l'appli| the app)? (?P<name>.+)")(_app("open_app"))
rule("switch_app", r"(?:passe sur|passe a|passe dans|va sur|va dans|bascule sur|affiche|montre moi|switch to|go to|show me)(?: l'app(?:lication)?| l'appli| the app)? (?P<name>.+)")(_app("switch_app"))


OFF = (r"\b(?:quitte|sors|enleve|retire|coupe|desactive|cache|vire|arrete|stoppe|supprime|ferme|eteins|exit|leave|remove|disable|hide"
       r"|turn off|stop|close|get out)\b")
KEYWORDS = [  # (name, pattern searched anywhere, call), in order
    ("kw_exit_fullscreen", rf"{OFF}.*\b(?:plein ecran|full ?screen)\b", lambda: player("exit_fullscreen")),
    ("kw_fullscreen", r"\b(?:plein ecran|full ?screen)\b", lambda: player("fullscreen")),
    ("kw_theater_off", rf"{OFF}.*\b(?:mode cinema|theat(?:er|re) mode)\b", lambda: player("theater", False)),
    ("kw_theater", r"\b(?:mode cinema|theat(?:er|re) mode)\b", lambda: player("theater", True)),
    ("kw_pip", r"\bimage dans l'? ?image\b|\bpicture in picture\b", lambda: player("pip")),
    ("kw_miniplayer", r"\bmini ?(?:lecteur|player)\b", lambda: player("miniplayer")),
    ("kw_captions_off", rf"{OFF}.*\b(?:sous titres?|subtitles|captions)\b", lambda: player("captions", False)),
    ("kw_captions_on", r"\b(?:sous titres?|subtitles|captions)\b", lambda: player("captions", True)),
    ("kw_skip_ad", r"\b(?:passe|saute|ignore|zappe|skip)\b.*\b(?:pub|pubs|publicite|ads?)\b", lambda: player("skip_ad")),
    ("kw_unmute", r"\b(?:remets|reactive|rallume)\b.*\bson\b|\bunmute\b", lambda: Call("system_volume", {"action": "unmute"})),
    ("kw_mute", rf"{OFF}.*\bson\b|\bmute\b", lambda: Call("system_volume", {"action": "mute"})),
    ("kw_mac_volume_up", r"\b(?:monte|augmente|remonte)\b.*\b(?:son|volume)\b.*\b(?:mac|ordi|ordinateur|computer)\b", lambda: Call("system_volume", {"action": "up"})),
    ("kw_mac_volume_down", r"\b(?:baisse|diminue)\b.*\b(?:son|volume)\b.*\b(?:mac|ordi|ordinateur|computer)\b", lambda: Call("system_volume", {"action": "down"})),
    ("kw_volume_up", r"\b(?:monte|augmente|remonte)\b.*\b(?:son|volume)\b|\bplus fort\b|\blouder\b", lambda: Call("system_volume", {"action": "up"})),
    ("kw_volume_down", r"\b(?:baisse|diminue)\b.*\b(?:son|volume)\b|\bmoins fort\b|\bquieter\b", lambda: Call("system_volume", {"action": "down"})),
    ("kw_autoplay_off", rf"{OFF}.*\b(?:lecture auto(?:matique)?|autoplay)\b", lambda: player("autoplay", False)),
    ("kw_loop_off", rf"{OFF}.*\bboucle\b", lambda: player("loop", False)),
    ("kw_loop", r"\ben boucle\b", lambda: player("loop", True)),
    ("kw_chapter_next", r"\bchapitre suivant\b|\bnext chapter\b", lambda: player("chapter_next")),
    ("kw_chapter_previous", r"\bchapitre precedent\b|\bprevious chapter\b", lambda: player("chapter_previous")),
    ("kw_next_video", r"\bvideo suivante\b|\bnext video\b", lambda: Call("youtube_nav", {"action": "next"})),
    ("kw_previous_video", r"\bvideo precedente\b|\bprevious video\b", lambda: Call("youtube_nav", {"action": "previous"})),
    ("kw_unpause", rf"{OFF}.*\bpause\b", lambda: player("play")),  # « enlève la pause »
    ("kw_pause", r"\bpause\b", lambda: player("pause")),
    # « play » alone (« play Daft Punk » asks for something else: LLM)
    ("kw_play", r"\b(?:reprends|reprise|resume|relance|lecture)\b|\bplay$", lambda: player("play")),
]
KEYWORDS = [(name, re.compile(pattern), build) for name, pattern, build in KEYWORDS]
# Sentence asking for more than a simple action: no keywords, hand over to the LLM
COMPLEX = re.compile(r"\b(?:celle|celui|ceux|qui parle|cherche|recherche|search|find|chaine|channel|videos?|sur|"
                     r"one|about|et puis|puis|ensuite|et|and|then|ne|pas|don't|dont|not)\b")


# « Commente : super vidéo »: the text is taken as is from the transcript, never rewritten.
# « Comment » and « Commentaire » alone require « : » or « , » (« comment réparer un vélo » is a question)
COMMENT = re.compile(r"^\s*(?:(?:commente|commentez|commenter|commenti|(?:é|e)cris un commentaire|mets un commentaire|poste un commentaire|"
                     r"publie un commentaire|ajoute un commentaire|laisse un commentaire|write a comment|"
                     r"post a comment|leave a comment|comenta|comentar|escribe un comentario|kommentiere|kommentieren|"
                     r"commenta|scrivi un commento|comente|comentar|escreva um comentário)\b\s*(?:[:,;.\-–]|disant|qui dit|saying)?"
                     r"|(?P<bare>comment|commentaire)\s*(?P<sep>[:,;]))\s*(?P<text>.*)$",
                     re.IGNORECASE)


# « Écris : acheter du pain »: typed as is into the frontmost app (never Return)
# « Écris … », « Marque … », « Dicte … »: the text is typed as soon as the key is released (already transcribed
# while speaking), with no mode to open and no « fin de dictée » to say
# Variants heard by the transcription: « Marc », « Mark » for « marque »; « écrit »; « tap »; « saisie »…
WRITE = re.compile(r"^\s*(?P<verb>note down|jot down|(?:é|e)crivez|(?:é|e)crits|(?:é|e)crit|(?:é|e)cris|marquez|marques|marque|marc|mark"
                   r"|tapez|tapes|tape|tap|saisissez|saisis|saisie|saisi|dictez|dictes|dicte|notez|note|write|type)"
                   r"(?P<me>[- ]moi)?\b\s*(?P<sep>[:,;\-–]|que\b|this\b\s*[:,]?)?\s*(?P<text>.+)$", re.IGNORECASE)
# Verbs with another meaning (« Mark my words », « note bien », « marque la vidéo comme vue »): be careful
AMBIGUOUS_WRITE = {"marc", "mark", "marque", "marques", "marquez", "note", "notez", "tap"}
# Writing request (« écris-moi un poème », « write an email to my boss »): never typed, hand over to the LLM
GENERATE = re.compile(
    r"^(?:un|une|des|du|a|an|some|mon|ma|my|le|la|the|quelques?)\s+(?:(?:petite?|courte?|jolie?|beau|belle|short|little|nice|quick)\s+)?"
    r"(?:po[eè]mes?|poems?|e ?mails?|mails?|courriels?|lettres?|letters?|histoires?|stor(?:y|ies)|chansons?|songs?|articles?|essais?"
    r"|essays?|discours|speech(?:es)?|blagues?|jokes?|ha[iï]kus?|dissertations?|r[ée]dactions?|r[ée]sum[ée]s?|summar(?:y|ies)|tweets?"
    r"|posts?|slogans?|raps?|textes? (?:sur|pour)|texts? (?:about|for)|paragraphes? sur|paragraphs? about|r[ée]ponses? [àa]|repl(?:y|ies) to)\b",
    re.IGNORECASE)
FOR_ME = re.compile(r"^(?:me\s+)?(?:un|une|des|du|a|an|some|quelque chose|something|ce que|what)\b", re.IGNORECASE)
# « Tape entrée »: a key, not text (Boulito never presses Return)
KEY_NAME = re.compile(r"^(?:sur |on )?(?:la touche |the )?(?:entr[ée]e|enter|return|retour|tab|tabulation|espace|space|[ée]chap|escape"
                      r"|backspace|effacer|supprimer|delete)(?: key)?\s*[.!]?$", re.IGNORECASE)
# After an ambiguous verb, a command (« Marc, mets pause ») is run, not typed
COMMAND_VERBS = set(INFINITIVES.values()) | {
    "relance", "verrouille", "pause", "play", "open", "close", "quit", "launch", "switch", "go", "turn", "set", "skip", "mute",
    "unmute", "stop", "resume", "search", "find", "show", "lock", "rewind", "like"}
# « Ouvre youtube.com », « va sur lemonde point fr »: address taken from the original text
TLD = r"(?:com|fr|org|net|io|dev|app|be|ch|ca|eu|co|uk|de|es|it|tv|gg|me|ai)"
URL = re.compile(rf"^\s*(?:ouvre|va sur|affiche|open|go to|show)\s+(?:le site(?: de| du| d')?\s*|the site\s+|the website\s+)?"
                 rf"(?P<url>[\w' -]*?[\w-]+(?:\.[\w-]+)*\.{TLD}(?:/\S*)?)\s*[.!?]?$", re.IGNORECASE)


# Line breaks spoken in the text: « écris bonjour, saute une ligne, à demain » → « bonjour,\nÀ demain ».
# « soit la ligne », « sot la ligne »: « saute la ligne » mistranscribed. « À la ligne » alone (without « retour »)
# is a command only next to punctuation: « il est à la ligne d'arrivée » stays text.
BREAK = r"(?:saute[rz]?|sauts?|soit|sois|sot|seau)\s+(?:une|la|deux|1|2)\s+lignes?|saut de ligne|retour (?:à|a) la ligne|nouvelle ligne|new line|next line|line break"
PARAGRAPH = r"nouveau paragraphe|new paragraph"
LINE_BREAK = re.compile(rf"\s*(?:\b(?P<paragraph>{PARAGRAPH})\b|\b(?P<line>{BREAK})\b|(?<=[,.;:!?])\s*(?:à|a) la ligne\b"
                        rf"|\s(?:à|a) la ligne(?=\s*(?:[,.;:!?]|$)))\s*[,.;:]?\s*", re.IGNORECASE)
# « Écris bonjour à tous et écris à demain »: the repeated verb is not typed
WRITE_AGAIN = re.compile(r"\s*(?:,|\bet|\bpuis|\band|\bthen)\s+(?:(?:é|e)cris|(?:é|e)crit|(?:é|e)crivez|write)\b\s*[:,]?\s*", re.IGNORECASE)


def line_breaks(said: str) -> str:
    def replace(m: re.Match) -> str:
        count = 2 if m["paragraph"] or re.search(r"\b(?:deux|2)\b", m["line"] or "") else 1
        return "\n" * count
    lines = LINE_BREAK.sub(replace, said).split("\n")
    return "\n".join([lines[0]] + [line[:1].upper() + line[1:] for line in lines[1:]])  # capital letter at the start of each line


def write(text: str) -> Call | None:
    m = WRITE.match(text)
    if not m or re.match(r"(?:un |a )?(?:commentaire|comment|message)\b", m["text"], re.IGNORECASE):
        return None  # « écris un commentaire / un message »: other tools
    verb, rest = fold(m["verb"]), m["text"].strip()
    if m["sep"] != ":" and (GENERATE.match(rest) or ((m["me"] or re.match(r"me\s", rest, re.IGNORECASE)) and FOR_ME.match(rest))):
        return None  # composing a text: never made up and then typed
    if verb in ("tape", "tapes", "tapez", "tap", "type") and KEY_NAME.match(re.sub(r"\s*\b(?:please|s'il (?:te|vous) pla[iî]t|stp)\b", "", rest)):
        from .i18n import t

        return Call("speak", {"text": t("out_of_scope")}, rule="write_key")
    if verb in AMBIGUOUS_WRITE:
        if verb == "mark" and (m["sep"] or "")[:1] not in (":", ",", ";", "-", "–"):
            return None  # « Mark my words », « mark this as done »: not dictation
        if re.match(r"(?:la|le|les|l'|cette|ce|cet|ces|this|that|the|it|ça|ca|tout|all)\b.*\b(?:comme|as)\b", rest, re.IGNORECASE):
            return None  # « marque la vidéo comme vue »
        if verb.startswith("note") and re.match(r"bien\b", rest, re.IGNORECASE):
            return None  # « note bien que… »
        words = fold(rest).split()
        if words and words[0] in COMMAND_VERBS:
            inner = route(rest)
            if inner and inner.tool not in ("type_text", "ask", "calculate", "speak", "youtube_search") and not inner.rule.startswith("kw_"):
                return inner  # « Marc, mets pause »: the command
    said = WRITE_AGAIN.sub(" ", m["text"].strip().strip("«»\"“” "))
    sentence = re.search(r"[,;:!?]", said[:-1]) or len(said.split()) >= 8
    if said.endswith(".") and not said.endswith("..") and not sentence:
        said = said[:-1]  # note (« acheter du pain »): final period added by the transcription removed; sentence: kept
    said = line_breaks(said).strip(" ")  # « écris saute une ligne »: just the line break
    return Call("type_text", {"text": said}, rule="write") if said else None


def web(text: str) -> Call | None:
    spoken = re.sub(rf"\s+point\s+(?={TLD}\b)", ".", text, flags=re.IGNORECASE)  # « lemonde point fr »
    m = URL.match(spoken)
    if not m:
        return None
    host, _, path = m["url"].partition("/")
    host = re.sub(r"[\s']+", "", host)  # « le monde.fr » → lemonde.fr, « le figaro.fr » → lefigaro.fr
    return Call("open_url", {"url": host.lower() + ("/" + path if path else "")}, rule="open_url")


MUSIC_KINDS = {"chanson": "song", "titre": "song", "morceau": "song", "song": "song", "track": "song",
               "album": "album", "playlist": "playlist", "musique de": "artist", "chansons de": "artist",
               "son de": "artist", "music by": "artist", "songs by": "artist"}
MUSIC = re.compile(
    r"^\s*(?:mets|mais|met|joue|lance|écoute|ecoute|play|put on)(?:[- ]moi)?\s+"
    r"(?:(?:la |le |l'|l’|ma |mon |the |my |some |de la |des |du )?(?P<kind>chansons? de|musique de|son de|chanson|titre|morceau|album|playlist|song|track|music by|songs by)\s+"
    r"|(?P<du>du |de la |some )(?=.+\s(?:sur|dans|on|in)\s+(?:apple\s+)?musi(?:c|que)))?"
    r"(?P<q>.+?)(?:\s+(?:sur|dans|on|in)\s+(?:l'app\s+)?(?:apple\s+)?(?P<app>music|musique))?"
    r"(?P<shuffle>\s+(?:en aléatoire|en aleatoire|au hasard|en shuffle|on shuffle|shuffled))?\s*[.!?]?\s*$", re.IGNORECASE)


# English order: the name before the type (« play my workout playlist », « put on the Discovery album »)
MUSIC_EN = re.compile(r"^\s*(?:play|put on)\s+(?:my|the)\s+(?P<q>.+?)\s+(?P<kind>playlist|album)\s*[.!]?\s*$", re.IGNORECASE)


# « Le son de » a device or an app: the volume, never an artist
SOUND_OF_DEVICE = re.compile(r"(?:mon |ma |le |la |l'|l |du |de la |ce |cet |cette )?(?:mac|macbook|ordi|ordinateur|computer|safari|tele|tv"
                             r"|television|video|youtube|lecteur|ecran|enceintes?|haut parleurs?|casque|airpods|iphone|ipad)\b")


def music(text: str) -> Call | None:
    """« Mets l'album Discovery », « Joue ma playlist sport », « Mets du Daft Punk sur Apple Music »."""
    if (en := MUSIC_EN.match(text)) and not re.search(r"youtube", text, re.IGNORECASE):
        return Call("music_play", {"query": en["q"].strip(), "kind": en["kind"].lower()}, rule="music")
    m = MUSIC.match(text)
    if not m or not (m["kind"] or m["app"]) or re.search(r"youtube|vid[ée]o", text, re.IGNORECASE):
        return None  # « lance le titre de la vidéo »: YouTube, not music
    query = m["q"].strip().strip("«»\"“” ")
    if fold(query) in ("suivante", "suivant", "precedente", "precedent", "d'avant", "d'apres", "next", "previous", "en pause", "pause",
                       "en boucle", "en repetition", "on repeat", "on loop"):
        return None  # « mets la chanson suivante »: media keys; « mets le titre en boucle »: player
    kind_word = fold(m["kind"] or "")
    if kind_word == "son de" and (SOUND_OF_DEVICE.match(plain(query)) or re.search(r"\s(?:à|a|au|sur)\s+(?:\d+|fond|max\w*)\s*(?:%|pour ?cent)?$", query, re.IGNORECASE)):
        return None  # « mets le son de mon Mac à 30 »: the volume, not an artist
    kind = "artist" if m["du"] or kind_word.startswith("chansons") else MUSIC_KINDS.get(kind_word, "any")
    args = {"query": query, "kind": kind}
    if m["shuffle"]:
        args["shuffle"] = True
    return Call("music_play", args, rule="music")


APPS_MSG = r"(?P<app>discord|messages|imessage|sms|whats ?app|telegram|télégramme|telegramme)"
_TO = r"(?P<to>[^,:]+?)"
MESSAGE = [
    # « envoie un message à Paul sur Discord (pour lui dire) que j'arrive », « … qui dit : j'arrive »
    re.compile(rf"^\s*(?:envoie|envoyer|écris|ecris)(?:[- ]lui)? (?:un )?(?:message|mot|texto|sms)? ?(?:à|a) {_TO} (?:sur|dans|via|par) {APPS_MSG}"
               rf",?\s*(?:pour (?:lui )?dire(?: que| qu')?|qui dit(?: que| qu')?|disant(?: que| qu')?|en disant(?: que| qu')?|que|qu'|:)\s*:?\s*(?P<text>.+?)\s*$", re.IGNORECASE),
    # « dis à Alice sur WhatsApp que le film commence à 21 heures »
    re.compile(rf"^\s*(?:dis|dites|réponds|reponds)(?:[- ]lui)? (?:à|a) {_TO} (?:sur|dans|via|par) {APPS_MSG},?\s*(?:que |qu'|:)\s*(?P<text>.+?)\s*$", re.IGNORECASE),
    # « send a message to Tom on Telegram saying I'm running late », « text Sam on WhatsApp: call me »
    re.compile(rf"^\s*(?:send(?: a)? (?:message|text|note) to|message|text) {_TO} (?:on|in|via|with) {APPS_MSG},?\s*(?:saying(?: that)?|that says|to say(?: that)?|:)\s*:?\s*(?P<text>.+?)\s*$", re.IGNORECASE),
    re.compile(rf"^\s*tell {_TO} (?:on|in|via) {APPS_MSG},?\s*(?:that |:)?\s*(?P<text>.+?)\s*$", re.IGNORECASE),
]


def message(text: str) -> Call | None:
    """Dictated message in common phrasings: the text sent is exactly the one in the sentence (no LLM)."""
    for pattern in MESSAGE:
        m = pattern.match(text)
        if m and m["to"].strip() and m["text"].strip(" .«»\"“”"):
            app = fold(m["app"]).replace(" ", "")
            app = {"imessage": "messages", "sms": "messages", "telegramme": "telegram"}.get(app, app)
            said = m["text"].strip().strip("«»\"“” ")
            if said.endswith(".") and not said.endswith(".."):
                said = said[:-1]  # final period added by the transcription
            said = said[0].upper() + said[1:]
            return Call("send_message", {"app": app, "recipient": m["to"].strip(), "text": said}, rule="message")
    return None


# --- Long dictation ---------------------------------------------------------------------------
DICTATE = re.compile(r"^\s*(?:dicte|dicter|dictée|dictee|dictate|dicta|diktiere|detta|dita)\s*[:,]\s*(?P<text>.+?)\s*$", re.IGNORECASE)
DICTATION_STOP = re.compile(
    r"(?:^|(?<=[\s.,!?;:]))(?:fin de (?:la )?dict[ée]e|fin dict[ée]e|stop(?:pe)? (?:la )?dict[ée]e|arr[êe]te (?:la )?dict[ée]e|termine (?:la )?dict[ée]e"
    r"|end (?:of )?dictation|stop dictation|fin del dictado|diktat beenden|fine (?:della )?dettatura|fim do ditado)\s*[.!?]*\s*$", re.IGNORECASE)
DICTATION_NEWLINE = re.compile(rf"(?:^|(?<=[\s.,!?;:]))(?:à la ligne|a la ligne|{BREAK})\s*[.!?]*\s*$", re.IGNORECASE)
DICTATION_PARAGRAPH = re.compile(r"(?:^|(?<=[\s.,!?;:]))(?:nouveau paragraphe|new paragraph)\s*[.!?]*\s*$", re.IGNORECASE)
DICTATION_UNDO = re.compile(r"^\s*(?:efface(?:[- ]ça| ca| la dernière phrase| la derniere phrase)?|supprime ça|supprime ca|annule ça|annule ca"
                            r"|delete that|scratch that|undo that)\s*[.!?]*\s*$", re.IGNORECASE)


def dictation_step(text: str) -> tuple[str, str | None]:
    """One dictated sentence → (text to type, command); command: stop, newline, paragraph, undo or None.

    « Merci à tous. Fin de dictée. » → (« Merci à tous. », stop); « Bonjour, à la ligne. » → (« Bonjour, », newline).
    """
    text = " ".join(text.split())
    if DICTATION_UNDO.match(text):
        return "", "undo"
    for pattern, command in ((DICTATION_STOP, "stop"), (DICTATION_PARAGRAPH, "paragraph"), (DICTATION_NEWLINE, "newline")):
        m = pattern.search(text)
        if m:
            return text[:m.start()].strip(), command
    return text, None


def dictation(text: str) -> Call | None:
    """« Dicte : bonjour à tous »: dictation starts with this text."""
    m = DICTATE.match(text)
    if not m:
        return None
    return Call("dictation", {"action": "start", "text": m["text"]}, rule="dictation")


# Lone words that ask for nothing: noise, a voice from a video, a reply to no one. Never an action.
# « Merci » is not one of them: it gets a reply (smalltalk)
FILLERS = {"yeah", "yep", "yes", "no", "nope", "ok", "okay", "right", "so", "well", "uh", "um", "hmm", "hm", "mm", "ah", "oh",
           "eh", "euh", "heu", "hein", "bon", "bah", "ben", "alors", "voila", "ouais", "oui", "non", "you", "d'accord", "daccord",
           "accord", "super", "cool", "top", "bien", "tres", "parfait", "genial", "wow", "bang", "it's", "its", "bad", "good", "nice",
           "great", "yeah", "and", "et", "the", "le", "la"}


def filler(text: str) -> bool:
    text = without_name(text)  # « Yeah Boulito »: the name doesn't count
    if smalltalk(text):
        return False  # « Merci », « Merci Boulito »: a reply
    if not fold(text) and re.search(r"\w", text):
        return True  # « S'il vous plaît »: fold strips polite phrases, nothing is left
    words = fold(text).split()
    return bool(words) and len(words) <= 5 and all(w in FILLERS for w in words)


def calculation(text: str) -> Call | None:
    """« Combien font 15 % de 80 ? »: exact calculation in code (calc.py), never by the LLM."""
    from . import calc

    expression = calc.spoken(text)
    return Call("calculate", {"expression": expression}, rule="calc") if expression else None


# General knowledge, language or definition questions (« comment on dit facture en anglais ? »):
# spoken answer from the local AI, with no tools (safety.ask). Text folded by fold.
QUESTION = re.compile(
    r"^(?:(?:dis moi|dites moi|tu peux me dire|pouvez vous me dire|tu sais|vous savez|est ce que tu sais|sais tu"
    r"|tell me|do you know|can you tell me)\s+)?"
    r"(?:comment (?:on dit|dit on|se dit|on traduit|traduit on|on ecrit|ecrit on|s'ecrit|on epelle|on prononce|prononce t on"
    r"|fonctionne|marche|on fait|faire|s'appelle|\w+(?:er|ir|re)\b)"
    r"|c'est quoi|c est quoi|qu'est ce qu|qu est ce qu|qui\b|quel|quelle|quels|quelles|pourquoi|combien\b|quand\b"
    r"|ou (?:est|sont|se trouve|se trouvent|se situe|vit|vivent)\b|est ce vrai|c'est vrai que"
    r"|explique|expliquez|definis|definissez|definition|donne moi (?:la definition|un synonyme|le contraire|un exemple)"
    r"|synonyme|contraire de|que veut dire|que signifie|ca veut dire quoi|traduis|traduisez|traduire|conjugue|conjuguez"
    r"|how (?:do|does|did|can|is|are|was|many|much|long|far|old|big|tall|to)\b|what\b|what's|who\b|who's|why\b"
    r"|when (?:is|was|did|does|do|are|were)\b|where (?:is|are|was|does|do)\b|which\b|explain|define|definition"
    r"|translate|is it true)")
# Questions about what is running on the Mac: for the tools (level 1), not for the tool-less AI
ABOUT_THE_MAC = re.compile(
    r"\b(?:videos?|youtube|chaine|playlists?|chansons?|musique|morceau|titre|album|artiste|chanteur|chanteuse|chante"
    r"|minuteurs?|timers?|rappels?|agenda|calendrier|rendez vous|evenements?|onglets?|safari|batterie|volume|son du mac"
    r"|sous titres|chapitres?|pubs?|abonnes?|abonnements?|regarde|joue|playing|en cours|ecoute|song|music|track"
    r"|reminders?|calendar|meetings?|tab|battery|heure|time|date|jour|day|app|appli|application|ecran|screen"
    r"|boulito|ton nom|your name)\b")


def question(text: str, folded: str) -> Call | None:
    # fold may have removed « cette vidéo » (« qui a fait cette vidéo ? »): the original text counts too
    if not QUESTION.match(folded) or ABOUT_THE_MAC.search(folded) or ABOUT_THE_MAC.search(plain(text)):
        return None
    return Call("ask", {"question": text.strip()}, rule="question")


def summary(text: str) -> Call | None:
    """« Résume cette vidéo très courtement »: with the accent it is « résumer », never the English « resume »."""
    if not re.search(r"\brésum", text, re.IGNORECASE):
        return None
    m = re.search(r"\b(?:à|a|vers|autour de)\s+(?:la minute\s+)?(\d+:\d\d|[\w\s-]+?(?:minutes?|min|secondes?|heures?)\b)", text, re.IGNORECASE)
    if m:
        clock_match = re.fullmatch(r"(\d+):(\d\d)", m[1])
        seconds = int(clock_match[1]) * 60 + int(clock_match[2]) if clock_match else timer_seconds(fold(m[1]))
        if seconds:
            return Call("youtube_summary", {"at": seconds}, rule="summary")
    return Call("youtube_summary", {}, rule="summary")


REMIND_START = re.compile(r"^\s*(?:rappelle[- ]moi|remind me|préviens[- ]moi|previens[- ]moi|réveille[- ]moi|reveille[- ]moi"
                          r"|wake me(?: up)?)\b[\s,]*", re.IGNORECASE)
LINK_WORDS = re.compile(r"^(?:de |d'|d’|que |qu'|qu’|pour |to |that |about |of )", re.IGNORECASE)
# « Rappelle-moi ce que je dois faire demain »: a question about what is planned, not a reminder to create
REMIND_WHAT = re.compile(r"^(?:ce que|ce qu'|ce qu’|ce qui|quels? sont|quelles? sont|what|which|my (?:reminders|tasks|to ?dos?|appointments|meetings)"
                         r"|mes (?:rappels|t[aâ]ches|rendez[- ]vous|rdv|r[ée]unions|[ée]v[éè]nements))\b",
                         re.IGNORECASE)


def _iso(when, has_time: bool) -> str:
    return when.isoformat(timespec="minutes") if has_time else when.date().isoformat()


def remind(text: str) -> Call | None:
    """« Rappelle-moi demain à 9 h d'appeler la banque », « … dans 20 minutes de sortir le linge ».

    The reminder text keeps the dictated words; the date is parsed by dates.extract.
    Without text (« réveille-moi dans une demi-heure »), it is a timer.
    """
    import datetime

    from . import dates

    if not REMIND_START.match(text):
        return None
    now = datetime.datetime.now()
    asked = REMIND_START.sub("", text, count=1)
    if REMIND_WHAT.match(asked):
        if re.search(r"\b(?:agenda|calendrier|rendez[- ]vous|rdv|r[ée]unions?|calendar|meetings?|appointments?|schedule)\b", asked, re.IGNORECASE):
            found = dates.extract(asked, now)
            return Call("calendar_list", {"when": found.at.date().isoformat() if found else ""}, rule="remind_list")
        return Call("reminder_list", {}, rule="remind_list")
    found = dates.extract(text, now)
    if not found:
        return None
    label = LINK_WORDS.sub("", REMIND_START.sub("", found.rest).strip()).strip(" .,!?«»\"“”")
    if not label and re.match(r"(?:dans|in)\b", found.text.strip(), re.IGNORECASE):
        seconds = timer_seconds(fold(found.text)) or round((found.at - now).total_seconds())  # the spoken duration, exactly
        return Call("timer", {"action": "start", "seconds": max(1, seconds)}, rule="remind")
    return Call("reminder_add", {"text": label, "when": found.at.isoformat(timespec="minutes")}, rule="remind")


AGENDA_READ = re.compile(
    r"^\s*(?:qu'est[- ]ce que j'ai|qu’est[- ]ce que j’ai|j'ai quoi|j’ai quoi|qu'est[- ]ce qu'il y a|quels? sont mes (?:rendez-vous|rdv|événements|évènements|evenements)"
    r"|(?:montre|lis|donne|dis)[- ]moi mon (?:agenda|programme|planning)|mon (?:agenda|programme|planning)"
    r"|what(?:'s| is) on my (?:calendar|schedule)|what do i have|what's my schedule|my (?:calendar|schedule))\b(?P<rest>.*)$", re.IGNORECASE)
AGENDA_ADD = re.compile(
    r"^\s*(?:(?:tu peux|peux[- ]tu|pourrais[- ]tu|est-ce que tu peux|can you|could you|please)\s+)?(?:me\s+)?"
    r"(?:ajoute|ajouter|crée|cree|créer|creer|note|noter|mets|mettre|programme|programmer|planifie|planifier|inscris|inscrire"
    r"|add|create|schedule|put|book)(?:[- ]moi)?\s+"
    r"(?:un |une |a |an )?(?:nouvel |nouveau |nouvelle |new )?"
    r"(?P<kind>rendez-vous|rendez vous|rdv|réunion|reunion|événement|évènement|evenement|meeting|appointment|event|appel|call|"
    r"dîner|diner|déjeuner|dejeuner|lunch|dinner)\b", re.IGNORECASE)
AGENDA_ADD_TO = re.compile(r"^\s*(?:ajoute|note|mets|inscris|add|put)(?: ça| ca| this)? (?:dans|à|a|sur|to|in|on) (?:mon|my) (?:agenda|calendrier|calendar)\b\s*:?\s*",
                           re.IGNORECASE)
AGENDA_TITLE_LEAD = re.compile(r"^(?:dans (?:mon|l')? ?(?:agenda|calendrier)|to my calendar|in my calendar|appelée?|intitulée?|nommée?|called|named|titled|:)\s*",
                               re.IGNORECASE)
AGENDA_KINDS = {"rdv": "rendez-vous", "rendez vous": "rendez-vous", "reunion": "réunion", "evenement": "événement",
                "évènement": "événement", "diner": "dîner", "dejeuner": "déjeuner"}


def agenda(text: str) -> Call | None:
    """Calendar: « qu'est-ce que j'ai demain ? » (read), « ajoute un rendez-vous lundi à 14 h avec Paul » (create,
    always confirmed). Reminders (« mes rappels ») don't go through here."""
    import datetime

    from . import dates

    if re.search(r"\brappels?\b|\breminders?\b", text, re.IGNORECASE):
        return None
    now = datetime.datetime.now()
    if m := AGENDA_READ.match(text):
        rest = m["rest"]
        if re.search(r"\b(?:cette semaine|la semaine|this week|the week)\b", rest, re.IGNORECASE):
            return Call("calendar_list", {"days": 7}, rule="agenda")
        found = dates.extract(rest, now)
        return Call("calendar_list", {"when": found.at.date().isoformat() if found else ""}, rule="agenda")
    kind = AGENDA_ADD.match(text)
    to_calendar = AGENDA_ADD_TO.match(text)
    if not kind and not to_calendar:
        return None
    found = dates.extract(text, now)
    if not found:
        return None
    rest = found.rest
    if kind:
        rest = AGENDA_ADD.sub("", rest, count=1).strip()
        word = kind["kind"].lower()
        word = AGENDA_KINDS.get(fold(word), word)
    else:
        rest, word = AGENDA_ADD_TO.sub("", rest, count=1).strip(), ""
    rest = AGENDA_TITLE_LEAD.sub("", rest).strip(" .,!?«»\"“”")
    title = f"{word} {rest}".strip() if word and not re.match(r"^(?::|appel|intitul|nomm|called|named|titled)", rest, re.IGNORECASE) else (rest or word)
    title = title[:1].upper() + title[1:] if title else ""
    return Call("calendar_add", {"title": title, "when": _iso(found.at, found.has_time)}, rule="agenda")


# « Comment » alone is also the French question word: « comment on dit… ? », « comment ça marche ? »
FRENCH_HOW = re.compile(r"^\s*comment\s+(?:on|ça|ca|tu|vous|il|elle|ils|elles|je|j'|est|sont|faire|fait|faut|se|s'|t'|m'|dit|dire"
                        r"|va|vont|allez|aller|marche|fonctionne|peut|peux|puis|pouvez|sais|savoir|était|avoir|a|as|ont|dois|doit)\b",
                        re.IGNORECASE)


def comment(text: str) -> Call | None:
    m = COMMENT.match(text)
    if not m:
        return None
    if m["bare"] and m["sep"] != ":" and (FRENCH_HOW.match(f"comment {m['text']}") or text.rstrip().endswith("?")):
        return None  # « Comment, ça va ? »: a question, not a comment
    said = m["text"].strip().strip("«»\"“” ").strip()
    if not said:
        from .i18n import t

        return Call("speak", {"text": t("comment.how")}, rule="comment_empty")
    return Call("youtube_comment", {"text": said[0].upper() + said[1:]}, rule="comment")


def sounds_like(word: str) -> str:
    """Simple phonetic form: « Boulitaux », « Boulitos » and « Boulito » end up the same."""
    word = re.sub(r"eaux?|aux?|ots?|oh", "o", word)
    word = word.replace("ph", "f").replace("qu", "k")
    word = re.sub(r"(.)\1", r"\1", word)  # doubled letters
    return re.sub(r"(?<=\w)[tsxde]$", "", word)  # silent final letter


# Parakeet sometimes writes a short word in Cyrillic (« Булито » for « Boulito »): read it back in Latin letters
CYRILLIC = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "io", "ж": "j", "з": "z", "и": "i",
                          "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
                          "у": "ou", "ф": "f", "х": "kh", "ц": "ts", "ч": "tch", "ш": "ch", "щ": "ch", "ъ": "", "ы": "y",
                          "ь": "", "э": "e", "ю": "iou", "я": "ia", "і": "i", "ї": "i", "є": "ie", "ґ": "g"})


def _same_name(candidate: str, name: str) -> bool:
    import difflib

    candidate = candidate.translate(CYRILLIC)
    for a, b in ((candidate, name), (sounds_like(candidate), sounds_like(name))):
        close_length = abs(len(a) - len(b)) <= max(1, len(b) // 4)  # « boule » is not « boulito »
        if a and close_length and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
            return True
    return False


# Typical words of each language (folded: lowercase, no accents), to spot a command spoken in a language
# the user hasn't ticked. Parakeet recognizes 25 languages and can't be forced to use just one.
LANG_WORDS = {
    "fr": "le la les un une des du et est pas mets met ouvre ferme lance cherche joue passe monte baisse coupe remets "
          "quelle quel quoi c'est il elle je moi vous sur dans avec pour suivante precedente chanson musique son "
          "minuteur heure rappelle ecris envoie dis mon ma mes cette ce plein ecran es",
    "en": "the and is it of to please open close search show previous turn up down what what's "
          "how set remind send text tell write song music my this louder quieter",
    # (no anglicisms common in French: play, pause, stop, skip, next, like, mute, timer, full screen…)
    "es": "el los las y es pon abre cierra busca reproduce siguiente anterior sube baja silencia que hora temporizador "
          "cancion musica por favor mi esta pantalla completa",
    "de": "der die das ein eine und ist nicht mach offne schliess such spiel spiele nachste vorherige lauter leiser stumm "
          "wie spat uhr bitte lied musik mein diese vollbild",
    "it": "il lo gli un una e metti apri chiudi cerca riproduci successivo precedente alza abbassa che ora canzone "
          "musica per favore mio questa schermo intero",
    "pt": "o os as um uma e coloca abre fecha procura toca proximo anterior aumenta abaixa que horas musica por favor "
          "meu esta tela cheia",
}
LANG_SETS = {code: set(words.split()) for code, words in LANG_WORDS.items()}
# Only words specific to one language count (« un », « que », « e » are shared)
LANG_OF = {w: code for code, vocab in LANG_SETS.items() for w in vocab
           if sum(w in other for other in LANG_SETS.values()) == 1}


# --- Confirmations (creating an event or a reminder, sending a message…) -----------------------
# Only a clear yes confirms: every word of the reply (except polite words and the assistant's name) is a yes,
# three words at most. In open listening, a video saying « go back » or « d'accord avec toi » confirms nothing.
# Any negation, any other word, silence: cancelled. When in doubt, cancel.
AFFIRMATIVE = {"oui", "ouais", "ouaip", "yes", "yeah", "yep", "yup", "si", "ja", "sim", "vas y", "je confirme", "confirme",
               "confirm", "confirmed", "claro", "certo", "ok", "okay", "oke", "okey"}  # « ok »: a yes (the user's choice)
# Only when said alone: « d'accord avec toi », « exactement ce que je pense » don't confirm
AFFIRMATIVE_ALONE = {"d'accord", "bien sur", "of course", "genau", "exactement", "carrement"}
YES = re.compile(r"(?:{0})(?: (?:{0})){{0,2}}".format("|".join(sorted(AFFIRMATIVE, key=len, reverse=True))))
POLITE = re.compile(r"\b(?:merci(?: beaucoup| bien)?|please|s'il (?:te|vous) plait|stp|svp|bitte|por favor|per favore|thanks|thank you"
                    r"|gracias|danke|grazie|obrigad[oa])\b")
NEGATIVE = re.compile(
    r"\b(?:non|no|nope|nan|pas|ne|n'|jamais|annule|annuler|annulez|arrete|arretez|stop|cancel|surtout|merci|laisse|laissez|"
    r"oublie|oubliez|attends|attendez|tard|later|wait|don't|dont|do not|not|never|nein|nicht|keine?|nao|niente|nessun|"
    r"mais|but|peut etre|maybe|perhaps|quizas|vielleicht|forse|talvez|sais|know|hmm|euh|bof)\b")


def confirmed(answer: str) -> bool:
    """Does the reply to a confirmation count as a clear yes? (otherwise: cancelled)

    Only the exact assistant name is removed (« oui Boulito »): a name close to a reply word, like
    « Louis » for « oui », must never erase it."""
    name = wake_name()
    kept = " ".join(w for w in plain(answer).split() if w.replace(" ", "") != name)
    text = " ".join(POLITE.sub(" ", kept).split())
    if not text or NEGATIVE.search(text) or len(text.split()) > 3:
        return False
    return text in AFFIRMATIVE_ALONE or bool(YES.fullmatch(text))


def understood_languages() -> list[str]:
    """Languages ticked by the user ([feedback] understood), otherwise the interface language."""
    from . import config

    feedback = config.load()["feedback"]
    chosen = [code for code in feedback.get("understood", []) if code in LANG_WORDS]
    return chosen or [feedback.get("language", "en")]


def foreign_language(text: str, understood: list[str]) -> str | None:
    """Unticked language the sentence was clearly spoken in, otherwise None.

    Refused only if there are words typical of another language and none from an understood one:
    « Mets la vidéo Get Lucky » stays French, « pause » (shared) always gets through.
    """
    scores = {code: 0 for code in LANG_SETS}
    for w in fold(text).split():
        if w in LANG_OF:
            scores[LANG_OF[w]] += 1
    if any(scores.get(code, 0) for code in understood):
        return None
    best = max((code for code in scores if code not in understood), key=lambda c: scores[c], default=None)
    return best if best and scores[best] >= 1 else None


def wake_name() -> str:
    """Chosen wake word (« Boulito » by default), folded, without spaces. Not via fold, which removes « merci »."""
    from . import config

    return plain(config.load()["trigger"].get("wake_word", "Boulito")).replace(" ", "")


def without_name(text: str) -> str:
    """The text without the assistant's name, wherever it is (« Merci Boulito » → « Merci »)."""
    name = wake_name()
    return " ".join(w for w in text.split() if not _same_name(plain(w).replace(" ", ""), name))


def wake_split(text: str) -> str | None:
    """Does the sentence start with the assistant's name? Returns the rest ("" if only the name), otherwise None.

    Tolerates « Hey / Dis / OK [name] … », spelling variants (« Boulitaux », « Bolito » for « Boulito ») and a name
    split in two by the transcription (« Boul ito » for « Boulito »).
    """
    name = wake_name()
    words = text.split()
    for lead in (0, 1):  # « [name] … » or « Hey / Dis / OK [name] … »
        if len(words) <= lead:
            break
        if lead and plain(words[0]) not in ("hey", "dis", "ok", "okay", "eh", "he", "allo", "oye", "hola", "hallo", "ehi", "ei", "ola",
                                            "please"):
            break
        for n in (1, 2):  # the name may be written as one or two words
            if _same_name(plain(" ".join(words[lead:lead + n])).replace(" ", ""), name):
                return " ".join(words[lead + n:]).lstrip(" ,.:;!-?")
    return None


def wake_find(text: str) -> str | None:
    """The assistant's name anywhere in the sentence: returns what follows it ("" if at the end), otherwise None.

    Open listening during a video or a conversation: the transcript starts amid other voices
    (« …et donc en 1789 Boulito mets pause »), only what follows the name counts.
    """
    name = wake_name()
    words = text.split()
    for i in range(len(words)):
        for n in (1, 2):  # the name may be written as one or two words
            if i + n <= len(words) and _same_name(plain(" ".join(words[i:i + n])).replace(" ", ""), name):
                rest = " ".join(words[i + n:]).lstrip(" ,.:;!-?")
                for k in (3, 2, 1):  # « Merci Boulito », « Bonne nuit Boulito »: the polite words before the name
                    if not rest.strip(" .!?") and i >= k and smalltalk(" ".join(words[i - k:i])):
                        return " ".join(words[i - k:i])
                return rest
    return None


def strip_name(text: str) -> str:
    """Removes the assistant's name at the start (« Boulito, mets pause », « Hey Boulito… », « Boulitaux »)."""
    rest = wake_split(text)
    return text if rest is None else rest


# Politeness and help: short spoken reply, no LLM. Text folded by plain (fold removes « merci »), without the name.
SMALLTALK = [(name, key, re.compile(pattern)) for name, key, pattern in [
    ("smalltalk_thanks", "smalltalk.thanks", r"(?:(?:ok|okay|super|parfait|top|cool|genial|great|perfect) )?(?:merci|thanks|thank you|thx|cimer)"
                                             r"(?: (?:beaucoup|bien|infiniment|mille fois|a toi|a vous|pour tout|so much|very much|a lot"
                                             r"|for everything))*|mille mercis|many thanks"),
    ("smalltalk_hello", "smalltalk.hello", r"(?:bonjour|salut|coucou|bonsoir|hello|hi|good morning|good afternoon|good evening)(?: a toi| a vous| there)?"),
    ("smalltalk_how", "smalltalk.how", r"(?:(?:salut|bonjour|hello|hi) )?(?:ca va(?: bien)?|comment ca va|comment vas tu|comment allez vous|tu vas bien"
                                       r"|vous allez bien|how are you(?: doing)?|how's it going|how is it going|how do you do)"),
    ("smalltalk_bye", "smalltalk.bye", r"au revoir|a plus(?: tard)?|a bientot|a demain|a tout a l'heure|a la prochaine|bye(?: bye)?|goodbye|good bye"
                                       r"|see you(?: later| soon| tomorrow)?"),
    ("smalltalk_night", "smalltalk.night", r"bonne nuit|good ?night|nighty night"),
    ("help", "help.short", r"aide(?: moi)?|a l'aide|help(?: me)?|qu'est ce que tu (?:sais|peux) faire|que (?:sais|peux) tu faire|tu (?:sais|peux) faire quoi"
                           r"|what can you do|what can i (?:say|ask)|what do you do|what are you able to do"),
]]


def smalltalk(text: str) -> Call | None:
    """« Merci », « Bonjour Boulito », « Ça va ? », « Qu'est-ce que tu sais faire ? »: short spoken reply."""
    said = plain(without_name(text))
    for name, key, pattern in SMALLTALK:
        if pattern.fullmatch(said):
            from .i18n import t

            return Call("speak", {"text": t(key)}, rule=name)
    return None


# Simple commands that can be chained without the LLM: « mets en pause et coupe le son »
CHAINABLE = {"player", "system_volume", "media", "youtube_account", "open_app", "switch_app", "quit_app",
             "app_shortcut", "screen", "local_info", "youtube_nav"}
CHAIN_SPLIT = re.compile(r"\s*,?\s*\b(?:et puis|et ensuite|puis|ensuite|et|and then|then|and)\b\s*", re.IGNORECASE)


def route_chain(text: str) -> list[Call] | None:
    """Sentence chaining simple commands, each matched by a rule; otherwise None (LLM).

    Only if the whole sentence is not already a command (« écris du pain et des œufs » stays dictation).
    """
    text = strip_name(text)
    parts = [p for p in CHAIN_SPLIT.split(text) if p.strip(" .,!?")]
    if len(parts) < 2 or len(parts) > 4:
        return None
    calls = [route(p) for p in parts]
    if any(c is None for c in calls):
        return None
    last_is_dictation = calls[-1].tool == "type_text"  # « … puis écris … »: dictation can only come last
    if all(c.tool in CHAINABLE for c in (calls[:-1] if last_is_dictation else calls)):
        return calls
    return None


def route(text: str) -> Call | None:
    """Rule matching the whole sentence, otherwise an unambiguous keyword; None: level 1."""
    said, text = text, strip_name(text)
    if call := smalltalk(text):  # « Merci Boulito », « Bonjour »: not "Yes?"
        return call
    if said.strip() and not text.strip():  # just the name: it answers
        from .i18n import t

        return Call("speak", {"text": t("yes?")}, rule="name_only")
    if call := (comment(text) or message(text) or agenda(text) or write(text) or web(text) or remind(text)
                or music(text) or summary(text) or calculation(text)):
        return call
    folded = fold(text)
    if not folded:
        return None
    for name, pattern, build in RULES:
        m = pattern.match(folded)
        if m:
            call = build(m)
            if call is not None:
                call.rule = name
                return call
    if chain := route_chain(text):  # « mets play et monte le son »: two rules, no LLM
        return Call("chain", {"calls": chain}, rule="chain")
    if call := question(text, folded):  # « c'est quoi un ETF ? »: the AI answers, with no tools
        return call
    if len(folded.split()) <= 8 and not COMPLEX.search(folded):
        for name, pattern, build in KEYWORDS:
            if pattern.search(folded):
                call = build()
                call.rule = name
                return call
    return None
