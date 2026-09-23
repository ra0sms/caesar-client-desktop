# SPDX-License-Identifier: AGPL-3.0-only
# Port of the deepcw-engine reference implementation
# (https://github.com/e04/deepcw-engine), see morse/deepcw/LICENSE.

"""DeepCW neural CW decoder: model loading, pre-processing and CTC decoding.

Port of the reference implementation in deepcw-engine
(``examples/python/decode_morse.py``): audio is resampled to the model's
rate, turned into a log1p magnitude spectrogram restricted to the model's
400-1200 Hz band, run through the ONNX model, and the CTC output is
greedily collapsed into text. On top of that, character and word-space
frame positions are kept (as the DeepCW web app does) so the streaming
decoder can decide where it is safe to cut the audio.
"""

import json
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deepcw")
MODEL_PATH = os.path.join(MODEL_DIR, "model.onnx")
METADATA_PATH = os.path.join(MODEL_DIR, "model.onnx.json")

# The model only sees 400-1200 Hz. Tones outside a safe margin inside that
# band are frequency-shifted to this pitch before the spectrogram is taken.
SHIFT_TARGET_HZ = 750.0
IN_BAND_MIN_HZ = 450.0
IN_BAND_MAX_HZ = 1150.0

TONE_SEARCH_MIN_HZ = 250.0
TONE_SEARCH_MAX_HZ = 1300.0
# A CW tone concentrates into one FFT bin and stands far above the median
# bin; the largest bin of pure noise over a few seconds is ~4x the median.
TONE_MIN_PEAK_RATIO = 8.0

# The model takes an un-normalized log1p spectrogram, so its behaviour depends
# on absolute level. On windows of pure noise it occasionally hallucinates a
# character, and measurably more often the louder that noise is (about 2% of
# noise-only windows at RMS 0.01, 10% at 0.05, over half at 0.2), while
# decoding accuracy on real signals was the same anywhere from 0.005 to 0.05.
# So each window is scaled to a low RMS first (within the gain limits).
AGC_TARGET_RMS = 0.01
AGC_MIN_GAIN = 0.25
AGC_MAX_GAIN = 1000.0


@dataclass
class CharSpan:
    char: str
    start_frame: int
    end_frame: int
    # model posterior for this label (peak over its frames): real characters
    # come out at ~1.0, hallucinations on noise well below
    prob: float = 1.0


@dataclass
class Analysis:
    """Result of one model pass over a window of audio."""

    chars: List[CharSpan] = field(default_factory=list)
    word_spaces: List[CharSpan] = field(default_factory=list)
    frames: int = 0
    frame_seconds: float = 0.0
    # per-frame loudest bin of the model input, for locating real gaps
    envelope: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))

    def frame_to_seconds(self, frame: float) -> float:
        return frame * self.frame_seconds


def _design_lowpass(cutoff_hz: float, sample_rate: int, taps: int = 129) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2.0
    fc = cutoff_hz / sample_rate
    h = 2.0 * fc * np.sinc(2.0 * fc * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


def detect_tone(audio: np.ndarray, sample_rate: int) -> Optional[float]:
    """Dominant CW tone in the search band, or None if nothing stands out."""
    if len(audio) < 1024:
        return None
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / sample_rate)
    band = (freqs >= TONE_SEARCH_MIN_HZ) & (freqs <= TONE_SEARCH_MAX_HZ)
    if not band.any():
        return None
    mags = spectrum[band]
    median = float(np.median(mags))
    peak_index = int(np.argmax(mags))
    if median <= 0.0 or mags[peak_index] < TONE_MIN_PEAK_RATIO * median:
        return None
    return float(freqs[band][peak_index])


def _shift_frequency(audio: np.ndarray, shift_hz: float, sample_rate: int) -> np.ndarray:
    """Move the whole spectrum by shift_hz using the analytic signal."""
    n = len(audio)
    spectrum = np.fft.fft(audio)
    h = np.zeros(n)
    h[0] = 1.0
    if n % 2 == 0:
        h[n // 2] = 1.0
        h[1 : n // 2] = 2.0
    else:
        h[1 : (n + 1) // 2] = 2.0
    analytic = np.fft.ifft(spectrum * h)
    t = np.arange(n) / sample_rate
    return np.real(analytic * np.exp(2j * np.pi * shift_hz * t)).astype(np.float32)


class DeepCwEngine:
    """Loads the ONNX model once; each analyze() call is independent."""

    def __init__(self, model_path: str = MODEL_PATH, metadata_path: str = METADATA_PATH):
        import onnxruntime as ort

        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        self.chars = list(meta["chars"])
        self.blank_index = int(meta["blank_index"])
        self.sample_rate = int(meta["sample_rate"])
        self.fft_length = int(meta["fft_length"])
        self.hop_length = int(meta["hop_length"])
        self.input_name = meta["onnx_input_name"]
        self.output_name = meta["onnx_output_name"]
        if meta.get("normalization") != "log1p":
            raise ValueError(f"Unsupported normalization: {meta.get('normalization')}")

        bin_hz = self.sample_rate / self.fft_length
        self.start_bin = int(math.ceil(float(meta["spectrogram_min_freq_hz"]) / bin_hz))
        self.stop_bin = int(math.floor(float(meta["spectrogram_max_freq_hz"]) / bin_hz)) + 1
        if self.stop_bin - self.start_bin != int(meta["spectrogram_frequency_bins"]):
            raise ValueError("Model metadata and spectrogram band disagree")

        self.space_index = self.chars.index(" ")
        self.frame_seconds = self.hop_length / self.sample_rate
        self.window = np.hanning(self.fft_length + 1)[:-1].astype(np.float32)

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._lowpass_cache = {}

    def resample(self, audio: np.ndarray, source_rate: int) -> np.ndarray:
        """Anti-aliased resample to the model rate (lowpass + linear interp)."""
        target = self.sample_rate
        if source_rate == target or len(audio) == 0:
            return audio.astype(np.float32, copy=False)
        if source_rate > target:
            h = self._lowpass_cache.get(source_rate)
            if h is None:
                h = _design_lowpass(0.44 * target, source_rate)
                self._lowpass_cache[source_rate] = h
            audio = np.convolve(audio, h, mode="same").astype(np.float32)
        length = int(round(len(audio) * target / source_rate))
        pos = np.arange(length, dtype=np.float64) * source_rate / target
        left = np.floor(pos).astype(np.int64)
        right = np.minimum(left + 1, len(audio) - 1)
        frac = (pos - left).astype(np.float32)
        return (audio[left] * (1.0 - frac) + audio[right] * frac).astype(np.float32)

    def _spectrogram(self, audio: np.ndarray) -> np.ndarray:
        pad = self.fft_length // 2
        audio = np.pad(audio, (pad, pad), mode="reflect")
        frames = 1 + (len(audio) - self.fft_length) // self.hop_length
        index = (
            np.arange(self.fft_length)[None, :]
            + self.hop_length * np.arange(frames)[:, None]
        )
        spectrum = np.abs(np.fft.rfft(audio[index] * self.window, axis=1))
        band = spectrum[:, self.start_bin : self.stop_bin]
        return np.log1p(band).astype(np.float32)[np.newaxis, np.newaxis]

    def analyze(self, audio: np.ndarray, source_rate: int, tone_hz: Optional[float]) -> Analysis:
        """Decode a window of mono float audio in [-1, 1]."""
        audio = self.resample(audio, source_rate)
        if len(audio) < self.fft_length:
            return Analysis(frame_seconds=self.frame_seconds)
        model_tone = tone_hz
        if tone_hz and not (IN_BAND_MIN_HZ <= tone_hz <= IN_BAND_MAX_HZ):
            audio = _shift_frequency(audio, SHIFT_TARGET_HZ - tone_hz, self.sample_rate)
            model_tone = SHIFT_TARGET_HZ
        rms = float(np.sqrt(np.mean(audio * audio)))
        if rms > 1e-9:
            gain = min(max(AGC_TARGET_RMS / rms, AGC_MIN_GAIN), AGC_MAX_GAIN)
            audio = (audio * gain).astype(np.float32)

        spectrogram = self._spectrogram(audio)
        log_probs = self.session.run([self.output_name], {self.input_name: spectrogram})[0]
        result = self._ctc_spans(log_probs[0])
        band = spectrogram[0, 0]
        if model_tone:
            center = int(round(model_tone * self.fft_length / self.sample_rate)) - self.start_bin
            lo, hi = max(0, center - 1), min(band.shape[1], center + 2)
            if lo < hi:
                band = band[:, lo:hi]
        result.envelope = band.max(axis=1)
        return result

    def _ctc_spans(self, log_probs: np.ndarray) -> Analysis:
        best_path = log_probs.argmax(axis=-1)
        result = Analysis(frames=len(best_path), frame_seconds=self.frame_seconds)
        previous = None
        active = None
        space_start = None
        for frame, index in enumerate(best_path.tolist()):
            if index == self.space_index:
                if space_start is None:
                    space_start = frame
            elif space_start is not None:
                result.word_spaces.append(CharSpan(" ", space_start, frame - 1))
                space_start = None

            if index == self.blank_index:
                previous = None
                active = None
                continue
            if index == previous:
                if active is not None:
                    active.end_frame = frame
                continue
            previous = index
            active = CharSpan(self.chars[index], frame, frame)
            result.chars.append(active)

        if space_start is not None:
            result.word_spaces.append(CharSpan(" ", space_start, len(best_path) - 1))

        probs = np.exp(log_probs)
        for span in result.chars:
            column = self.chars.index(span.char)
            span.prob = float(probs[span.start_frame : span.end_frame + 1, column].max())
        return result
