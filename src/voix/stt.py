"""Speech to text: Parakeet TDT 0.6B v3 (parakeet-mlx), locally, French and English.

Two choices measured on 49 test sentences (see docs/design.md):
- the sentence is transcribed in one block when the key is released, not streamed:
  35 ms instead of 90 ms, and without the duplicate words that streaming produces;
- the spectrogram is computed as in NeMo, which the model was trained with
  (parakeet-mlx uses an approximation): 16.6% word error rate instead of 23.3%.
"""

from pathlib import Path

import numpy as np

from . import config

SAMPLE_RATE = 16000
MODEL_FILES = ("config.json", "model.safetensors")
DISK_GB = 2.5  # Parakeet (2.3 GB) and Silero VAD: download size, for the progress display
# Memory MLX keeps aside after use to reuse it (process-wide setting, LLM included).
# Without a limit, 1.2 to 1.5 GB stayed reserved after Parakeet loaded, unused.
MLX_CACHE_LIMIT = 1 << 30  # 1 GB


def model_id() -> str:
    return config.load()["stt"]["model"]


def is_downloaded() -> bool:
    return all(isinstance(model_file(f), str) for f in MODEL_FILES)


def model_file(name: str):
    """Path of a model file in models/ (at the pinned commit), or something else if it is missing."""
    from huggingface_hub import try_to_load_from_cache

    return try_to_load_from_cache(model_id(), name, revision=config.model_revision(model_id()))


def download() -> None:
    """Downloads the model into models/. The only step that uses the network."""
    from huggingface_hub import hf_hub_download

    for name in MODEL_FILES:
        hf_hub_download(model_id(), name, revision=config.model_revision(model_id()))


class Transcriber:
    """Parakeet, loaded once. MLX: create and use the object in the same thread."""

    def __init__(self) -> None:
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import stft

        if not is_downloaded():
            raise SystemExit("Transcription model missing: run ./voix download")
        mx.set_cache_limit(MLX_CACHE_LIMIT)
        self.mx, self.stft = mx, stft
        # From the folder of the pinned commit (from_pretrained would take the one of the main branch)
        self.model = from_pretrained(str(Path(model_file("config.json")).parent))
        pre = self.pre = self.model.preprocessor_config
        # Symmetric Hann window centered in n_fft, like torch.stft in NeMo
        window = np.zeros(pre.n_fft, np.float32)
        offset = (pre.n_fft - pre.win_length) // 2
        window[offset : offset + pre.win_length] = np.hanning(pre.win_length)
        self.window = mx.array(window)
        self.transcribe(np.zeros(SAMPLE_RATE, np.float32))  # warm-up: 1.3 s the first time
        mx.clear_cache()  # frees the working memory used by loading and warm-up

    def logmel(self, audio: np.ndarray):
        """Log-mel as in NeMo: STFT magnitude power, log(x + 2^-24), per-band normalization."""
        mx, pre = self.mx, self.pre
        x = mx.array(audio)
        x = mx.concatenate([x[:1], x[1:] - pre.preemph * x[:-1]])
        spec = self.stft(x, pre.n_fft, pre.hop_length, pre.n_fft, self.window, pad_mode="constant")
        power = mx.abs(spec) ** pre.mag_power
        mel = mx.log(pre._filterbanks @ power.T + 2**-24)
        mel = (mel - mel.mean(axis=1, keepdims=True)) / (mel.std(axis=1, keepdims=True, ddof=1) + 1e-5)
        return mel.T[None]

    def transcribe(self, audio: np.ndarray) -> str:
        """Mono 16 kHz float32 audio → text."""
        return self.model.generate(self.logmel(audio))[0].text
