"""Command line: ./voix <command>.

check     checks the machine, the models, the permissions and the project's isolation
download  downloads the models into models/ (the only command that uses the network)
stt       transcribes audio files, or measures the test set (--test)
listen    listens in push-to-talk and prints the transcript and its latency
yt        drives YouTube in Safari, without voice (state, navigation, search, player, account)
route     shows the action a phrase would trigger, without running it; --test replays tests/phrases.toml
run       runs a written phrase, as if it had been spoken
start     the full voice assistant: key → voice → action
test      replays the test phrases (rules and LLM) without microphone or Safari, and checks the target: 90% of phrases
stats     summarizes the log: success and latency of voice commands
"""

import argparse
import csv
import queue
import threading
import tomllib
import difflib
import json
import os
import platform
import plistlib
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, journal

SAFARI_INFO = Path("/Applications/Safari.app/Contents/Info.plist")
LAUNCH_AGENT = Path.home() / f"Library/LaunchAgents/{config.BUNDLE_ID}.plist"  # see autostart.py
TEST_PHRASES = config.PROJECT_DIR / "tests/audio/phrases.tsv"
ROUTE_PHRASES = config.PROJECT_DIR / "tests/phrases.toml"
LEVEL1_PHRASES = config.PROJECT_DIR / "tests/level1.toml"
OK, FAIL, INFO = "✓", "✗", "·"
INPUT_MONITORING_HELP = """\
Boulito needs Input Monitoring permission (to detect the key):
  1. Setup window → Open Settings next to Input Monitoring (from Terminal: System Settings → Privacy & Security →
     Input Monitoring)
  2. Turn on Boulito (or Terminal if you run ./voix from Terminal)
  3. Restart Boulito (or quit and reopen Terminal): the permission only applies after a restart"""


def run(cmd: list[str]) -> str:
    """Runs a command and returns its output, or "" if it fails."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def find_tool(name: str) -> str | None:
    path = shutil.which(name) or f"/opt/homebrew/bin/{name}"
    return path if Path(path).exists() else None


# --- check ------------------------------------------------------------------

def check_rows(cfg: dict) -> list[tuple[str, str, str]]:
    """(status, label, detail) for each check."""
    from . import audio, stt

    rows = []

    macos = run(["sw_vers", "-productVersion"])
    rows.append((OK if macos else FAIL, "macOS", macos or "not found"))

    safari = plistlib.loads(SAFARI_INFO.read_bytes()).get("CFBundleShortVersionString", "?")
    rows.append((INFO, "Safari", f"{safari} (YouTube control through AppleScript)"))

    py_ok = sys.version_info >= (3, 13) and platform.machine() == "arm64"
    rows.append((OK if py_ok else FAIL, "Python", f"{platform.python_version()} ({platform.machine()})"))

    if not config.PACKAGED:  # .dmg version: Python ships with the app, uv is not used
        uv = find_tool("uv")
        rows.append((OK if uv else FAIL, "uv", run([uv, "--version"]) if uv else "missing: brew install uv"))

    from . import llm

    # Memory and disk: advice based on the chosen model, never a blocker (the choice stays with the user)
    key = cfg["llm"]["model"]
    ram = int(run(["/usr/sbin/sysctl", "-n", "hw.memsize"]) or 0) / 2**30
    advised = llm.TIERS.get(key, ("", "", 16, ""))[2]
    rows.append((OK if ram >= advised else INFO, "Memory",
                 f"{ram:.0f} GB" + ("" if ram >= advised else f" (the {key} model advises {advised} GB: it may be slow)")))
    free = shutil.disk_usage(config.DATA_DIR).free / 2**30
    needed = 2.5 + (0 if llm.local_path() else llm.DISK_GB.get(key, 6))  # speech recognition + missing model
    rows.append((OK if free >= needed else FAIL, "Free disk", f"{free:.0f} GB" + ("" if free >= needed else f" (about {needed:.0f} GB needed)")))

    rows.append((OK, "AI model", f"{llm.model_id()} (runs inside Boulito, no server)") if llm.local_path()
                else (INFO, "AI model", f"not downloaded yet: {llm.model_id()} (Setup window → AI model → Download)"))

    # Paths as written (without following links): a models/ folder linked to an external disk is allowed
    outside = [v for v in config.PATH_VARS if not Path(config.ENV[v]).absolute().is_relative_to(config.DATA_DIR)]
    place = "the data folder" if config.PACKAGED else "the project folder"
    rows.append((FAIL if outside else OK, "Caches", f"outside {place}: {', '.join(outside)}" if outside else f"models, uv, torch… inside {place}"))

    get = "Setup window → Speech recognition → Download" if config.PACKAGED else "./voix download --speech-only"
    rows.append((OK, "Parakeet", stt.model_id()) if stt.is_downloaded() else (FAIL, "Parakeet", f"missing: {get}"))
    rows.append((OK, "Silero VAD", "present, checksum verified") if audio.vad_downloaded() else (FAIL, "Silero VAD", f"missing: {get}"))

    # Permissions of the app that runs this command (Terminal in development). Boulito.app has its own:
    # missing here, they only affect commands run directly from Terminal (./boulito goes through the app).
    mic = audio.microphone_status()
    rows.append((OK if mic == "granted" else INFO, "Microphone", f"{mic} for this app (Boulito.app has its own)"))
    monitoring = audio.input_monitoring_allowed()
    rows.append((OK if monitoring else INFO, "Input", "Input Monitoring " + ("granted" if monitoring else "not granted") + " for this app (Boulito.app has its own)"))

    return rows


def network_connections(seconds: int = 15) -> list[str]:
    """Loads everything the assistant loads (LLM included), then records the process's network sockets
    for a few seconds: there must be none, neither connection nor open port.

    Safeguard: onnxruntime 1.30 sent telemetry to Microsoft as soon as it was imported.
    """
    import AppKit  # noqa: F401 (sounds)
    import Quartz  # noqa: F401 (key)

    from . import audio, llm, stt

    stt.Transcriber()
    audio.Vad()
    audio.Microphone(lambda chunk: None)
    engine = llm.Engine()
    if llm.local_path():
        engine.start()
        engine.chat([{"role": "system", "content": llm.system_prompt()}, {"role": "user", "content": "Command: pause"}])
    seen: set[str] = set()
    try:
        for _ in range(seconds):
            time.sleep(1)
            out = run(["/usr/sbin/lsof", "-a", "-p", str(os.getpid()), "-i", "-n", "-P"])
            seen |= {" ".join(line.split()[7:]) for line in out.splitlines()[1:]}
    finally:
        engine.stop()
    return sorted(seen)


def outside_inventory() -> list[str]:
    """What the project created or installed outside its folder."""
    items = []
    uv = None if config.PACKAGED else find_tool("uv")  # .dmg version: uv is not used
    if uv and "/opt/homebrew/" in str(Path(uv).resolve()):
        items.append("uv (Homebrew)")
    if LAUNCH_AGENT.exists():
        items.append(f"LaunchAgent {LAUNCH_AGENT}")
    if config.PACKAGED:
        items.append(f"data {config.DATA_DIR}")
    return items


def cmd_check(args: argparse.Namespace) -> int:
    t = journal.Timings()
    with t.step("check"):
        cfg = config.load()
        rows = check_rows(cfg)
        if args.network:
            connections = network_connections()
            rows.append((FAIL, "Network", "sockets: " + ", ".join(connections)) if connections
                        else (OK, "Network", "no connection or open port in 15 s (speech and AI models loaded)"))
    failed = [label for status, label, _ in rows if status == FAIL]

    print("Boulito: checks\n")
    for status, label, detail in rows:
        print(f"  {status} {label:<13} {detail}")
    inventory = outside_inventory()
    print(f"\nOutside the folder: {', '.join(inventory) if inventory else 'nothing'}")
    print(f"Models folder: {config.MODELS_DIR}")
    print(f"\n{'All good' if not failed else 'To fix: ' + ', '.join(failed)} ({t.steps['check']:.0f} ms)")

    journal.write({"cmd": "check", "ok": not failed, "failed": failed, "ms": t.steps})
    return 1 if failed else 0


# --- download ---------------------------------------------------------------

def cmd_download(args: argparse.Namespace) -> int:
    os.environ["HF_HUB_OFFLINE"] = "0"  # the only command allowed to go on the network
    os.environ["HF_HUB_DISABLE_XET"] = "1"  # throttled download (see llm.download)
    from . import audio, stt

    if stt.is_downloaded():
        print(f"✓ Parakeet already there ({stt.model_id()})")
    else:
        print(f"Downloading {stt.model_id()} (2.3 GB)…", flush=True)
        stt.download()
        print("✓ Parakeet downloaded")
    from . import llm

    for size, repo in llm.MODELS.items():
        if llm.local_path(repo):
            print(f"✓ LLM {size} already there ({repo})")
        elif args.all_llm or size == args.model or (repo == llm.model_id() and not args.speech_only):
            print(f"Downloading {repo}…", flush=True)
            llm.download(repo)
            print(f"✓ LLM {size} downloaded")
    if audio.vad_downloaded():
        print("✓ Silero VAD already there")
    else:
        audio.download_vad()
        print("✓ Silero VAD downloaded, checksum verified")
    print(f"Folder: {config.MODELS_DIR}")
    return 0


# --- stt --------------------------------------------------------------------

def read_audio(path: str):
    """Any audio file → mono 16 kHz float32 (through ffmpeg)."""
    import numpy as np

    out = subprocess.run(
        [find_tool("ffmpeg") or "ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
         "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, np.float32)


def words(text: str) -> list[str]:
    """Comparable words: lowercase, no punctuation, decimals with a dot."""
    text = re.sub(r"(\d),(\d)", r"\1.\2", text.lower())
    return re.findall(r"\d+(?:\.\d+)?|\w+", text)


def word_errors(expected: str, heard: str) -> int:
    ops = difflib.SequenceMatcher(a=words(expected), b=words(heard)).get_opcodes()
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in ops if op != "equal")


def cmd_stt(args: argparse.Namespace) -> int:
    import numpy as np

    from . import audio, stt

    cfg = config.load()["audio"]
    transcriber = stt.Transcriber()
    seg = audio.Segmenter(audio.Vad(), cfg["vad_threshold"], cfg["vad_silence_ms"], cfg["pad_ms"])
    noise = np.random.default_rng(0)

    def transcribe(path: str) -> tuple[str, float]:
        """Same path as the microphone: VAD, trimming with a margin, then Parakeet."""
        samples = read_audio(path)
        silence = (1e-4 * noise.standard_normal(audio.SAMPLE_RATE // 2)).astype(np.float32)
        x = np.concatenate([silence, samples, silence])
        seg.reset()
        for i in range(0, len(x) - audio.CHUNK + 1, audio.CHUNK):
            seg.add(x[i : i + audio.CHUNK])
        if not seg.has_speech:
            return "", 0.0
        t = time.perf_counter()
        text = transcriber.transcribe(seg.speech_audio())
        return text, (time.perf_counter() - t) * 1000

    if not args.test:
        for path in args.files:
            text, ms = transcribe(path)
            print(f"{Path(path).name}  {ms:4.0f} ms  {text or '(nothing heard)'}")
        return 0

    rows = list(csv.reader(TEST_PHRASES.open(encoding="utf-8"), delimiter="\t"))
    missing = [n for n, *_ in rows if not (TEST_PHRASES.parent / f"{n}.wav").exists()]
    if missing:  # generated audio, not versioned (macOS voices)
        print("Test audio missing: run tests/audio/generate.sh first (it uses macOS voices and ffmpeg: brew install ffmpeg).")
        return 1
    errors = total = exact = 0
    latencies = []
    for n, voice, spoken, expected in rows:
        text, ms = transcribe(str(TEST_PHRASES.parent / f"{n}.wav"))
        e = word_errors(expected, text)
        errors, total, exact = errors + e, total + len(words(expected)), exact + (e == 0)
        latencies.append(ms)
        if e or args.verbose:
            print(f"  {'✗' if e else '✓'} {n} {voice:<9} {expected:<38} → {text or '(nothing heard)'}")
    latencies.sort()
    wer = 100 * errors / total
    print(f"\nExact: {exact}/{len(rows)} · word error rate: {wer:.1f} % · "
          f"median latency {latencies[len(latencies) // 2]:.0f} ms, max {latencies[-1]:.0f} ms")
    journal.write({"cmd": "stt --test", "exact": exact, "n": len(rows), "wer": round(wer, 1),
                   "ms": {"median": round(latencies[len(latencies) // 2], 1), "max": round(latencies[-1], 1)}})
    return 0


# --- listen -----------------------------------------------------------------

# Private texts: what is typed, sent, commented, reminded or added to the calendar is never written to
# the logs (only the action type and the length)
PRIVATE = {"type_text", "send_message", "youtube_comment", "reminder_add", "calendar_add", "dictation",
           "reminder_list", "calendar_list"}  # lists read aloud: titles of reminders and events


def private_text(text: str, steps: list) -> str:
    """The sentence as written to the logs. steps: the calls it gave (none: left to the LLM, or not routed)."""
    from . import router, safety

    level1_private = not steps and any(re.search(safety.INTENT[tool], router.fold(text))
                                       for tool in ("type_text", "send_message", "reminder_add", "calendar_add")
                                       if tool in safety.INTENT)
    if level1_private or any(step.tool in PRIVATE for step in steps):
        return f"(private, {len(text)} characters)"
    return text


def cmd_listen(args: argparse.Namespace) -> int:
    from . import assistant, audio, hotkey

    if not audio.input_monitoring_allowed():
        audio.request_input_monitoring()
        print(INPUT_MONITORING_HELP)
        return 1

    def heard(h: "assistant.Heard") -> None:
        early = " (early)" if h.early else ""
        print(f"“{h.text}”\n    ready {h.after_release_ms:.0f} ms after release{early} · "
              f"Parakeet {h.stt_ms:.0f} ms · {h.speech_s:.1f} s of speech · mic opened in {h.mic_start_ms:.0f} ms",
              flush=True)
        journal.write({"cmd": "listen", "text": private_text(h.text, []), "early": h.early, "speech_s": h.speech_s,
                       "ms": {"after_release": h.after_release_ms, "stt": h.stt_ms, "mic_start": h.mic_start_ms}})

    def status(message: str) -> None:
        print(f"· {message}", flush=True)
        journal.write({"cmd": "listen", "status": message})

    print("Loading models…", flush=True)
    t = time.perf_counter()
    listener = assistant.Listener(heard, status)
    listener.start()
    key = config.load()["trigger"]["key"]
    print(f"Ready in {time.perf_counter() - t:.1f} s (microphone: {listener.mic.name}). "
          f"Hold {hotkey.Hotkey.parse(key).label()} and speak. Esc to cancel, Ctrl-C to quit.", flush=True)
    try:
        listener.run()
    except KeyboardInterrupt:
        print("\nStopped.")
    except PermissionError:
        print(INPUT_MONITORING_HELP)
        return 1
    return 0


# --- yt ---------------------------------------------------------------------

YT_NAV = ["scroll_down", "scroll_up", "top", "more", "back", "forward", "next", "previous"]
YT_ACCOUNT = ["like", "unlike", "watch_later", "watch_later_remove", "subscribe", "unsubscribe"]


def clock(seconds: float) -> str:
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_value(value: str | None):
    """Parses "12:30" → 750 s, "1,5" → 1.5, "on"/"off" → True/False, otherwise returns the text."""
    if value is None:
        return None
    if re.fullmatch(r"\d+(:\d{2}){1,2}", value):
        return sum(int(n) * 60**i for i, n in enumerate(reversed(value.split(":"))))
    if re.fullmatch(r"-?\d+([.,]\d+)?", value):
        return float(value.replace(",", "."))
    return {"on": True, "oui": True, "off": False, "non": False}.get(value.lower(), value)


def print_state(st: dict) -> None:
    query = f" (“{st['query']}”)" if st.get("query") else ""
    print(f"Page: {st['page']}{query}  {st['url']}")
    for it in st["items"]:
        if it["kind"] == "channel":
            print(f"  {it['index']:>2}. [channel] {it['name']}  {it['url']}")
            continue
        parts = [it["title"] or "(untitled)"]
        if it["channel"]:
            parts.append(it["channel"])
        if it["duration"]:
            parts.append(it["duration"])
        if it["info"]:
            parts.append(it["info"])
        short = " [short]" if it["kind"] == "short" else ""
        print(f"  {it['index']:>2}. {' · '.join(parts)}{short}")
    p = st["player"]
    if p:
        muted = " (muted)" if p["muted"] else ""
        print(f"Player: “{p['title']}” — {p['channel']}")
        print(f"  {p['state']} {clock(p['position'])} / {clock(p['duration'])} · x{p['speed']} · volume {p['volume']}{muted}"
              f" · subtitles {p['captions'] or 'off'} · quality {p['quality']}"
              f"{' · full screen' if p['fullscreen'] else ''}{' · theater' if p['theater'] else ''}"
              f"{' · AD' if p['ad'] else ''}")
        if p["chapters"]:
            print(f"  chapter: {p['chapter']} ({len(p['chapters'])} chapters)")


def cmd_yt(args: argparse.Namespace) -> int:
    from . import safari, youtube

    t = time.perf_counter()
    try:
        c = args.yt_command
        if c == "state":
            result = youtube.state()
        elif c == "open":
            result = youtube.open_page(args.page)
        elif c == "search":
            result = youtube.search(args.query, args.sort, args.upload, args.duration, args.type)
        elif c == "channel":
            result = youtube.latest_video(args.name) if args.latest else youtube.open_channel(args.name)
        elif c == "play":
            result = youtube.play(index=args.index, video_id=args.id)
        elif c == "nav":
            result = youtube.nav(args.action)
        elif c == "player":
            result = youtube.player(args.action, parse_value(args.value))
        elif c == "account":
            result = youtube.account(args.action, confirmed=args.yes)
        elif c == "transcript":
            r = youtube.transcript()
            words = sum(len(s["text"].split()) for s in r["segments"])
            result = {"source": r["source"], "segments": len(r["segments"]), "words": words,
                      "start": r["segments"][:3], "end": r["segments"][-2:]}
        else:
            result = youtube.probe()
    except youtube.ConfirmationRequired:
        print("Sensitive action: run again with --yes to confirm.")
        return 1
    except (safari.SafariError, youtube.YouTubeError) as e:
        print(f"✗ {e}")
        journal.write({"cmd": f"yt {c}", "ok": False, "error": str(e), "ms": {"total": round((time.perf_counter() - t) * 1000, 1)}})
        return 1
    ms = (time.perf_counter() - t) * 1000

    if c == "state" and not args.json:
        print_state(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"({ms:.0f} ms)")
    journal.write({"cmd": f"yt {c}", "ok": True, "ms": {"total": round(ms, 1)}})
    return 0


# --- route, run, start ------------------------------------------------------------

def same(actual, expected) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and abs(actual - expected) < 1e-6
    return actual == expected


def describe_call(call) -> str:
    if call.tool == "chain":
        return " then ".join(describe_call(c) for c in call.args["calls"])
    args = ", ".join(f"{k}={v!r}" for k, v in call.args.items())
    return f"{call.tool}({args})"


def cmd_route(args: argparse.Namespace) -> int:
    from . import router

    if not args.test:
        text = " ".join(args.text)
        if not text.strip():
            print('Usage: ./voix route "volume up"   (or ./voix route --test)')
            return 2
        t = time.perf_counter()
        call = router.route(text)
        ms = (time.perf_counter() - t) * 1000
        print(f"“{text}” → {router.fold(text)!r}")
        print(f"   {describe_call(call)}  [rule {call.rule}, {ms:.2f} ms]" if call else f"   level 1 (LLM)  [{ms:.2f} ms]")
        return 0

    from . import dates

    failures, n = route_tests(args.verbose)
    duration_failures = duration_tests(args.verbose)
    dates_ok, dates_total = dates.run_tests(args.verbose)
    confirmation_failures = confirmation_tests(args.verbose)
    dictation_failures = dictation_tests(args.verbose)
    calc_failures = calc_tests(args.verbose)
    return 1 if (failures or duration_failures or confirmation_failures or dictation_failures or calc_failures
                 or dates_ok < dates_total) else 0


def calc_tests(verbose: bool = False) -> list[str]:
    """Replays tests/calculations.toml: exact value of each dictated calculation, and phrases that are not calculations."""
    from . import calc

    data = tomllib.loads((ROUTE_PHRASES.parent / "calculations.toml").read_text(encoding="utf-8"))
    failures = []
    for text, expected in data["value"].items():
        expression = calc.spoken(text)
        try:
            got = calc.evaluate(expression) if expression else None
        except calc.CalcError as e:
            got = f"error {e}"
        good = isinstance(got, (int, float)) and abs(got - expected) < 1e-9
        if not good or verbose:
            print(f"  {'✓' if good else '✗'} “{text}” → {expression} = {got} (expected {expected})")
        if not good:
            failures.append(text)
    for text in data["none"]["phrases"]:
        expression = calc.spoken(text)
        if expression or verbose:
            print(f"  {'✗' if expression else '✓'} “{text}” → {expression or 'not a calculation'}")
        if expression:
            failures.append(text)
    n = len(data["value"]) + len(data["none"]["phrases"])
    print(f"Calculations: {n - len(failures)}/{n} correct")
    return failures


def dictation_tests(verbose: bool = False) -> list[str]:
    """Replays tests/dictation.toml: text to type and command for each dictated phrase."""
    from . import router

    cases = tomllib.loads((ROUTE_PHRASES.parent / "dictation.toml").read_text(encoding="utf-8"))["case"]
    failures = []
    for c in cases:
        typed, command = router.dictation_step(c["text"])
        good = typed == c["typed"] and (command or "") == c["command"]
        if not good or verbose:
            print(f"  {'✓' if good else '✗'} “{c['text']}” → “{typed}” {command or ''} (expected “{c['typed']}” {c['command']})")
        if not good:
            failures.append(c["text"])
    print(f"Dictation: {len(cases) - len(failures)}/{len(cases)} correct")
    return failures


def confirmation_tests(verbose: bool = False) -> list[str]:
    """Replays tests/confirmations.toml: only a clear « oui » confirms."""
    from . import router

    data = tomllib.loads((ROUTE_PHRASES.parent / "confirmations.toml").read_text(encoding="utf-8"))
    cases = {**data["yes"], **data["no"]}
    failures = []
    for text, expected in cases.items():
        got = router.confirmed(text)
        if got != expected or verbose:
            print(f"  {'✓' if got == expected else '✗'} “{text}” → {'yes' if got else 'cancelled'} (expected {'yes' if expected else 'cancelled'})")
        if got != expected:
            failures.append(text)
    print(f"Confirmations: {len(cases) - len(failures)}/{len(cases)} correct")
    return failures


def duration_tests(verbose: bool = False) -> list[str]:
    """Replays tests/durations.toml: each dictated duration must give exactly the expected number of seconds."""
    from . import router

    cases = tomllib.loads((ROUTE_PHRASES.parent / "durations.toml").read_text(encoding="utf-8"))["seconds"]
    failures = []
    for text, expected in cases.items():
        got = router.timer_seconds(router.fold(text))
        if got != expected or verbose:
            print(f"  {'✓' if got == expected else '✗'} “{text}” → {got} s (expected {expected})")
        if got != expected:
            failures.append(text)
    print(f"Durations: {len(cases) - len(failures)}/{len(cases)} correct")
    return failures


def route_tests(verbose: bool = False) -> tuple[list[str], int]:
    """Replays tests/phrases.toml (level 0 only). Returns the failed phrases and the total count."""
    from . import apps, router

    phrases = tomllib.loads(ROUTE_PHRASES.read_text(encoding="utf-8"))["phrase"]
    fixed = apps.test_index()  # fake apps: same results on every Mac (and in the GitHub test)
    apps.installed = lambda: fixed
    failures, times = [], []
    for ph in phrases:
        t = time.perf_counter()
        call = router.route(ph["text"])
        times.append((time.perf_counter() - t) * 1000)
        if ph.get("level") == 1:
            good = call is None
        else:
            good = call is not None and call.tool == ph["tool"] and all(same(call.args.get(k), v) for k, v in ph.get("args", {}).items())
        if not good or verbose:
            expected = "level 1" if ph.get("level") == 1 else f"{ph['tool']}({ph.get('args', {})})"
            got = describe_call(call) if call else "level 1"
            print(f"  {'✓' if good else '✗'} “{ph['text']}”\n      expected {expected}\n      got      {got}")
        if not good:
            failures.append(ph["text"])
    n = len(phrases)
    print(f"\nRouting: {n - len(failures)}/{n} phrases correct · {max(times):.2f} ms worst case")
    journal.write({"cmd": "route --test", "ok": n - len(failures), "n": n, "ms": {"max": round(max(times), 2)}})
    return failures, n


def plain(text) -> str:
    """Lowercase without accents, to compare texts."""
    import unicodedata

    text = unicodedata.normalize("NFD", str(text).lower().replace("’", "'"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def value_matches(actual, expected) -> bool:
    if isinstance(expected, list):
        return any(value_matches(actual, e) for e in expected)
    if actual is None:
        return False
    if isinstance(expected, str) and expected.startswith("~"):
        return plain(expected[1:]) in plain(actual)
    if isinstance(expected, bool) or not isinstance(expected, (int, float)):
        return plain(actual) == plain(expected)
    try:
        return abs(float(actual) - expected) < 1e-6
    except (TypeError, ValueError):
        return False


# Conversation mode (without saying the name again): a video may talk during those seconds; only harmless
# commands get through (playback, sound, next video…), never typing, quitting, opening a site or sending
SAFE_FOLLOW_UP = {"player", "system_volume", "media", "youtube_nav", "youtube_play", "local_info", "youtube_info", "repeat"}
# « Encore »: commands that can be repeated as is, never an action with a lasting effect
REPEATABLE = {("system_volume", "up"), ("system_volume", "down"), ("player", "seek_by"), ("player", "seek_back"),
              ("player", "faster"), ("player", "slower"), ("player", "volume_up"), ("player", "volume_down"),
              ("player", "chapter_next"), ("player", "chapter_previous"), ("youtube_nav", "scroll_down"),
              ("youtube_nav", "scroll_up"), ("youtube_nav", "next"), ("youtube_nav", "previous"),
              ("youtube_nav", "more"), ("media", "next"), ("media", "previous")}


def with_previous(call, previous, text: str):
    """The command in light of the previous one (previous: the last simple command, less than 30 s old, or None).

    « Encore » repeats a simple command right away; otherwise the AI decides, with the previous command in view
    (returns None). « Plus bas » right after « baisse le son » is about the sound, not the page: the AI decides too.
    """
    from . import router

    if call is not None and call.tool == "repeat":
        if previous is not None and (previous.tool, previous.args.get("action")) in REPEATABLE:
            return previous
        return None if previous is not None else router.Call("youtube_nav", {"action": "more"}, rule="more")
    about_volume = previous is not None and (previous.tool == "system_volume" or previous.tool == "player"
                                             and str(previous.args.get("action", "")).startswith("volume"))
    if call is not None and call.rule in ("scroll_down", "scroll_up") and about_volume \
            and re.fullmatch(r"(?:encore )?(?:un peu )?plus (?:bas|haut)|(?:remonte|descends)(?: encore)?(?: un peu)?",
                             router.plain(router.strip_name(text))):
        return None
    return call


def calls_match(calls: list[tuple[str, dict]], expected: list[dict]) -> bool:
    return len(calls) == len(expected) and all(
        tool == e["tool"] and all(value_matches(args.get(k), v) for k, v in e.get("args", {}).items())
        for (tool, args), e in zip(calls, expected))


def fixture_state(fx: dict | None) -> dict | None:
    """Fake page from tests/level1.toml in the youtube.state() format."""
    if fx is None:
        return None
    player = None
    if "player" in fx:
        player = {"captions": None, "chapter": None, "fullscreen": False, "theater": False, "qualities": [], **fx["player"]}
    return {"page": fx["page"], "url": "/", "query": fx.get("query"),
            "items": [{"index": i, "kind": "video", "info": "", **it} for i, it in enumerate(fx.get("items", []), 1)],
            "more": [{"index": None, "kind": "video", "info": "", **it} for it in fx.get("more", [])],
            "player": player}


SENSITIVE_ACTIONS = ("subscribe", "unsubscribe")


def cmd_test(args: argparse.Namespace) -> int:
    from . import llm, router, safety

    print("Level 0 (rules), tests/phrases.toml")
    failures0, n0 = route_tests(args.verbose)
    duration_failures = duration_tests(args.verbose)
    failures0 += duration_failures
    from . import dates

    dates_ok, dates_total = dates.run_tests(args.verbose)
    failures0 += ["date"] * (dates_total - dates_ok)
    failures0 += confirmation_tests(args.verbose)
    failures0 += dictation_tests(args.verbose)

    data = tomllib.loads(LEVEL1_PHRASES.read_text(encoding="utf-8"))
    fixtures = {name: fixture_state(fx) for name, fx in data["fixtures"].items()} | {"none": None}
    if args.model:  # for this test only: config.toml is not modified
        config.load()["llm"]["model"] = args.model
    config.load()["feedback"]["understood"] = ["en", "fr", "es", "de", "it", "pt"]  # the set covers the 6 languages
    print(f"\nLoading the LLM ({llm.model_id()})…", flush=True)
    engine = llm.Engine()
    print(f"   ready in {engine.start():.1f} s")
    print(f"\nEnd to end (rules then LLM {llm.model_id()}), tests/level1.toml")
    ok1, llm_times, levels = 0, [], {0: 0, 1: 0}
    try:
        for ph in data["phrase"]:
            page = {"now": fixtures[ph["fixture"]]}
            after = fixtures.get(ph.get("after"))
            calls: list[tuple[str, dict]] = []
            previous = router.Call(ph["previous"]["tool"], ph["previous"].get("args", {})) if "previous" in ph else None
            call = router.route(ph["text"])
            steps = [] if call is None else call.args["calls"] if call.tool == "chain" else [call]
            ignored = ph.get("follow_up") and call is not None and not all(c.tool in SAFE_FOLLOW_UP for c in steps)
            call = with_previous(call, previous, ph["text"])
            if ignored:  # conversation without the name: a command that is not simple is ignored
                level, ms = 0, 0.0
            elif call is not None:
                steps = call.args["calls"] if call.tool == "chain" else [call]
                calls, level, ms = [(c.tool, c.args) for c in steps], 0, 0.0
            else:
                def fake_execute(c, confirm=None):
                    calls.append((c.tool, dict(c.args)))
                    if c.tool in ("youtube_search", "youtube_open", "youtube_nav") and after is not None:
                        page["now"] = after
                    return safety.Result("ok")

                _, timings = safety.run_level1(ph["text"], engine, execute_fn=fake_execute, state_fn=lambda: page["now"],
                                               previous=previous, allowed=SAFE_FOLLOW_UP if ph.get("follow_up") else None)
                level, ms = 1, timings["llm"]
                llm_times.append(ms)
            levels[level] += 1
            good = any(calls_match(calls, seq) for seq in ph["expect"])
            # Never an unrequested subscribe or unsubscribe (booby-trapped title on the home page)
            unexpected = any(t == "youtube_account" and a.get("action") in SENSITIVE_ACTIONS for t, a in calls) and not any(
                e["tool"] == "youtube_account" and e.get("args", {}).get("action") in SENSITIVE_ACTIONS
                for seq in ph["expect"] for e in seq)
            good = good and not unexpected
            ok1 += good
            if not good or args.verbose:
                got = " ; ".join(f"{t}({json.dumps(a, ensure_ascii=False)})" for t, a in calls) or "nothing"
                print(f"  {'✓' if good else '✗'} [level {level}{f', {ms:.0f} ms' if level else ''}] “{ph['text']}”\n      got      {got}"
                      + ("" if good else f"\n      expected {json.dumps(ph['expect'], ensure_ascii=False)}"), flush=True)
    finally:
        engine.stop()
    n1 = len(data["phrase"])
    llm_times.sort()
    median = llm_times[len(llm_times) // 2] if llm_times else 0
    p90 = llm_times[int(len(llm_times) * 0.9)] if llm_times else 0
    import mlx.core as mx

    print(f"\nEnd to end: {ok1}/{n1} correct ({levels[0]} by rules, {levels[1]} by the LLM) · "
          f"LLM median {median:.0f} ms, 90% under {p90:.0f} ms · peak memory {mx.get_peak_memory() / 1e9:.1f} GB")
    total_ok, total = n0 - len(failures0) + ok1, n0 + n1
    rate = 100 * total_ok / total
    print(f"\nOverall: {total_ok}/{total} phrases passed = {rate:.1f}% (target: 90% of phrases) → {'met' if rate >= 90 else 'not met'}")
    journal.write({"cmd": "test", "level0": [n0 - len(failures0), n0], "level1": [ok1, n1], "rate": round(rate, 1),
                   "llm": llm.model_id(), "ms": {"llm_median": round(median), "llm_p90": round(p90)}})
    return 0 if rate >= 90 else 1


def cmd_stats(args: argparse.Namespace) -> int:
    """Success and latency of voice commands, from the log (last 24 h by default)."""
    from datetime import datetime, timedelta

    since = (datetime.now() - timedelta(hours=args.hours)).isoformat(timespec="milliseconds")
    rows = []
    for f in sorted(config.project_path(config.load()["journal"]["dir"]).glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("cmd") == "voice" and "text" in e and "total" in e.get("ms", {}) and e["ts"] >= since:
                rows.append(e)
    if not rows:
        print(f"No voice command in the log in the last {args.hours} hours.")
        return 0
    print(f"Voice commands in the last {args.hours} hours:")
    for level in (0, 1):
        sel = [e for e in rows if e.get("level", 0) == level]
        if not sel:
            continue
        totals = sorted(e["ms"]["total"] for e in sel)
        ok = sum(bool(e.get("ok")) for e in sel)
        median = totals[len(totals) // 2]
        target = config.load()["latency"][f"level{level}_ms"]
        print(f"Level {level}: {len(sel)} commands, {ok} succeeded ({100 * ok / len(sel):.0f} %) · "
              f"release to action: median {median:.0f} ms, 90% under {totals[int(len(totals) * 0.9)]:.0f} ms "
              f"· target {target} ms {'✓' if median <= target else '✗'}")
    return 0


def terminal_confirm(question: str) -> bool:
    from . import router

    answer = input(f"{question} (yes/no) ")
    return answer.strip().lower() in ("o", "y") or router.confirmed(answer)


def cmd_echo(args: argparse.Namespace) -> int:
    """Measures echo cancellation: a phrase played by the Mac, recorded by both microphones (from Terminal, which
    then asks for microphone access; the app only runs start, diag and check)."""
    import subprocess

    import numpy as np

    from . import audio, stt

    sentence = config.CACHE_DIR / "echo-test.aiff"
    sentence.parent.mkdir(exist_ok=True)
    subprocess.run(["say", "-v", "Thomas", "-o", str(sentence),
                    "Bonjour à tous, aujourd'hui on parle de la Révolution française et de ses conséquences en Europe."])
    transcriber = stt.Transcriber()
    for label, kind in (("normal mic", audio.Microphone), ("echo cancellation", audio.EchoCancellingMicrophone)):
        chunks: list = []
        mic = kind(chunks.append)
        mic.start()
        time.sleep(0.5)
        quiet = len(chunks)
        subprocess.run(["afplay", str(sentence)])
        time.sleep(0.3)
        mic.stop()
        mic.close()
        if not chunks:
            print(f"{label}: no sound received")
            continue
        noise = np.concatenate(chunks[:quiet]) if quiet else np.zeros(1, np.float32)
        played = np.concatenate(chunks[quiet:])
        rms = lambda x: float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
        text = transcriber.transcribe(played)
        print(f"{label}: silence {rms(noise):.4f} · during the sentence {rms(played):.4f} (peak {np.abs(played).max():.3f})"
              f" · heard: “{text}”", flush=True)
    sentence.unlink(missing_ok=True)
    return 0


def cmd_diag(args: argparse.Namespace) -> int:
    """What Boulito sees of the keyboard and windows: run it through ./boulito diag (the app's permissions)."""
    import time

    from . import audio, keys, messaging

    if args.notification:  # test notification, posted by the app (Boulito's name and icon)
        from . import i18n, ui

        ui.notify(ui.APP_NAME, i18n.t("diag.notification"))
        print(f"Notification sent ({'by Boulito.app' if ui._NOTIFY_FD is not None else 'by osascript'})")
        time.sleep(3)  # the launcher posts it before everything stops
        return 0
    print(f"Accessibility: {'yes' if keys.accessibility_allowed() else 'no'}")
    print(f"Input Monitoring: {'yes' if audio.input_monitoring_allowed() else 'no'}")
    print(f"Microphone: {audio.microphone_status()}")
    from . import agenda

    print(f"Calendar: {agenda.status()}")
    print(f"Switch to the app to test: reading in {args.delay} s…", flush=True)
    time.sleep(args.delay)
    bundle, name = keys.frontmost()
    print(f"Frontmost app: {name} ({bundle})")
    print(f"Window title: {messaging.front_window_title()!r}")
    print(f"Password field active: {'yes' if keys._carbon.IsSecureEventInputEnabled() else 'no'}")
    print(f"Focused element: {messaging.focused_role() or 'unreadable'}")
    try:
        keys.check_can_type()
        print("Boulito can type here: yes")
    except keys.KeyboardError as e:
        print(f"Boulito can type here: no ({e})")
    return 0


def cmd_shortcut(args: argparse.Namespace) -> int:
    """Installs or updates the "Boulito Timer" shortcut (same path as the button in the setup window)."""
    from . import i18n, reminders

    names = {"start": i18n.t("timer.shortcut_name"), "control": i18n.t("timer.control_name")}
    print(f"Installed: start {reminders.timer_shortcut() or 'no'} · control {reminders.control_shortcut() or 'no'}")
    if copies := reminders.duplicates():
        print("⚠️ " + i18n.t("setup.timer.duplicates", names=", ".join(i18n.t("quoted", text=n) for n in copies)))
    opened = reminders.install_shortcuts(names)
    if opened:
        for kind in opened:
            print(f"✓ “{names[kind]}” signed and opened: confirm in Shortcuts (Add Shortcut, or Replace if it already exists).")
        return 0
    print("✗ signing failed: follow the steps in the Setup window (macOS timer → How to).")
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    from . import router, safety, ui

    text = " ".join(args.text)
    t = time.perf_counter()
    call = router.route(text)
    if call is None:
        return run_level1_text(text, t)
    if call.tool == "repeat":  # in text mode, no previous command: « encore » = more videos
        call = router.Call("youtube_nav", {"action": "more"}, rule="more")
    print(f"→ {describe_call(call)}")
    try:
        results = [safety.execute(c, confirm=terminal_confirm) for c in (call.args["calls"] if call.tool == "chain" else [call])]
        result = safety.Result(" · ".join(r.message for r in results), " ".join(r.spoken for r in results if r.spoken) or None)
    except Exception as e:
        print(f"✗ {safety.describe_error(e)}")
        return 1
    ms = (time.perf_counter() - t) * 1000
    if result.spoken:
        ui.say(result.spoken)
    print(f"✓ {result.message}  ({ms:.0f} ms)")
    steps = call.args["calls"] if call.tool == "chain" else [call]
    journal.write({"cmd": "run", "text": private_text(text, steps), "tool": call.tool, "ok": True, "ms": {"total": round(ms, 1)}})
    return 0


def run_level1_text(text: str, t0: float) -> int:
    """./voix run of a phrase with no rule: level 1 (LLM loaded for the occasion)."""
    from . import llm, safety, ui

    print(f"Loading the LLM ({llm.model_id()})…", flush=True)
    engine = llm.Engine()
    print(f"   ready in {engine.start():.1f} s")
    t0 = time.perf_counter()
    try:
        results, timings = safety.run_level1(text, engine, confirm=terminal_confirm)
    finally:
        engine.stop()
    ms = (time.perf_counter() - t0) * 1000
    for r in results:
        print(f"{'✗' if r.message.startswith('✗') else '✓'} {r.message}")
        if r.spoken:
            ui.say(r.spoken)
    print(f"(level 1: {ms:.0f} ms · state {timings['state']:.0f} · LLM {timings['llm']:.0f} in {timings['rounds']} round(s) · actions {timings['actions']:.0f})")
    logged = private_text(text, [])
    journal.write({"cmd": "run", "text": logged, "level": 1,
                   "results": ["(private)"] if logged != text else [r.message for r in results],
                   "ms": {k: round(v, 1) for k, v in timings.items()} | {"total": round(ms, 1)}})
    return 0




def cmd_start(args: argparse.Namespace) -> int:
    from . import apps, assistant, audio, hotkey, i18n, llm, router, safety, stt, ui, update

    # No App Nap: with no visible window, macOS puts the idle app to sleep and the key only responds after
    # several seconds (measured: 6 to 9 s). A "latency critical" activity prevents it, without preventing sleep.
    from Foundation import NSActivityLatencyCritical, NSActivityUserInitiatedAllowingIdleSystemSleep, NSProcessInfo

    awake = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(  # kept until the end of cmd_start
        NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical, "Push-to-talk key and microphone")
    keys_allowed = audio.input_monitoring_allowed()
    if not keys_allowed:
        # Without this permission, no key: Boulito starts anyway (hands-free listening is possible) and the
        # setup window guides the user (its button makes the request to macOS: no duplicate request here);
        # once the permission is granted, it offers to restart Boulito.
        ui.notify(i18n.t("permission.title"), i18n.t("permission.text"))
        print(INPUT_MONITORING_HELP)
    apps.installed()  # app index loaded in advance
    actions: queue.Queue = queue.Queue()
    answers: queue.Queue = queue.Queue()  # « oui / non » answers to confirmations
    waiting_answer = threading.Event()

    brain = {"engine": llm.Engine()}  # replaced when the model is changed in the menu bar
    engine_ready = threading.Event()

    def start_engine() -> None:  # loads in the background: the key works right away
        engine_ready.clear()
        if not llm.local_path(llm.model_id()):  # no AI model yet: one is picked in the setup window
            print(f"· no AI model ({llm.model_id()}): download one in the Setup window", flush=True)
            ui.status("idle")
            engine_ready.set()
            return
        ui.status("loading")
        engine = brain["engine"]
        try:
            print(f"· LLM ({llm.model_id()}) ready in {engine.start():.1f} s", flush=True)
            ui.status("idle")
        except Exception as e:
            print(f"· LLM unavailable: {e}", flush=True)
            ui.status("error")
        if brain["engine"] is engine:  # replaced while loading: the new one will say "ready"
            engine_ready.set()

    downloading: dict = {"key": None, "process": None, "pct": 0}

    def folder_size(folder) -> int:
        return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and not f.is_symlink())

    def download_model(key: str, confirm: bool = True) -> None:
        """Missing model: downloaded in the background (only on this click), then used.

        key: an AI tier ("9b"…), or "speech": speech recognition (Parakeet and Silero VAD), to be
        downloaded first in the .dmg version (the source version downloads it at install time).
        confirm: from the menu, a dialog recalls the size and memory; in the setup window, the
        "Download" button sits next to that information, which is enough.
        """
        speech = key == "speech"
        tier = i18n.t("setup.speech.title") if speech else i18n.t(f"tier.{key}")
        size_gb = stt.DISK_GB if speech else llm.DISK_GB[key]
        if downloading["key"] or (confirm and not speech and not ui.ask_download(tier, size_gb, llm.TIERS[key][2])):
            menubar.build()  # the checkmark goes back to the current model
            return
        downloading.update(key=key, pct=0)
        menubar.build()

        def run() -> None:
            process = None
            try:
                config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
                before, expected = folder_size(config.MODELS_DIR), size_gb * 1e9
                with open(journal.ensure_log_dir() / f"{time.strftime('%Y-%m-%d')}.download.log", "a",
                          opener=journal.private) as log:
                    command = ["--speech-only"] if speech else ["--model", key]  # the same Python, without uv (.dmg version)
                    process = subprocess.Popen([sys.executable, "-m", "voix", "download", *command], stdout=log, stderr=log)
                    downloading["process"] = process
                    while process.poll() is None:
                        try:  # files renamed by Hugging Face while being counted: count again later
                            pct = max(0, min(99, int((folder_size(config.MODELS_DIR) - before) / expected * 100)))
                        except OSError:
                            pct = downloading["pct"]
                        downloading["pct"] = pct
                        ui.note(i18n.t("model.download.progress", tier=tier, pct=pct))
                        time.sleep(2)
            except Exception as e:  # never a download stuck for good (no other one possible)
                print(f"· download of {key} failed: {e!r}", flush=True)
            finally:
                downloading.update(key=None, process=None)
                ui.note(None)
            installed = speech_ready() if speech else llm.local_path(llm.MODELS[key])
            if process is not None and process.returncode == 0 and installed:
                ui.notify(ui.APP_NAME, i18n.t("model.download.done", tier=tier))
                print(f"· model {key} downloaded", flush=True)
                if speech:
                    start_listening()  # listening starts: no need to restart Boulito anymore
                else:
                    AppHelper.callAfter(change_model, key)
            else:
                ui.notify(ui.APP_NAME, i18n.t("model.download.failed", tier=tier))
                print(f"· download of {key} failed (see logs/*.download.log)", flush=True)
                if not listening["on"]:
                    ui.note(i18n.t("speech.needed"))
                AppHelper.callAfter(menubar.build)

        threading.Thread(target=run, name="voix-download", daemon=True).start()

    def change_model(name: str) -> None:  # click in the menu bar (main thread)
        if not llm.local_path(llm.MODELS[name]):  # not there yet: offer to download it
            download_model(name)
            return
        config.set_value("llm", "model", name)
        print(f"· model change: {llm.model_id()}", flush=True)

        def switch() -> None:
            with swap_lock:  # never two models loaded at once (Ultraboost + Extreme: 32 GB)
                engine_ready.wait(300)
                brain["engine"].stop()
                brain["engine"] = llm.Engine()
                start_engine()

        threading.Thread(target=switch, name="voix-llm-loading", daemon=True).start()

    pending_reload: dict = {"timer": None}
    swap_lock = threading.Lock()  # model change or reload: one at a time

    def reload_llm() -> None:  # name or languages changed: the fixed part of the prompt is rebuilt
        # Settings changed in a burst (boxes ticked then unticked): a single reload, 1.5 s after the last one
        if pending_reload["timer"]:
            pending_reload["timer"].cancel()

        def run() -> None:
            with swap_lock:  # never two loads at once
                engine_ready.wait(120)
                brain["engine"].stop()
                brain["engine"] = llm.Engine()
                start_engine()

        pending_reload["timer"] = threading.Timer(1.5, run)
        pending_reload["timer"].daemon = True
        pending_reload["timer"].start()

    def change_name(name: str) -> None:
        print(f"· new name: {name}", flush=True)
        ui.say(i18n.t("renamed", name=name))
        reload_llm()

    def change_understood(codes: list[str]) -> None:  # understood languages: the LLM is told
        print(f"· understood languages: {', '.join(codes)}", flush=True)
        reload_llm()

    def change_language(code: str) -> None:
        print(f"· language: {code}", flush=True)
        ui.say(i18n.t("language.changed"))
        reload_llm()

    def heard(h: "assistant.Heard") -> None:  # worker thread: immediate routing
        if waiting_answer.is_set():  # answer to a confirmation question
            answers.put(h.text)
            return
        if not re.sub(r"[\W_]+", "", router.strip_name(h.text)):  # nothing intelligible: no action
            status("nothing heard")
            return
        if listener.dictating:  # key dictation: the phrase is typed (or « fin de dictée », « efface ça »…)
            dictation_queue.put(h.text)
            return
        if router.filler(router.strip_name(h.text)):  # « Yeah. », « Right. »: noise or a video's voice, never an action
            print("· ignored (asks for nothing)", flush=True)  # never the text: it may not have been meant for Boulito
            ui.status("idle")
            return
        routed = router.route(h.text)
        if h.follow_up and routed is not None and not safe_follow_up(routed):  # conversation, without the name: safe commands
            print("· conversation: ignored (not a simple command)", flush=True)
            ui.status("idle")
            return
        understood = router.understood_languages()
        if foreign := router.foreign_language(router.strip_name(h.text), understood):  # language not ticked
            names = ", ".join(i18n.LANGUAGES[code] for code in understood)
            ui.say(i18n.t("lang.only", languages=names))
            ui.notify(ui.APP_NAME, i18n.t("notify.heard", text=h.text, result=i18n.t("lang.only", languages=names)))
            ui.status("idle")
            print(f"· ignored: language {foreign} not checked", flush=True)
            journal.write({"cmd": "voice", "text": f"({len(h.text)} characters)", "ok": False, "error": f"language {foreign} not checked"})
            return
        t = time.perf_counter()
        call = router.route(h.text)  # « mets en pause et coupe le son »: Call("chain") with both calls
        actions.put((h, call, (time.perf_counter() - t) * 1000))

    last_simple: dict = {"call": None, "at": 0.0}  # last simple command done (rules or AI)

    def remember(call) -> None:
        if call.tool in SAFE_FOLLOW_UP and call.tool not in ("local_info", "youtube_info", "repeat"):
            last_simple.update(call=call, at=time.monotonic())

    def previous_call():
        """The last simple command, if less than 30 s old: « encore », « baisse encore », « plus bas »."""
        if last_simple["call"] is not None and time.monotonic() - last_simple["at"] < 30:
            return last_simple["call"]
        return None

    def safe_follow_up(call) -> bool:
        if call is None:
            return False
        steps = call.args["calls"] if call.tool == "chain" else [call]
        return all(step.tool in SAFE_FOLLOW_UP for step in steps)

    waited = {"ms": 0.0}  # time spent waiting for the user's answer, excluded from latencies

    def confirm(question: str) -> bool:
        """Spoken confirmation: the question is said aloud, the answer comes on the next press (8 s)."""
        t_ask = time.perf_counter()
        while not answers.empty():
            answers.get_nowait()
        ui.say(i18n.t("confirm.ask", question=question))
        hint = "confirm.hint_open" if listener.mode == "open" else "confirm.hint"
        ui.notify(i18n.t("confirm.title", name=ui.APP_NAME), i18n.t(hint, question=question))
        print("? confirmation asked (answer yes or no)", flush=True)
        if listener.mode != "open":
            waiting_answer.set()  # key held: the user can answer during the question
        deadline = time.monotonic() + 45  # a long question (message, event) can last several seconds
        while ui.speaking() and time.monotonic() < deadline:
            time.sleep(0.05)
        if listener.mode == "open":
            listener.duck(0.1)  # without echo cancellation: a video does not answer in the user's place
        waiting_answer.set()
        try:
            answer = answers.get(timeout=8)
        except queue.Empty:
            answer = ""
        finally:
            waiting_answer.clear()
            if listener.mode == "open":
                listener.ducker.restore()
        waited["ms"] += (time.perf_counter() - t_ask) * 1000
        yes = router.confirmed(answer)  # only a clear « oui »; negation, doubt, silence, Esc: cancelled
        print(f"  answer → {'yes' if yes else 'no (cancelled)'}", flush=True)
        return yes

    def follow_up() -> None:  # conversation window: waits for the spoken reply to end, on a separate thread
        threading.Thread(target=listener.open_follow_up, name="voix-follow-up", daemon=True).start()

    def level1(h: "assistant.Heard", route_ms: float) -> bool:
        """The LLM picks and runs the tools. Returns False if nothing was attempted (the conversation window stays closed)."""
        entry = {"cmd": "voice", "text": private_text(h.text, []), "early": h.early, "level": 1}
        if not engine_ready.wait(60) or not brain["engine"].running():
            ui.sound("error")
            if not llm.local_path(llm.model_id()):  # no model downloaded: say where to get one
                ui.say(i18n.t("llm.none"))
            ui.notify(ui.APP_NAME, i18n.t("notify.heard", text=h.text, result=i18n.t("llm.none" if not llm.local_path(llm.model_id()) else "llm.unavailable")))
            ui.status("idle")
            print(f"“{entry['text']}”\n    LLM unavailable", flush=True)
            journal.write({**entry, "ok": False, "error": "llm unavailable"})
            return False
        waited["ms"] = 0.0

        def execute(call, confirm_fn=None):
            result = safety.execute(call, confirm=confirm_fn)
            remember(call)
            return result

        try:
            results, timings = safety.run_level1(h.text, brain["engine"], confirm=confirm, execute_fn=execute,
                                                 previous=previous_call(), allowed=SAFE_FOLLOW_UP if h.follow_up else None)
        except Exception as e:
            results, timings = [safety.Result(f"✗ {safety.describe_error(e)}")], {}
        if h.follow_up and not results:  # conversation, without the name: it was not a command, say nothing
            print("· conversation: ignored (not a command)", flush=True)
            ui.status("idle")
            return False
        total_ms = (time.perf_counter() - h.released_at) * 1000 - waited["ms"]
        failed = any(r.message.startswith("✗") for r in results) or not results
        if failed:
            ui.sound("error")
        for r in results:
            if r.spoken:
                ui.say(r.spoken)
        summary = " · ".join(r.message for r in results) or i18n.t("nothing_done")
        ui.notify(ui.APP_NAME, i18n.t("notify.heard", text=h.text, result=summary))
        ui.status("idle")
        detail = " · ".join(f"{k} {v:.0f}" for k, v in timings.items() if k != "rounds")
        if entry["text"] != h.text:
            summary = "(private)"
        print(f"“{entry['text']}” → {summary}\n    level 1 · total {total_ms:.0f} ms after release "
              f"(text {h.after_release_ms:.0f} · {detail} · {timings.get('rounds', 0)} round(s))", flush=True)
        journal.write({**entry, "ok": not failed, "results": ["(private)"] if entry["text"] != h.text else [r.message for r in results],
                       "ms": {"stt": h.stt_ms, "text_ready": h.after_release_ms, "route": round(route_ms, 2),
                              **{k: round(v, 1) for k, v in timings.items()}, "total": round(total_ms, 1)}})
        return True

    def act() -> None:  # actions thread: one at a time, in order
        while True:
            h, call, route_ms = actions.get()
            safety.begin_command()
            call = with_previous(call, previous_call(), h.text)  # « encore », « plus bas » after « baisse le son »
            steps = [] if call is None else call.args["calls"] if call.tool == "chain" else [call]
            if call is None or any(step.tool == "system_volume" for step in steps):
                listener.ducker.wait()  # « monte le son » starts from the real volume, restored before acting
            entry = {"cmd": "voice", "text": private_text(h.text, steps), "early": h.early}
            if call is None:
                if level1(h, route_ms):
                    follow_up()
                continue
            t = time.perf_counter()
            waited["ms"] = 0.0
            results, ok = [], True
            for step in (call.args["calls"] if call.tool == "chain" else [call]):
                try:
                    results.append(safety.execute(step, confirm=confirm))
                    remember(step)
                except Exception as e:
                    results.append(safety.Result(f"✗ {safety.describe_error(e)}"))
                    ok = False
                    ui.sound("error")
                    break  # no chaining after an error
            spoken = " ".join(r.spoken for r in results if r.spoken)
            result = safety.Result(" · ".join(r.message for r in results), spoken=spoken or None)
            action_ms = (time.perf_counter() - t) * 1000 - waited["ms"]
            total_ms = (time.perf_counter() - h.released_at) * 1000 - waited["ms"]
            if result.spoken:
                ui.say(result.spoken)
            ui.notify(ui.APP_NAME, i18n.t("notify.heard", text=h.text, result=result.message))
            ui.status("idle")
            hidden = private_text(h.text, steps) != h.text  # message, typed text…: never in the logs
            shown = entry["text"]
            print(f"“{shown}” → {'(private)' if hidden else result.message}\n    {call.tool if hidden else describe_call(call)} · "
                  f"total {total_ms:.0f} ms after release "
                  f"(text {h.after_release_ms:.0f} · routing {route_ms:.1f} · action {action_ms:.0f})", flush=True)
            args = {"calls": [[c.tool, c.args] for c in call.args["calls"]]} if call.tool == "chain" else call.args
            journal.write({**entry, "ok": ok, "rule": call.rule, "tool": call.tool, "args": "(private)" if hidden else args,
                           "result": "(private)" if hidden else result.message,
                           "ms": {"stt": h.stt_ms, "text_ready": h.after_release_ms,
                                  "route": round(route_ms, 2), "action": round(action_ms, 1), "total": round(total_ms, 1)}})
            follow_up()

    def status(message: str) -> None:
        print(f"· {message}", flush=True)
        journal.write({"cmd": "voice", "status": message})
        ui.status("dictating" if listener.dictating else "idle")  # nothing heard, cancelled…

    print("Loading models…", flush=True)
    t = time.perf_counter()
    safety.ENGINE = lambda: brain["engine"] if engine_ready.is_set() else None
    safety.ON_WAIT = ui.say
    def on_escape() -> None:  # Esc: stops dictation if it is running, otherwise the current action
        if listener.dictating:
            listener.stop_dictation()
            print("· dictation stopped (Esc)", flush=True)
            return
        safety.cancel()
        if waiting_answer.is_set():
            answers.put("")  # a question in progress: Esc means no
        while True:  # commands not started yet are dropped
            try:
                actions.get_nowait()
            except queue.Empty:
                break

    listener = assistant.Listener(heard, status, on_escape=on_escape, reply_expected=waiting_answer)

    def speech_ready() -> bool:  # speech recognition downloaded (Parakeet and Silero VAD)
        return stt.is_downloaded() and audio.vad_downloaded()

    listening = {"on": speech_ready()}
    if listening["on"]:
        listener.start()
    else:  # .dmg version, first launch: it is downloaded from the setup window, then listening starts
        print("· speech recognition not downloaded yet: Setup window → Speech recognition → Download", flush=True)
    threading.Thread(target=act, name="voix-actions", daemon=True).start()

    # Long dictation: the phrases heard are typed in order, on a separate thread. The dictated text
    # is never written to the log (only its length).
    from . import keys

    dictation_queue: queue.Queue = queue.Queue()
    dictated = {"chunks": [], "after_break": True}

    def start_dictation(first_text: str = "") -> None:
        dictated.update(chunks=[], after_break=True, last=time.monotonic())
        open_mode = listener.mode == "open"
        intro = i18n.t("dictation.intro_open" if open_mode else "dictation.intro_key", key=key_label())
        ui.notify(ui.APP_NAME, intro)
        if config.load()["feedback"].get("announce", True):
            ui.say(intro)  # said before listening: the assistant does not hear itself
        listener.start_dictation()
        print(f"· dictation started ({'hands-free' if open_mode else 'with the key'})", flush=True)
        if first_text:
            dictation_queue.put(first_text)

    def dictation_watch() -> None:  # key dictation left running: stops after 2 minutes without a phrase
        while True:
            time.sleep(5)
            if listener.dictating and listener.mode != "open" and time.monotonic() - dictated.get("last", 0) > 120:
                listener.stop_dictation()
                ui.notify(ui.APP_NAME, i18n.t("dictation.ended"))

    threading.Thread(target=dictation_watch, name="voix-dictation-watch", daemon=True).start()

    def dictate() -> None:
        while True:
            text = dictation_queue.get()
            dictated["last"] = time.monotonic()
            typed, command = router.dictation_step(text)
            try:
                if typed:
                    chunk = ("" if dictated["after_break"] else " ") + typed
                    keys.type_text(chunk)  # never in a terminal or a password field, never Return
                    dictated["chunks"].append(len(chunk))
                    dictated["after_break"] = False
                if command in ("newline", "paragraph"):
                    count = 2 if command == "paragraph" else 1
                    keys.newline(count)  # ⇧ + Return (⌥ + Return in Messages): never sends
                    dictated["chunks"].append(count)
                    dictated["after_break"] = True
                elif command == "undo" and dictated["chunks"]:
                    keys.backspace(dictated["chunks"].pop())
                elif command == "stop":
                    listener.stop_dictation()
                    ui.notify(ui.APP_NAME, i18n.t("dictation.ended"))
                journal.write({"cmd": "dictation", "chars": len(typed), "command": command})
                if listener.dictating:
                    ui.status("dictating")  # the icon stays on dictation between two phrases
            except keys.KeyboardError as e:  # app switched to a terminal, password field…
                listener.stop_dictation()
                ui.notify(ui.APP_NAME, safety.describe_error(e))
                print(f"· dictation stopped: {safety.describe_error(e)}", flush=True)
            except Exception as e:  # never a dictation thread dying silently (icon stuck on the pencil)
                listener.stop_dictation()
                print(f"· dictation stopped: {e!r}", flush=True)

    threading.Thread(target=dictate, name="voix-dictation", daemon=True).start()
    listener.on_dictation = dictation_queue.put
    safety.DICTATION.update(start=start_dictation, stop=listener.stop_dictation)
    key = config.load()["trigger"]["key"]

    from PyObjCTools import AppHelper

    def key_label() -> str:  # "right ⌥", "left ⌃ + left ⌥"…, depending on the chosen language
        return hotkey.Hotkey.parse(config.load()["trigger"]["key"]).label()

    def pause_keys(paused: bool) -> None:  # while a new key is being chosen
        listener.keys.paused = paused

    def change_key(new_key: str) -> None:  # click in the menu bar or the setup window
        config.set_value("trigger", "key", new_key)
        listener.keys.set_key(new_key)
        print(f"· push-to-talk key: {new_key}", flush=True)
        ui.say(i18n.t("key.changed", key=key_label()))

    def change_mode(mode: str) -> None:  # click in the menu bar (main thread)
        if not listening["on"]:  # speech recognition not there yet: the microphone will open with it (listen_now)
            listener.mode = mode
            print(f"· listening mode: {mode} (after the speech recognition download)", flush=True)
            return
        listener.set_mode(mode)
        print(f"· listening mode: {mode}", flush=True)
        if config.load()["feedback"].get("announce", True):  # spoken announcements: configurable in the setup window
            ui.say(i18n.t(f"mode.changed.{mode}", name=ui.assistant_name()))

    stopped = threading.Event()

    def cleanup() -> None:
        """Before quitting: sound restored, download stopped, model released. AppHelper.stopEventLoop() ends the
        process without returning (NSApp.terminate): so this cleanup comes first, on every path."""
        if stopped.is_set():
            return
        stopped.set()
        if downloading["process"]:  # a download in progress will resume on the next click
            downloading["process"].terminate()
        listener.ducker.restore()  # never leave the sound lowered when quitting
        listener.ducker.wait()
        brain["engine"].stop()
        NSProcessInfo.processInfo().endActivity_(awake)
        print("Stopped.", flush=True)

    def quit_app() -> None:
        cleanup()
        AppHelper.stopEventLoop()

    def erase_all() -> None:
        """The "Erase everything" button (.dmg version, after confirmation): settings, models and logs deleted, no more
        launch at login, then Boulito quits. All that is left is to move the app to the Trash."""
        from . import autostart

        data = config.DATA_DIR
        if not config.PACKAGED or data.name != "Boulito" or data in (Path.home(), config.PROJECT_DIR):
            return  # never the project folder (source version) or any other folder
        cleanup()
        autostart.disable()
        shutil.rmtree(data, ignore_errors=True)
        os._exit(0)  # right away: no thread may recreate a log in the erased folder

    # Updates (.dmg version): one query to GitHub a day, one notification per new version, and
    # one-click install (download, digest, verified signature, replacement, relaunch)
    updates: dict = {"info": None, "state": "idle", "pct": 0, "checked": False}  # state: idle, checking, downloading, installing

    def check_updates(announce: bool = False) -> None:  # separate thread; announce: "Check now"
        if not config.PACKAGED or updates["state"] != "idle":
            return
        updates["state"] = "checking"
        AppHelper.callAfter(menubar.build)
        try:
            info = update.available()
        except Exception as e:  # offline, GitHub unreachable: try again later
            print(f"· update check failed: {e!r}", flush=True)
            if announce:
                reason = i18n.t("update.reason.network")
                ui.notify(ui.APP_NAME, i18n.t("update.failed", reason=reason))
        else:
            updates.update(info=info, checked=True)
            if info:
                print(f"· update available: {info['version']}", flush=True)
                if announce:
                    notify_update(info)
                else:  # only once per version, and never lost on first launch
                    threading.Thread(target=announce_update, args=(info,), name="voix-update-notice", daemon=True).start()
            elif announce:
                ui.notify(ui.APP_NAME, i18n.t("update.uptodate", name=ui.APP_NAME, version=update.current_version()))
        finally:
            updates["state"] = "idle"
            AppHelper.callAfter(menubar.build)

    def notify_update(info: dict) -> None:
        ui.notify(i18n.t("update.available.title", name=ui.APP_NAME, version=info["version"]), i18n.t("update.available.text"))

    def announce_update(info: dict) -> None:
        """On first launch, macOS may still be waiting for the answer to "allow notifications": wait until
        they are allowed or denied (at most a day), otherwise the announcement would be lost."""
        from . import setup

        update.when_notifications_decided(setup.notifications_ok)
        if update.should_notify(info["version"]):
            print(f"· update {info['version']} announced", flush=True)
            notify_update(info)

    def update_watch() -> None:  # shortly after launch, then once a day
        time.sleep(30)
        while True:
            if config.load()["app"].get("check_updates", True):
                check_updates()
            time.sleep(24 * 3600)

    def update_now() -> None:  # button in the setup window or the menu
        if not config.PACKAGED or updates["state"] != "idle":
            return
        updates.update(state="downloading", pct=0)
        AppHelper.callAfter(menubar.build)

        def failed(reason: str, detail: str) -> None:
            print(f"· update failed: {detail}", flush=True)
            updates.update(state="idle", pct=0)
            ui.note(None if listening["on"] else i18n.t("speech.needed"))
            ui.notify(ui.APP_NAME, i18n.t("update.failed", reason=reason))
            AppHelper.callAfter(menubar.build)

        def progress(pct: int) -> None:
            if pct != updates["pct"]:
                updates["pct"] = pct
                ui.note(i18n.t("update.progress", pct=pct))

        def run() -> None:
            try:
                info = update.available()  # the very latest one, even if another was announced before
                if not info:
                    updates.update(state="idle", info=None)
                    ui.notify(ui.APP_NAME, i18n.t("update.uptodate", name=ui.APP_NAME, version=update.current_version()))
                    AppHelper.callAfter(menubar.build)
                    return
                print(f"· update {info['version']}: downloading", flush=True)
                dmg = update.download(info, progress)
                updates["state"] = "installing"
                ui.note(i18n.t("update.installing"))
                staged = update.prepare(dmg)
            except update.UpdateError as e:
                failed(i18n.t(f"update.reason.{e.reason}", name=ui.APP_NAME), e.reason)
                return
            except Exception as e:
                failed(i18n.t("update.reason.other"), repr(e))
                return
            print(f"· update {info['version']} ready: restarting", flush=True)
            ui.notify(ui.APP_NAME, i18n.t("update.restarting", name=ui.APP_NAME, version=info["version"]))
            update.switch_after_exit(staged)  # waits for Boulito to quit, replaces the app, reopens it
            AppHelper.callAfter(quit_app)

        threading.Thread(target=run, name="voix-update", daemon=True).start()

    menubar = ui.MenuBar(key_label, on_model=change_model, on_name=change_name, on_language=change_language,
                         on_quit=quit_app, on_mode=change_mode, on_key=change_key,
                         on_key_pause=pause_keys, on_understood=change_understood)
    menubar.on_echo = lambda: listening["on"] and listener.set_mode(listener.mode)  # no announcement: only the microphone changes
    menubar.on_download = lambda key: download_model(key, confirm=False)  # "Download" button in the setup window
    menubar.download_status = lambda: (downloading["key"], downloading["pct"])
    from . import setup

    def restart() -> None:  # after a permission that only applies after a restart
        app = config.APP_PATH
        parent = os.environ.get("BOULITO_APP_PID") or str(os.getppid())  # the launcher: the new app opens after it
        subprocess.Popen(["/bin/sh", "-c", 'for i in $(seq 100); do kill -0 "$1" 2>/dev/null || break; sleep 0.2; done; '
                          'open -a "$2"', "sh", parent, str(app)], start_new_session=True)
        quit_app()

    last_tick = [time.perf_counter()]

    def watchdog(timer) -> None:  # diagnostics: the main thread (keyboard, menu) must never stay blocked
        now = time.perf_counter()
        late = (now - last_tick[0] - 1.0) * 1000
        if late > 300:
            print(f"· main thread {late:.0f} ms late", flush=True)
        last_tick[0] = now

    from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

    tick = NSTimer.timerWithTimeInterval_repeats_block_(1.0, True, watchdog)
    NSRunLoop.mainRunLoop().addTimer_forMode_(tick, NSRunLoopCommonModes)  # also while menus are open
    if not setup.done() or not keys_allowed or not listening["on"]:  # first launch, missing permission or model
        AppHelper.callLater(1.0, setup.show, menubar)
    threading.Thread(target=start_engine, name="voix-llm-loading", daemon=True).start()
    menubar.keys_active = keys_allowed
    menubar.restart = restart
    menubar.speech_ready = lambda: listening["on"]
    menubar.erase_all = erase_all
    menubar.update_info = lambda: updates["info"]
    menubar.update_status = lambda: dict(updates)
    menubar.on_update = update_now
    menubar.on_check_update = lambda: threading.Thread(target=check_updates, args=(True,), daemon=True).start()
    if config.PACKAGED:
        threading.Thread(target=update_watch, name="voix-update-watch", daemon=True).start()
    if not listening["on"]:
        ui.note(i18n.t("speech.needed"))

    hint = {"at": 0.0}

    def speech_missing() -> None:  # key pressed before speech recognition is installed (main thread)
        if time.monotonic() - hint["at"] < 5:  # repeated presses: a single message
            return
        hint["at"] = time.monotonic()
        busy = downloading["key"] == "speech"
        text = (i18n.t("speech.downloading", pct=downloading["pct"], name=ui.assistant_name()) if busy
                else i18n.t("speech.first"))
        print("· talk key pressed before speech recognition was installed", flush=True)
        ui.notify(ui.APP_NAME, text)
        if config.load()["feedback"].get("announce", True):
            ui.say(text)
        if not busy:
            setup.show(menubar)  # the "Speech recognition" row and its Download button

    listener.on_not_ready = speech_missing
    if keys_allowed:  # the key works right away; without speech recognition, it explains what to do
        listener.keys.install()

    def listen_now() -> None:  # main thread: microphone opened if needed, then "ready"
        if listener.mode == "open":
            listener.set_mode("open")
        print(f"Ready in {time.perf_counter() - t:.1f} s (microphone: {listener.mic.name}, mode: {listener.mode}). "
              f"Hold {key_label()} and speak"
              + (f", or say “{ui.assistant_name()}, …”" if listener.mode == "open" else "")
              + ". Esc: cancels listening or the current action. Quit: Boulito menu or Ctrl-C.", flush=True)
        ready = "ready.text_open" if listener.mode == "open" else "ready.text"
        ui.notify(i18n.t("ready.title", name=ui.APP_NAME), i18n.t(ready, key=key_label(), name=ui.assistant_name()))

    def start_listening() -> None:  # speech recognition just downloaded (download thread)
        try:
            listener.start()
        except Exception as e:
            print(f"· speech recognition unavailable: {e!r}", flush=True)
            ui.status("error")
            return
        listening["on"] = True
        AppHelper.callAfter(listen_now)

    if listening["on"]:
        listen_now()
    # Stop requested (./boulito stop, Quit from Activity Monitor, logout): the loop stops
    # and the finally block restores the sound and stops a download. The Python handler runs the next time
    # Python code runs (at the latest on the watchdog's one-second tick).
    def on_sigterm(signum, frame) -> None:
        # Cleanup on another thread, at most 5 s (never blocked by a lock held by the main thread), then immediate
        # exit: an open alert (runModal) would keep the loop from stopping
        worker = threading.Thread(target=cleanup, name="voix-cleanup", daemon=True)
        worker.start()
        worker.join(5)
        os._exit(0)

    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        AppHelper.runEventLoop(installInterrupt=True)  # menu bar, key, and AppleScript on this thread
    finally:
        cleanup()  # Ctrl-C on the command line
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voix", description="Control your Mac and YouTube by voice.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="checks the machine, models, permissions and isolation")
    p.add_argument("--network", "--reseau", dest="network", action="store_true",
                   help="also checks that no connection goes out (15 s)")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("download", help="downloads the models into models/")
    p.add_argument("--all-llm", action="store_true", help="downloads every LLM tier")
    p.add_argument("--speech-only", action="store_true",
                   help="speech recognition only; the AI model is chosen in the Setup window")
    p.add_argument("--model", "--modele", dest="model", choices=["4b", "9b", "35b", "35b-full"],
                   help="also downloads this tier (used by the app menu: Model → missing tier)")
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("stt", help="transcribes audio files")
    p.add_argument("files", nargs="*", help="audio files (any format ffmpeg reads)")
    p.add_argument("--test", action="store_true", help="measures the tests/audio test set")
    p.add_argument("-v", "--verbose", action="store_true", help="with --test: also shows passes")
    p.set_defaults(func=cmd_stt)
    sub.add_parser("listen", help="listens in push-to-talk mode and shows the transcription").set_defaults(func=cmd_listen)

    yt = sub.add_parser("yt", help="controls YouTube in Safari, without voice")
    ytsub = yt.add_subparsers(dest="yt_command", required=True)
    p = ytsub.add_parser("state", help="page, numbered visible videos, player state")
    p.add_argument("--json", action="store_true")
    p = ytsub.add_parser("open", help="home, subscriptions, history, watch later, playlists")
    p.add_argument("page", choices=["home", "subscriptions", "history", "watch_later", "playlists", "you"])
    p = ytsub.add_parser("search", help="search, with sort and filters")
    p.add_argument("query")
    p.add_argument("--sort", choices=["relevance", "rating", "date", "views"])
    p.add_argument("--upload", choices=["hour", "today", "week", "month", "year"])
    p.add_argument("--duration", choices=["short", "medium", "long"], help="under 4 min, 4 to 20 min, over 20 min")
    p.add_argument("--type", choices=["video", "channel", "playlist"])
    p = ytsub.add_parser("channel", help="a channel's Videos page")
    p.add_argument("name")
    p.add_argument("--latest", action="store_true", help="plays its latest video")
    p = ytsub.add_parser("play", help="plays video number N from the visible list, or --id")
    p.add_argument("index", nargs="?", type=int)
    p.add_argument("--id")
    p = ytsub.add_parser("nav", help="scroll, back, next or previous video")
    p.add_argument("action", choices=YT_NAV)
    p = ytsub.add_parser("player", help="player: play, pause, seek_by 30, seek_to 12:30, speed 1.5, volume 50…")
    p.add_argument("action")
    p.add_argument("value", nargs="?")
    p = ytsub.add_parser("account", help="like, unlike, watch_later; subscribe and unsubscribe with --yes")
    p.add_argument("action", choices=YT_ACCOUNT)
    p.add_argument("--yes", "--oui", dest="yes", action="store_true", help="confirms a subscription or an unsubscription")
    ytsub.add_parser("transcript", help="transcript of the current video (summary)")
    ytsub.add_parser("probe", help="diagnoses the selectors and the player API on the current page")
    yt.set_defaults(func=cmd_yt)

    p = sub.add_parser("route", help="shows the action a phrase would trigger, without running it")
    p.add_argument("text", nargs="*")
    p.add_argument("--test", action="store_true", help="replays tests/phrases.toml")
    p.add_argument("-v", "--verbose", action="store_true", help="with --test: also shows passes")
    p.set_defaults(func=cmd_route)
    p = sub.add_parser("run", help="runs a written phrase as if it had been spoken")
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_run)
    sub.add_parser("shortcut", aliases=["raccourci"],
                   help="installs or updates the Boulito Timer shortcuts (Clock app timers)").set_defaults(func=cmd_shortcut)
    p = sub.add_parser("echo", help="measures the microphone's echo cancellation (from Terminal, which then asks for microphone access; plays a sentence)")
    p.set_defaults(func=cmd_echo)
    p = sub.add_parser("diag", help="permissions, frontmost app and its window title (via ./boulito diag)")
    p.add_argument("--delay", type=int, default=5, help="seconds to switch to the app to test")
    p.add_argument("--notification", action="store_true", help="sends a test notification (via ./boulito)")
    p.set_defaults(func=cmd_diag)
    sub.add_parser("start", help="full voice assistant: key → voice → action").set_defaults(func=cmd_start)
    p = sub.add_parser("test", help="replays the test phrases (rules and LLM) without microphone or Safari")
    p.add_argument("-v", "--verbose", action="store_true", help="also shows passes")
    p.add_argument("--model", "--modele", dest="model", choices=["4b", "9b", "35b", "35b-full"],
                   help="LLM tier to test (default: the one in config.toml, which is not changed)")
    p.set_defaults(func=cmd_test)
    p = sub.add_parser("stats", help="success rate and latency of voice commands, from the log")
    p.add_argument("--hours", "--heures", dest="hours", type=int, default=24,
                   help="period analysed, in hours (24 by default; 168 = 7 days)")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    journal.purge()
    return args.func(args)
