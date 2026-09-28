"""Main loop: key or assistant's name → mic → VAD → Parakeet → text → router.

Three threads:
- main: keyboard (event tap) and sounds, starts and stops the mic;
- mic (AudioQueue): pushes each 32 ms chunk into the queue;
- work: VAD and transcription, in queue order. MLX is only used in this thread.

Push-to-talk: as soon as the VAD hears 300 ms of silence after speech, the sentence is
transcribed without waiting for the release; if nothing else is said, the text is already ready.

Open listening: the mic stays open and only sentences that start with the assistant's
name are kept (see OpenListening). The key still works.
"""

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import audio, config, hotkey, router, stt, ui


WAKE_UP = np.zeros(audio.SAMPLE_RATE // 4, np.float32)  # 250 ms of silence


def ms_since(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 1)


@dataclass
class Heard:
    text: str
    speech_s: float          # length of the audio sent to Parakeet
    stt_ms: float            # duration of the transcription kept
    after_release_ms: float  # from key release to text ready
    early: bool              # transcribed while the key is held, as soon as speech ends
    mic_start_ms: float      # mic start when the key is pressed
    released_at: float = 0.0 # release time (time.perf_counter)
    follow_up: bool = False  # said during the conversation window, without the name (rules only)


class OpenListening:
    """Open listening: keeps only what follows the assistant's name, wherever it is said.

    Like Siri or Alexa, the name is searched for continuously: every 0.3 s of speech, Parakeet transcribes the
    last 2 seconds, so a video or a conversation that talks without pausing does not keep it from being heard
    (checking only the start of each sentence would). As soon as the name is heard: sound, and the Mac's
    volume is lowered, then the command is listened to until silence (at most 12 s).

    Nothing is recorded or logged: without the name, only the last 2 seconds stay in memory, then
    are discarded, audio and text, without being displayed. Below the volume threshold, the VAD does not run.
    """

    WINDOW_S = 2.0       # audio transcribed to look for the name
    STEP_S = 0.3         # one search every 0.3 s of speech (a voice mixed with a video is not read every time)
    COMMAND_MAX_S = 7.0  # after the name is heard: latest end of the command (video that keeps talking)

    def __init__(self, transcriber: "stt.Transcriber", cfg: dict) -> None:
        a = cfg["audio"]
        self.transcriber = transcriber
        self.seg = audio.Segmenter(audio.Vad(), a["vad_threshold"], a.get("open_silence_ms", 600), a["pad_ms"])
        self.gate = a.get("open_gate", 0.006)
        self.max_s = a["max_seconds"]
        self.window = round(self.WINDOW_S * 1000 / audio.CHUNK_MS)
        self.step = round(self.STEP_S * 1000 / audio.CHUNK_MS)
        self.reset()

    def reset(self) -> None:
        self.seg.reset()
        self.named_at: int | None = None  # chunk where the command audio starts (name included)
        self.heard_at = 0                 # chunk where the name was heard
        self.window_rest: str | None = None  # what followed the name in the window where it was heard
        self.since_check = 0
        self.end_gate = self.gate         # after the name: below this level, it is no longer the user's voice
        self.quiet = 0                    # consecutive chunks below end_gate

    def _keep_window(self) -> None:
        """Without the name: only the last 2 seconds (and the padding) stay in memory."""
        seg = self.seg
        extra = len(seg.chunks) - (self.window + seg.pad_chunks)
        if extra > 0:
            del seg.chunks[:extra]
            seg.first = max(0, seg.first - extra)
            seg.last = max(0, seg.last - extra)

    def _command(self, speech: np.ndarray, ended_at: float, retries: int = 0) -> tuple | None:
        """Transcribes the sentence and keeps what follows the name. retries: if the name is missing, try again
        starting 0.5 s later (mixed with a video, the voice is not read the same way depending on its surroundings).

        If no attempt reads the name again (short isolated word transcribed empty or in Cyrillic: « Булито »), keep
        what the name search had read: « Boulito » alone arms listening, « Boulito pause » pauses."""
        step = audio.SAMPLE_RATE // 2
        for i in range(retries + 1):
            text = self.transcriber.transcribe(speech[i * step:])
            rest = router.wake_find(text)
            if rest is not None:
                break
        if rest is None and self.window_rest is not None:
            rest = self.window_rest
        stt_ms = ms_since(ended_at)
        self.reset()
        if rest is None:
            return None
        if not rest.strip(" .,!?"):
            return ("armed",)
        return ("command", rest, round(len(speech) / audio.SAMPLE_RATE, 2), stt_ms, ended_at)

    def add(self, chunk: np.ndarray, free: bool) -> tuple | None:
        """32 ms of audio. free: the name is not needed (yes/no answer, or right after « Boulito » alone).

        Returns ("name",) as soon as the name is heard, then ("command", text, speech duration, transcription ms,
        end time); ("armed",) if the name was said alone (the command follows); ("dropped",) if the name is
        not in the sentence after all. Otherwise None.
        """
        seg = self.seg
        seg.add(chunk, gate=self.gate)
        if not seg.has_speech:
            if len(seg.chunks) > seg.pad_chunks:  # silence: only keep the pre-speech padding
                del seg.chunks[0]
            return None
        if free:  # the whole sentence, without looking for the name
            if not seg.speech_ended and seg.seconds < self.max_s:
                return None
            ended_at = time.perf_counter()
            speech = seg.speech_audio()
            text = self.transcriber.transcribe(speech)
            stt_ms = ms_since(ended_at)
            self.reset()
            if not text.strip(" .,!?"):
                return None
            if not router.without_name(text).strip(" .,!?"):  # « Boulito » alone (during the conversation): the command follows
                return ("armed",)
            return ("command", text, round(len(speech) / audio.SAMPLE_RATE, 2), stt_ms, ended_at)
        if self.named_at is None:
            if seg.speech_ended:  # short sentence finished before a search: check it in full
                return self._command(seg.speech_audio(), time.perf_counter())
            self.since_check += 1
            if self.since_check < self.step:
                return None
            self.since_check = 0
            recent = np.concatenate(seg.chunks[-self.window:])
            rest = router.wake_find(self.transcriber.transcribe(recent))
            if rest is None:
                self._keep_window()
                return None
            self.window_rest = rest
            self.named_at = max(0, len(seg.chunks) - self.window)
            self.heard_at = len(seg.chunks)
            # End of the command tuned to the user's voice: a video still audible (quieter,
            # the Mac's sound is lowered and the echo removed) does not extend the sentence
            levels = [float(np.abs(c).max()) for c in seg.chunks[-self.window:]]
            self.end_gate = max(self.gate, 0.3 * float(np.percentile(levels, 90)))
            return ("name",)
        after_name = (len(seg.chunks) - self.heard_at) * audio.CHUNK_MS / 1000
        self.quiet = self.quiet + 1 if float(np.abs(chunk).max()) < self.end_gate else 0
        voice_ended = self.quiet >= seg.silence_chunks
        if not seg.speech_ended and not voice_ended and after_name < self.COMMAND_MAX_S:
            return None
        last = len(seg.chunks) - self.quiet if voice_ended else seg.last + 1
        end = min(len(seg.chunks), last + seg.pad_chunks)
        speech = np.concatenate(seg.chunks[self.named_at:end])
        return self._command(speech, time.perf_counter(), retries=3) or ("dropped",)


class Listener:
    ARMED_S = 6.0  # after the name said alone: the command can follow without repeating it

    def __init__(self, on_heard: Callable[[Heard], None], on_status: Callable[[str], None],
                 on_escape: Callable[[], None] | None = None, reply_expected: threading.Event | None = None) -> None:
        self.cfg = config.load()
        self.on_heard, self.on_status = on_heard, on_status
        self.mode = self.cfg["trigger"].get("mode", "push_to_talk")
        self.reply_expected = reply_expected or threading.Event()  # confirmation pending: the name is not needed
        from .system import Ducker

        self.ducker = Ducker(lambda: config.load()["audio"].get("duck", True))  # sound lowered while listening
        self.armed_until = 0.0
        self.name_signalled = False  # beep already played for the heard name
        self.follow_until = 0.0  # conversation mode: end of the listening window without the name
        self.dictating = False  # long dictation: each sentence is typed (see start_dictation)
        self.on_dictation: Callable[[str], None] | None = None
        self.ignore_release = False
        self.events: queue.Queue = queue.Queue()
        self.plain_mic = audio.Microphone(self._audio)
        self.echo_mic: audio.EchoCancellingMicrophone | None = None
        self.mic = self._mic_for(self.mode)
        trigger = self.cfg["trigger"]
        self.keys = hotkey.PushToTalk(trigger["key"], trigger["min_hold_ms"], self._press, self._release,
                                     self._cancel, on_escape)
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.on_not_ready: Callable[[], None] | None = None  # key pressed before speech recognition is available
        self.mic_start_ms = 0.0

    def duck(self, factor: float = 0.25) -> None:
        """Lowers the Mac's sound while listening, if "Lower the sound while listening" is checked (user's choice)."""
        self.ducker.duck(factor)

    def _audio(self, chunk: np.ndarray) -> None:
        self.events.put(("audio", chunk))

    def _mic_for(self, mode: str):
        """Open listening: mic with echo cancellation (the Mac's sound is removed), if it is checked;
        push-to-talk: normal mic (open only while the key is held, the sound is lowered)."""
        if mode != "open" or not config.load()["audio"].get("echo_cancel", True):
            return self.plain_mic
        if self.echo_mic is None:
            try:
                self.echo_mic = audio.EchoCancellingMicrophone(self._audio)
            except Exception as e:  # macOS without VoiceProcessingIO: normal mic
                print(f"· echo cancellation unavailable ({e}): normal mic", flush=True)
                return self.plain_mic
        return self.echo_mic

    # --- main thread -----------------------------------------------------

    def _press(self) -> None:
        if not self.ready.is_set() or self.error:  # speech recognition not installed yet (.dmg version)
            self.ignore_release = True  # mic closed: the user is notified, nothing else
            if self.on_not_ready:
                self.on_not_ready()
            return
        if self.dictating and self.mode == "open":  # hands-free dictation: the key stops it (like Esc)
            self.ignore_release = True
            self.stop_dictation()
            return
        ui.status("listening")
        self.events.put(("start", None))
        t = time.perf_counter()
        if self.mode != "open":  # in open listening, the mic is already open
            self.mic.start()
        self.mic_start_ms = ms_since(t)
        ui.sound("start")
        self.duck()

    def _release(self) -> None:
        if self.ignore_release:
            self.ignore_release = False
            return
        # The marker goes before the mic closes (100 to 150 ms): transcription starts right away
        self.events.put(("stop", time.perf_counter()))
        self.ducker.restore()
        if self.mode != "open":
            self.mic.stop()
        ui.sound("stop")
        ui.status("working")

    def _cancel(self, reason: str) -> None:
        if self.ignore_release:
            self.ignore_release = False
            return
        self.ducker.restore()
        if self.mode != "open":
            self.mic.stop()
        ui.status("idle")
        if reason == "Escape":
            ui.sound("cancel")
        self.events.put(("cancel", reason))

    def start(self) -> None:
        """Loads the models in the work thread and waits until they are ready."""
        threading.Thread(target=self._work, name="voix-work", daemon=True).start()
        self.ready.wait()
        if self.error:
            raise self.error

    def run(self) -> None:
        """Listens to the key until Ctrl-C (without the menu bar)."""
        self.keys.run()

    def set_mode(self, mode: str) -> None:
        """Push-to-talk (mic open while the key is held) or open listening (mic always open).

        Also called when echo cancellation is checked or unchecked: the mic switches immediately.
        """
        self.mode = mode
        self.events.put(("mode", mode))
        if self.armed_until or self.follow_until or self.name_signalled:  # listening in progress: sound restored, window closed
            self.armed_until = self.follow_until = 0.0
            self.name_signalled = False
            self.ducker.restore()
            ui.status("idle")
        mic = self._mic_for(mode)
        if mic is not self.mic:
            self.mic.stop()
            self.mic = mic
        if mode == "open":
            self.on_status("open listening: " + ("echo cancellation on" if mic is self.echo_mic else "normal mic"))
        if mode == "open":
            if not self.mic.running:
                self.mic.start()
        else:
            self.mic.stop()

    # --- work thread -----------------------------------------------------

    def _work(self) -> None:
        try:
            self.transcriber = stt.Transcriber()
            a = self.cfg["audio"]
            self.seg = audio.Segmenter(audio.Vad(), a["vad_threshold"], a["vad_silence_ms"], a["pad_ms"])
            self.open = OpenListening(self.transcriber, self.cfg)
            self.dictation_seg = audio.Segmenter(audio.Vad(), max(0.6, a["vad_threshold"]), 700, a["pad_ms"])  # stricter
            self.dictation_last = time.monotonic()
        except BaseException as e:
            self.error = e
            self.ready.set()
            return
        self.ready.set()
        active = False
        early = None  # (last speech chunk, text, duration) transcribed while the key is held
        while True:
            kind, value = self.events.get()
            try:
                if kind == "start":
                    self.open.reset()
                    self.seg.reset()
                    active, early = True, None
                    # Wakes the GPU while the user starts speaking: 60 ms instead of 90 at the end
                    self.transcriber.transcribe(WAKE_UP)
                elif kind == "dictation":
                    self.dictation_seg.reset()
                    self.open.reset()
                    self.dictation_last = time.monotonic()
                elif kind == "audio" and not active:
                    if self.dictating:
                        self._dictation_audio(value)
                    elif self.mode == "open":
                        self._open_audio(value)
                elif kind == "mode":
                    self.open.reset()
                    self.armed_until = 0.0
                elif kind == "audio" and active:
                    self.seg.add(value)
                    if self.seg.speech_ended and (early is None or early[0] != self.seg.last):
                        t = time.perf_counter()
                        early = (self.seg.last, self.transcriber.transcribe(self.seg.speech_audio()), ms_since(t))
                    if self.seg.seconds >= self.cfg["audio"]["max_seconds"]:
                        active = False
                        self._finish(time.perf_counter(), early)
                elif kind == "stop" and active:
                    active = False
                    self._finish(value, early)
                elif kind == "stop":  # released after a sentence already cut (maximum length): the icon returns
                    self.on_status("released after the maximum length")
                elif kind == "cancel" and active:
                    active = False
                    self.on_status(f"cancelled ({value})")
            except Exception as e:  # a failed command must not stop listening
                active = False
                self.on_status(f"error: {e!r}")

    # --- long dictation ---

    DICTATION_IDLE_S = 60  # long silence: dictation stops by itself

    def start_dictation(self) -> None:
        """Long dictation, depending on the listening mode:
        - push-to-talk: each press is a sentence, typed on release; the mic only opens while the key
          is held (robust to noise, and that is what this mode promises);
        - open listening: mic open continuously, each sentence (end: 700 ms of silence) is typed.
        """
        if self.dictating:
            return
        self.dictating = True
        self.follow_until = self.armed_until = 0.0
        self.events.put(("dictation", True))
        if self.mode == "open":
            if not self.mic.running:
                self.mic.start()
            self.duck(0.25)  # a video must not be typed
        ui.sound("start")
        ui.status("dictating")

    def stop_dictation(self) -> None:
        """End of dictation (« fin de dictée », Esc, the key, or 60 s of silence): sound, and the icon returns."""
        if not self.dictating:
            return
        self.dictating = False
        self.events.put(("dictation", False))
        if self.mode != "open" and not self.keys.down:
            self.mic.stop()
        self.ducker.restore()
        ui.sound("stop")
        ui.status("idle")

    def _dictation_audio(self, chunk: np.ndarray) -> None:
        seg = self.dictation_seg
        if ui.speaking():
            seg.reset()
            return
        seg.add(chunk, gate=self.cfg["audio"].get("open_gate", 0.006))
        if not seg.has_speech:
            if len(seg.chunks) > seg.pad_chunks:
                del seg.chunks[0]
            if time.monotonic() - self.dictation_last > self.DICTATION_IDLE_S:
                self.stop_dictation()
            return
        if not seg.speech_ended and seg.seconds < self.cfg["audio"]["max_seconds"]:
            return
        spoken_ms = (seg.last - seg.first + 1) * audio.CHUNK_MS
        if spoken_ms < 350:  # short noise (door, distant voice): ignored
            seg.reset()
            return
        text = self.transcriber.transcribe(seg.speech_audio())
        seg.reset()
        self.dictation_last = time.monotonic()
        if text.strip(" .,!?") and self.on_dictation:
            self.on_dictation(text)

    def open_follow_up(self) -> None:
        """Conversation mode (open listening): after an action, a few seconds of listening without the name.

        The window opens when the assistant has finished speaking (it does not hear itself), with a sound and
        the mic icon. Only simple commands (rules, or the AI limited to these commands) are accepted, so
        that a video triggers nothing.
        The Mac's sound is not lowered: echo cancellation already removes the video from the mic, and a 5 s dip
        after each command (« monte le son » included) made the sound seem to change on its own.
        """
        audio_cfg = config.load()["audio"]
        if self.mode != "open" or not audio_cfg.get("conversation", True):
            return
        deadline = time.monotonic() + 20
        while ui.speaking() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)  # end of the echo of its own voice
        self.follow_until = time.monotonic() + audio_cfg.get("conversation_s", 5)
        ui.sound("start")
        ui.status("listening")

    def _close_follow_up(self) -> None:
        self.follow_until = 0.0
        self.ducker.restore()
        ui.status("idle")

    def _open_audio(self, chunk: np.ndarray) -> None:
        if self.armed_until and time.monotonic() >= self.armed_until:  # nothing said after the name
            self.armed_until = 0.0
            self.ducker.restore()
            ui.status("idle")
        now = time.monotonic()
        if self.follow_until:
            if self.open.seg.has_speech:
                self.follow_until = max(self.follow_until, now + 0.5)  # sentence started: let it finish
            elif now >= self.follow_until:
                self._close_follow_up()
        if ui.speaking():  # Boulito is speaking: it must not hear itself
            if self.open.named_at is not None:  # name heard just before: the lowered sound is restored
                self.name_signalled = False
                self.ducker.restore()
                ui.status("idle")
            self.open.reset()
            return
        in_conversation = now < self.follow_until
        free = self.reply_expected.is_set() or now < self.armed_until or in_conversation
        event = self.open.add(chunk, free)
        if event is None:
            return
        signalled, self.name_signalled = self.name_signalled, event[0] == "name"
        if event[0] == "name":  # name heard: sound, and the Mac lowers its sound to hear the command better
            self.on_status("open listening: name heard")  # never the heard text
            ui.sound("start")
            ui.status("listening")
            self.duck()
            return
        if event[0] == "dropped":  # not the name after all: nothing is done
            self.on_status("open listening: name not confirmed, nothing done")
            self.ducker.restore()
            ui.status("idle")
            return
        if event[0] == "armed":  # « Boulito » alone: beep, and the command can follow without repeating it
            self.armed_until = time.monotonic() + self.ARMED_S
            self.follow_until = 0.0  # the next command is addressed to Boulito: not a mere follow-up
            if not signalled:  # no second beep if the name was just signalled
                ui.sound("start")
            ui.status("listening")
            self.duck()  # the command is coming: lower the sound to hear it better
            return
        _, text, speech_s, stt_ms, ended_at = event
        follow_up = in_conversation and not self.reply_expected.is_set() and router.wake_split(text) is None
        if follow_up and (rest := router.wake_find(text)) is not None:  # « Euh Boulito, ouvre Notes »: addressed to Boulito
            text, follow_up = rest or router.without_name(text), False
        self.armed_until = 0.0
        self.follow_until = 0.0
        self.ducker.restore()
        if not self.reply_expected.is_set():
            ui.sound("stop")
            ui.status("working")
        self.on_heard(Heard(text, speech_s, stt_ms, ms_since(ended_at), False, 0.0, ended_at, follow_up))

    def _finish(self, released_at: float, early) -> None:
        seg = self.seg
        if seg.peak == 0:
            self.on_status("microphone silent: Microphone permission denied? (./voix check)")
            return
        if not seg.has_speech:
            self.on_status("nothing heard")
            return
        speech = seg.speech_audio()
        if early and early[0] == seg.last:
            text, stt_ms, was_early = early[1], early[2], True
        else:
            t = time.perf_counter()
            text = self.transcriber.transcribe(speech)
            stt_ms, was_early = ms_since(t), False
        self.on_heard(Heard(text, round(len(speech) / audio.SAMPLE_RATE, 2), stt_ms,
                            ms_since(released_at), was_early, self.mic_start_ms, released_at))
