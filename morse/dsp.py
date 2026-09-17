"""Lightweight DSP helpers for the CW (Morse) decoder.

Pure Python — no numpy required. Input is 8 kHz mono S16LE PCM produced
by the RX audio tap branch in ``audio/rx.py``.
"""

import math

# The tap branch delivers 8 kHz mono S16LE
SAMPLE_RATE = 8000


def goertzel_magnitude(samples, freq, sample_rate=SAMPLE_RATE):
    """Estimate the amplitude of ``freq`` present in ``samples``.

    Single-bin Goertzel algorithm. The returned value is normalized by
    the block length and is proportional to the tone amplitude in raw
    PCM units (for a full-scale sine it is roughly amplitude / 2).
    """
    n = len(samples)
    if n <= 0:
        return 0.0

    k = int(round(n * freq / sample_rate))
    if k <= 0 or k >= n:
        return 0.0

    w = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(w)

    s1 = 0.0
    s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0

    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return math.sqrt(max(power, 0.0)) / n


class ToneBank:
    """Goertzel filter bank with automatic dominant-tone tracking.

    Scans a bank of narrow bins covering the CW passband and returns the
    magnitude of the strongest *persistent* tone together with its
    frequency. This makes decoding independent of the actual beat tone
    produced by the radio (typically 300–1200 Hz).

    The dominant bin is selected by the highest smoothed energy, which
    rejects transient noise peaks; the returned magnitude is the raw
    current-window magnitude of that bin, so short CW gaps still make
    the gate close.
    """

    # CW passband to scan (Hz)
    FREQ_START = 300.0
    FREQ_END = 1200.0
    FREQ_STEP = 50.0

    EMA_ALPHA = 0.25  # per-window energy smoothing

    def __init__(self, sample_rate=SAMPLE_RATE, window_size=80, manual_tone=0.0):
        self.sample_rate = sample_rate
        self.window = window_size
        self.freqs = []
        self._coeffs = []

        if manual_tone and manual_tone > 0:
            self._add_bin(float(manual_tone))
        else:
            freq = self.FREQ_START
            while freq <= self.FREQ_END:
                self._add_bin(freq)
                freq += self.FREQ_STEP

        self._energy = [0.0] * len(self.freqs)

    def _add_bin(self, freq: float) -> None:
        n = self.window
        k = int(round(n * freq / self.sample_rate))
        if k <= 0 or k >= n:
            return
        w = 2.0 * math.pi * k / n
        # report the actual DFT bin center, not the requested frequency —
        # adjacent requested frequencies often collapse onto the same bin
        self.freqs.append(k * self.sample_rate / n)
        self._coeffs.append(2.0 * math.cos(w))

    @property
    def dominant_freq(self) -> float:
        """Frequency (Hz) of the currently tracked dominant tone."""
        if not self.freqs:
            return 0.0
        best = 0
        for i in range(1, len(self._energy)):
            if self._energy[i] > self._energy[best]:
                best = i
        return self.freqs[best]

    def process(self, samples):
        """Return ``(mag, freq_hz)`` for the dominant tone in ``samples``."""
        n = self.window
        if n <= 0 or not samples:
            return 0.0, self.dominant_freq

        mags = []
        for coeff in self._coeffs:
            s1 = 0.0
            s2 = 0.0
            for x in samples:
                s0 = x + coeff * s1 - s2
                s2 = s1
                s1 = s0
            power = s1 * s1 + s2 * s2 - coeff * s1 * s2
            mags.append(math.sqrt(max(power, 0.0)) / n)

        alpha = self.EMA_ALPHA
        best = 0
        for i, m in enumerate(mags):
            self._energy[i] = (1.0 - alpha) * self._energy[i] + alpha * m
            if self._energy[i] > self._energy[best]:
                best = i

        return mags[best], self.freqs[best]


class ToneGate:
    """Adaptive tone gate (Schmitt trigger) with noise-floor tracking.

    Opens when the measured tone magnitude exceeds the tracked noise
    floor by ``open_db`` decibels and closes when it drops below
    ``close_db`` decibels. The floor follows the noise level down while
    idle and drifts up only very slowly while a signal is present, so
    short CW elements and gaps are preserved.
    """

    def __init__(
        self,
        open_db=12.0,
        close_db=6.0,
        attack=0.06,
        release=0.0005,
        min_floor=2.0,
        hold_fraction=0.12,
        peak_release=0.006,
    ):
        self.open_db = open_db
        self.close_db = close_db
        self.attack = attack       # floor tracking rate while idle
        self.release = release     # slow upward drift while signal present
        self.min_floor = min_floor
        self.hold_fraction = hold_fraction  # threshold hold relative to peak
        self.peak_release = peak_release    # peak decay per window (hang time)
        self.floor = 0.0
        self.peak = 0.0
        self.is_open = False

    def process(self, mag):
        if not self.is_open or mag < self.floor * 1.5:
            # idle — follow the noise floor down quickly
            self.floor = (1.0 - self.attack) * self.floor + self.attack * mag
        else:
            # signal present — let the floor drift up very slowly
            self.floor *= 1.0 + self.release

        if self.floor < self.min_floor:
            self.floor = self.min_floor

        # peak with fast attack / slow decay (hang time): keeps the
        # threshold up across quiet gaps (e.g. Opus digital silence)
        if mag > self.peak:
            self.peak = mag
        else:
            self.peak *= 1.0 - self.peak_release

        thr_open = max(
            self.floor * 10.0 ** (self.open_db / 20.0),
            self.peak * self.hold_fraction,
        )
        thr_close = max(
            self.floor * 10.0 ** (self.close_db / 20.0),
            self.peak * self.hold_fraction * 0.5,
        )

        if self.is_open:
            if mag < thr_close:
                self.is_open = False
        else:
            if mag > thr_open:
                self.is_open = True
        return self.is_open

    def level_db(self, mag):
        """Signal level above the noise floor, clamped to 0..40 dB."""
        if mag <= 0 or self.floor <= 0:
            return 0.0
        db = 20.0 * math.log10(mag / self.floor)
        return max(0.0, min(db, 40.0))