"""Listening: push-to-talk key, 16 kHz mic, Silero VAD.

The mic is only open while the key is held. The key is read by a read-only "event tap"
(Input Monitoring permission): it sees keystrokes but can neither
block nor modify them, and the code only looks at the chosen key and Esc.
"""

import ctypes
import hashlib
import time
import urllib.request
from typing import Callable

import numpy as np

from . import config

SAMPLE_RATE = 16000
CHUNK = 512  # 32 ms, the size Silero VAD expects at 16 kHz
CHUNK_MS = 1000 * CHUNK / SAMPLE_RATE

# Silero VAD v6 run in numpy, with the weights read from the official ONNX model (16 kHz).
# No onnxruntime: it sends telemetry to Microsoft as soon as it is imported.
VAD_FILE = config.MODELS_DIR / "silero-vad" / "silero_vad_16k_op15.onnx"
VAD_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad_16k_op15.onnx"
VAD_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"



# --- Silero VAD -------------------------------------------------------------

def vad_downloaded() -> bool:
    return VAD_FILE.exists() and hashlib.sha256(VAD_FILE.read_bytes()).hexdigest() == VAD_SHA256


def download_vad() -> None:
    """Downloads the Silero VAD model (1.3 MB, pinned version) and checks its hash."""
    VAD_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(VAD_URL, timeout=30).read()
    if hashlib.sha256(data).hexdigest() != VAD_SHA256:
        raise SystemExit("Silero VAD: unexpected checksum, file rejected.")
    VAD_FILE.write_bytes(data)


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def _fields(buf: bytes):
    """Fields of a protobuf message: (number, integer value or bytes)."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = buf[i : i + size], i + size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, i = buf[i : i + size], i + size
        else:
            raise ValueError(f"protobuf: unsupported field type {wire}")
        yield number, value


def read_onnx_weights(path) -> dict[str, np.ndarray]:
    """float32 weights ("initializers") of an ONNX model, read without onnx or onnxruntime.

    Fields used: ModelProto.graph = 7, GraphProto.initializer = 5,
    TensorProto: dims = 1, data_type = 2 (1 = float32), float_data = 4, name = 8, raw_data = 9.
    """
    graph = next(value for number, value in _fields(path.read_bytes()) if number == 7)
    weights = {}
    for number, tensor in _fields(graph):
        if number != 5:
            continue
        dims, name, data, dtype = [], "", b"", 1
        for field, value in _fields(tensor):
            if field == 1:
                if isinstance(value, int):
                    dims.append(value)
                else:  # "packed" dimensions
                    j = 0
                    while j < len(value):
                        d, j = _varint(value, j)
                        dims.append(d)
            elif field == 2:
                dtype = value
            elif field in (4, 9):
                data = value
            elif field == 8:
                name = value.decode()
        if dtype == 1:
            weights[name] = np.frombuffer(data, np.float32).reshape(dims)
    return weights


def conv1d(x: np.ndarray, w: np.ndarray, b: np.ndarray | None, stride: int = 1, pad: int = 0) -> np.ndarray:
    """1D convolution like torch: x (inputs, time), w (outputs, inputs, kernel).

    A single matrix product (BLAS) instead of an einsum: 90 µs per 32 ms chunk instead of 930 µs,
    same probabilities within 3e-6 (measured on 4,526 chunks, speech and noise, no threshold crossed differently).
    """
    if pad:  # zeros on each side, without np.pad (slow on such small arrays)
        padded = np.zeros((x.shape[0], x.shape[1] + 2 * pad), x.dtype)
        padded[:, pad:-pad] = x
        x = padded
    o, i, k = w.shape
    frames = np.lib.stride_tricks.sliding_window_view(x, k, axis=1)[:, ::stride]  # (inputs, t, kernel)
    y = w.reshape(o, i * k) @ frames.transpose(0, 2, 1).reshape(i * k, -1)  # (outputs, inputs × kernel) @ (…, t)
    return y if b is None else y + b[:, None]


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1 + np.tanh(x / 2))  # shape without overflow


class Vad:
    """Silero VAD: speech probability for each 32 ms chunk.

    Same graph as the official ONNX model: learned STFT, 4 convolutions,
    LSTM cell (gates in PyTorch order: i, f, g, o), sigmoid output.
    """

    NAMES = {  # name in the ONNX model → short name
        "model.stft.forward_basis_buffer": "stft", "model.decoder.rnn.weight_ih": "w_ih",
        "model.decoder.rnn.weight_hh": "w_hh", "model.decoder.rnn.bias_ih": "b_ih",
        "model.decoder.rnn.bias_hh": "b_hh", "model.decoder.decoder.2.weight": "out_w",
        "model.decoder.decoder.2.bias": "out_b",
        **{f"model.encoder.{i}.reparam_conv.{k}": f"conv{i + 1}_{k[0]}" for i in range(4) for k in ("weight", "bias")},
    }

    def __init__(self) -> None:
        onnx = read_onnx_weights(VAD_FILE)
        self.w = {short: onnx[name] for name, short in self.NAMES.items()}
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros(128, np.float32)
        self.c = np.zeros(128, np.float32)
        self.context = np.zeros(64, np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        w = self.w
        x = np.concatenate([self.context, chunk])  # 64 context samples + 512
        self.context = x[-64:]
        x = np.pad(x, (0, 64), mode="reflect")[None, :]
        x = conv1d(x, w["stft"], None, stride=128)
        x = np.sqrt(x[:129] ** 2 + x[129:] ** 2)
        x = np.maximum(conv1d(x, w["conv1_w"], w["conv1_b"], 1, 1), 0)
        x = np.maximum(conv1d(x, w["conv2_w"], w["conv2_b"], 2, 1), 0)
        x = np.maximum(conv1d(x, w["conv3_w"], w["conv3_b"], 2, 1), 0)
        x = np.maximum(conv1d(x, w["conv4_w"], w["conv4_b"], 1, 1), 0)[:, 0]
        gates = w["w_ih"] @ x + w["b_ih"] + w["w_hh"] @ self.h + w["b_hh"]
        i, f, g, o = np.split(gates, 4)
        self.c = sigmoid(f) * self.c + sigmoid(i) * np.tanh(g)
        self.h = sigmoid(o) * np.tanh(self.c)
        out = w["out_w"][0, :, 0] @ np.maximum(self.h, 0) + w["out_b"][0]
        return float(sigmoid(out))


class Segmenter:
    """Accumulates the audio of one key press and locates the speech."""

    def __init__(self, vad: Vad, threshold: float, silence_ms: int, pad_ms: int) -> None:
        self.vad, self.threshold = vad, threshold
        self.silence_chunks = round(silence_ms / CHUNK_MS)
        self.pad_chunks = round(pad_ms / CHUNK_MS)
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self.chunks: list[np.ndarray] = []
        self.first: int | None = None  # first speech chunk
        self.last: int | None = None   # last speech chunk
        self.peak = 0.0
        self.gated = False

    def add(self, chunk: np.ndarray, gate: float = 0.0) -> None:
        """gate: below this level, certain silence, the VAD does not run (open listening: almost 0% in silence)."""
        self.chunks.append(chunk)
        level = float(np.abs(chunk).max())
        self.peak = max(self.peak, level)
        if level < gate:
            self.gated = True
            return
        if self.gated:  # sound back after a silence: the VAD restarts from zero
            self.vad.reset()
            self.gated = False
        in_speech = self.last is not None and len(self.chunks) - 2 == self.last
        # Silero hysteresis: speech starts at 0.5, and only ends below 0.35
        if self.vad(chunk) >= (self.threshold - 0.15 if in_speech else self.threshold):
            self.last = len(self.chunks) - 1
            if self.first is None:
                self.first = self.last

    @property
    def seconds(self) -> float:
        return len(self.chunks) * CHUNK_MS / 1000

    @property
    def has_speech(self) -> bool:
        return self.first is not None

    @property
    def speech_ended(self) -> bool:
        """Speech followed by enough silence: the sentence is probably over."""
        return self.has_speech and len(self.chunks) - 1 - self.last >= self.silence_chunks

    def speech_audio(self, max_s: float | None = None) -> np.ndarray:
        """The speech, with a margin of silence before and after (max_s: only the beginning)."""
        start = max(0, self.first - self.pad_chunks)
        end = min(len(self.chunks), self.last + 1 + self.pad_chunks)
        if max_s is not None:
            end = min(end, start + self.pad_chunks + round(max_s * 1000 / CHUNK_MS))
        return np.concatenate(self.chunks[start:end])


# --- Mic --------------------------------------------------------------------

_capture_device = None


def microphone_status() -> str:
    """Microphone permission of the app that launches Voix (Terminal during development)."""
    global _capture_device
    import objc

    if _capture_device is None:  # loaded once: reloading AVFoundation goes through every class
        objc.loadBundle("AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework")
        _capture_device = objc.lookUpClass("AVCaptureDevice")
    status = _capture_device.authorizationStatusForMediaType_("soun")
    return {0: "not requested yet", 1: "restricted", 2: "denied", 3: "granted"}.get(status, "unknown")


AUDIO_TOOLBOX = "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"


class _Format(ctypes.Structure):  # AudioStreamBasicDescription
    _fields_ = [("mSampleRate", ctypes.c_double), ("mFormatID", ctypes.c_uint32), ("mFormatFlags", ctypes.c_uint32),
                ("mBytesPerPacket", ctypes.c_uint32), ("mFramesPerPacket", ctypes.c_uint32),
                ("mBytesPerFrame", ctypes.c_uint32), ("mChannelsPerFrame", ctypes.c_uint32),
                ("mBitsPerChannel", ctypes.c_uint32), ("mReserved", ctypes.c_uint32)]


class _Buffer(ctypes.Structure):  # AudioQueueBuffer
    _fields_ = [("mAudioDataBytesCapacity", ctypes.c_uint32), ("mAudioData", ctypes.c_void_p),
                ("mAudioDataByteSize", ctypes.c_uint32), ("mUserData", ctypes.c_void_p),
                ("mPacketDescriptionCapacity", ctypes.c_uint32), ("mPacketDescriptions", ctypes.c_void_p),
                ("mPacketDescriptionCount", ctypes.c_uint32)]


_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Buffer),
                             ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)


class Microphone:
    """Mic chosen in System Settings → Sound → Input, mono 16 kHz, open only between start() and stop().

    Goes through AudioQueue (Apple's AudioToolbox) and not PortAudio: the PortAudio version
    shipped with sounddevice sometimes hung when the mic stopped (deadlock in CoreAudio,
    mic left on). AudioQueue converts to 16 kHz itself and calls on_chunk from its
    own thread, with 512-sample chunks.
    """

    # 32 ms buffers. The callback (_on_buffer) waits for Python's lock (GIL): if another thread holds it for
    # a moment (model loading, numpy computation), macOS keeps writing into the buffers already queued, and
    # sound is only lost once they are all full. 3 buffers covered 64 ms of waiting, 8 cover
    # 224 ms. No added latency: buffers fill in order and each is returned as soon as it
    # is full, whatever their number (16 KB in total).
    BUFFERS = 8

    def __init__(self, on_chunk: Callable[[np.ndarray], None]) -> None:
        import sounddevice as sd

        self.on_chunk = on_chunk
        self.name = sd.query_devices(kind="input")["name"]  # display name only, without opening the mic
        lib = self.lib = ctypes.CDLL(AUDIO_TOOLBOX)  # CDLL: the GIL is released during calls
        # Declared types: without them, ctypes passes a Python integer as a 32-bit C int, and the queue received by
        # _on_buffer (a 64-bit pointer) would be truncated before AudioQueueEnqueueBuffer.
        queue_ref, osstatus = ctypes.c_void_p, ctypes.c_int32
        for name, args in (("AudioQueueNewInput", [ctypes.POINTER(_Format), _CALLBACK, ctypes.c_void_p, ctypes.c_void_p,
                                                   ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(queue_ref)]),
                           ("AudioQueueAllocateBuffer", [queue_ref, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(_Buffer))]),
                           ("AudioQueueEnqueueBuffer", [queue_ref, ctypes.POINTER(_Buffer), ctypes.c_uint32, ctypes.c_void_p]),
                           ("AudioQueueStart", [queue_ref, ctypes.c_void_p]),
                           ("AudioQueueStop", [queue_ref, ctypes.c_bool]),
                           ("AudioQueueDispose", [queue_ref, ctypes.c_bool])):
            getattr(lib, name).argtypes, getattr(lib, name).restype = args, osstatus
        self.callback = _CALLBACK(self._on_buffer)  # kept alive as long as the object exists
        self.queue = ctypes.c_void_p()
        self.running = False
        self.pending = np.zeros(0, np.float32)
        fmt = _Format(SAMPLE_RATE, 0x6C70636D, 0x9, 4, 1, 4, 1, 32, 0)  # 'lpcm', float, packed
        self._check(self.lib.AudioQueueNewInput(ctypes.byref(fmt), self.callback, None, None, None, 0,
                                                ctypes.byref(self.queue)), "AudioQueueNewInput")
        self.buffers = []
        for _ in range(self.BUFFERS):
            buf = ctypes.POINTER(_Buffer)()
            self._check(self.lib.AudioQueueAllocateBuffer(self.queue, CHUNK * 4, ctypes.byref(buf)), "AllocateBuffer")
            self.buffers.append(buf)

    @staticmethod
    def _check(status: int, what: str) -> None:
        if status != 0:
            raise RuntimeError(f"microphone: {what} failed (code {status})")

    def _on_buffer(self, user, queue, buf, start_time, n_packets, packets) -> None:
        size = buf.contents.mAudioDataByteSize
        if size:
            data = np.frombuffer(ctypes.string_at(buf.contents.mAudioData, size), np.float32)
            self.pending = np.concatenate([self.pending, data])
            while len(self.pending) >= CHUNK:  # exact 512-sample chunks for Silero VAD
                self.on_chunk(self.pending[:CHUNK].copy())
                self.pending = self.pending[CHUNK:]
        if self.running:
            self.lib.AudioQueueEnqueueBuffer(queue, buf, 0, None)

    def start(self) -> None:
        self.pending = np.zeros(0, np.float32)
        for buf in self.buffers:
            buf.contents.mAudioDataByteSize = 0
            self._check(self.lib.AudioQueueEnqueueBuffer(self.queue, buf, 0, None), "EnqueueBuffer")
        self.running = True
        self._check(self.lib.AudioQueueStart(self.queue, None), "AudioQueueStart")

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.lib.AudioQueueStop(self.queue, True)  # immediate, synchronous stop

    def close(self) -> None:
        self.stop()
        self.lib.AudioQueueDispose(self.queue, True)


class _ComponentDescription(ctypes.Structure):  # AudioComponentDescription
    _fields_ = [("type", ctypes.c_uint32), ("subType", ctypes.c_uint32), ("manufacturer", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("flagsMask", ctypes.c_uint32)]


class _AudioBuffer(ctypes.Structure):
    _fields_ = [("mNumberChannels", ctypes.c_uint32), ("mDataByteSize", ctypes.c_uint32), ("mData", ctypes.c_void_p)]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [("mNumberBuffers", ctypes.c_uint32), ("mBuffers", _AudioBuffer * 1)]


class _Ducking(ctypes.Structure):  # AUVoiceIOOtherAudioDuckingConfiguration
    _fields_ = [("mEnableAdvancedDucking", ctypes.c_uint8), ("mDuckingLevel", ctypes.c_uint32)]


_RENDER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.c_uint32, ctypes.c_void_p)


class _RenderCallback(ctypes.Structure):  # AURenderCallbackStruct
    _fields_ = [("inputProc", _RENDER), ("inputProcRefCon", ctypes.c_void_p)]


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode(), "big")


class EchoCancellingMicrophone:
    """Mic with macOS echo cancellation (VoiceProcessingIO, the one used by FaceTime and Siri).

    macOS removes from the mic the sound the Mac itself plays (video, music, Boulito's voice): in open
    listening, the name and the command stay audible while a video is talking. Same interface as Microphone.
    """

    INPUT, OUTPUT, GLOBAL = 1, 2, 0  # scopes
    ENABLE_IO, STREAM_FORMAT, INPUT_CALLBACK, RENDER_CALLBACK, DUCKING = 2003, 8, 2005, 23, 2108

    def __init__(self, on_chunk: Callable[[np.ndarray], None], duck_level: int = 10) -> None:
        import sounddevice as sd

        self.on_chunk = on_chunk
        self.name = sd.query_devices(kind="input")["name"]
        lib = self.lib = ctypes.CDLL(AUDIO_TOOLBOX)
        lib.AudioComponentFindNext.restype = ctypes.c_void_p
        lib.AudioComponentFindNext.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ComponentDescription)]
        lib.AudioUnitRender.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                        ctypes.c_uint32, ctypes.c_void_p]
        lib.AudioUnitSetProperty.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_void_p, ctypes.c_uint32]
        for name in ("AudioUnitInitialize", "AudioOutputUnitStart", "AudioOutputUnitStop", "AudioUnitUninitialize",
                     "AudioComponentInstanceDispose"):
            getattr(lib, name).argtypes = [ctypes.c_void_p]
        desc = _ComponentDescription(_fourcc("auou"), _fourcc("vpio"), _fourcc("appl"), 0, 0)
        component = lib.AudioComponentFindNext(None, ctypes.byref(desc))
        if not component:
            raise RuntimeError("microphone: echo cancellation (VoiceProcessingIO) not found")
        self.unit = ctypes.c_void_p()
        Microphone._check(lib.AudioComponentInstanceNew(ctypes.c_void_p(component), ctypes.byref(self.unit)), "VPIO")
        on = ctypes.c_uint32(1)
        self._set(self.ENABLE_IO, self.INPUT, 1, on)
        # The output stays active (VoiceProcessingIO requires it) but only plays silence
        fmt = _Format(SAMPLE_RATE, 0x6C70636D, 0x9, 4, 1, 4, 1, 32, 0)  # 16 kHz mono float: converted by macOS
        self._set(self.STREAM_FORMAT, self.OUTPUT, 1, fmt)
        self._set(self.STREAM_FORMAT, self.INPUT, 0, fmt)
        self.callback = _RenderCallback(_RENDER(self._on_input), None)  # kept alive as long as the object exists
        self.silence = _RenderCallback(_RENDER(self._on_output), None)
        self._set(self.INPUT_CALLBACK, self.GLOBAL, 0, self.callback)
        self._set(self.RENDER_CALLBACK, self.INPUT, 0, self.silence)
        try:  # other apps' sound is barely lowered (macOS 14 and later)
            self._set(self.DUCKING, self.GLOBAL, 0, _Ducking(0, duck_level))
        except RuntimeError:
            pass
        status = lib.AudioUnitInitialize(self.unit)
        if status != 0:  # failure (another app holding the mic…): the audio unit is released before giving up
            lib.AudioComponentInstanceDispose(self.unit)
            Microphone._check(status, "AudioUnitInitialize")
        self.samples = (ctypes.c_float * 8192)()
        self.running = False
        self.pending = np.zeros(0, np.float32)

    def _set(self, prop: int, scope: int, element: int, value) -> None:
        Microphone._check(self.lib.AudioUnitSetProperty(self.unit, prop, scope, element, ctypes.byref(value),
                                                        ctypes.sizeof(value)), f"property {prop}")

    def _on_input(self, refcon, flags, timestamp, bus, frames, data) -> int:
        frames = min(frames, len(self.samples))
        buffers = _AudioBufferList(1, (_AudioBuffer * 1)(_AudioBuffer(1, frames * 4, ctypes.addressof(self.samples))))
        status = self.lib.AudioUnitRender(self.unit, flags, timestamp, 1, frames, ctypes.byref(buffers))
        if status == 0 and self.running:
            self.pending = np.concatenate([self.pending, np.frombuffer(self.samples, np.float32, count=frames).copy()])
            while len(self.pending) >= CHUNK:
                self.on_chunk(self.pending[:CHUNK].copy())
                self.pending = self.pending[CHUNK:]
        return 0

    def _on_output(self, refcon, flags, timestamp, bus, frames, data) -> int:
        buffers = ctypes.cast(data, ctypes.POINTER(_AudioBufferList)).contents
        for i in range(buffers.mNumberBuffers):
            b = ctypes.cast(ctypes.addressof(buffers.mBuffers) + i * ctypes.sizeof(_AudioBuffer), ctypes.POINTER(_AudioBuffer)).contents
            ctypes.memset(b.mData, 0, b.mDataByteSize)
        return 0

    def start(self) -> None:
        self.pending = np.zeros(0, np.float32)
        self.running = True
        Microphone._check(self.lib.AudioOutputUnitStart(self.unit), "AudioOutputUnitStart")

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.lib.AudioOutputUnitStop(self.unit)

    def close(self) -> None:
        self.stop()
        self.lib.AudioUnitUninitialize(self.unit)
        self.lib.AudioComponentInstanceDispose(self.unit)


# --- Push-to-talk key -------------------------------------------------------

def input_monitoring_allowed() -> bool:
    import Quartz

    return bool(Quartz.CGPreflightListenEventAccess())


def request_input_monitoring() -> None:
    """Makes Terminal appear in System Settings → Input Monitoring."""
    import Quartz

    Quartz.CGRequestListenEventAccess()
