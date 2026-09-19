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

    The tracked bin is "sticky": once one bin is locked in, a competing
    bin only takes over after it has led by a margin for several
    consecutive windows (see ``_locked`` below). Without this, a nearby
    station or noise burst that is momentarily louder than the wanted
    signal during its own inter-element gaps — a common occurrence
    under QRM — would otherwise be re-elected as "dominant" on the
    spot, and the gate would then key off a completely different
    signal for a few windows, garbling the timing.
    """

    # CW passband to scan (Hz)
    FREQ_START = 300.0
    FREQ_END = 1200.0
    FREQ_STEP = 50.0

    EMA_ALPHA = 0.25  # per-window energy smoothing

    # Bin-lock hysteresis: a challenger must beat the locked bin's energy
    # by this factor, for this many consecutive windows, before it takes
    # over as the tracked tone.
    SWITCH_MARGIN = 1.6
    SWITCH_HOLD_WINDOWS = 4

    # Safety valve for the freeze below: a brief noise click (atmospheric
    # static, ignition/RFI, contact bounce — all common on real HF audio
    # and easily 20-100ms long) can itself pass as a legitimate mark and
    # freeze the lock onto pure noise *before* the wanted signal even
    # starts, which would otherwise strand the tracker there — silently
    # dropping the entire, however loud, transmission that follows —
    # until a full 3-second silence reset. Even while frozen, a
    # candidate that is overwhelmingly stronger (not just comparably
    # louder, which is what a same-strength QRM station would be) for a
    # much longer stretch than the normal hysteresis is allowed to break
    # the freeze and take over.
    OVERRIDE_MARGIN = 3.0
    OVERRIDE_HOLD_WINDOWS = 100  # ~500ms

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
        self._locked = 0
        self._challenger = None
        self._challenger_count = 0
        self._override_challenger = None
        self._override_count = 0
        self._frozen = False

    def freeze(self) -> None:
        """Stop re-electing the locked bin.

        Called once the decoder has confirmed a real mark, so that a
        competing station or noise burst that outshines the wanted
        signal during its own inter-element gaps can no longer steal
        the lock for the rest of the transmission.
        """
        self._frozen = True

    def unfreeze(self) -> None:
        """Resume free bin acquisition (after a reset / end-of-tx)."""
        self._frozen = False
        self._challenger = None
        self._challenger_count = 0
        self._override_challenger = None
        self._override_count = 0

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
        """Frequency (Hz) of the currently tracked (locked) tone."""
        if not self.freqs:
            return 0.0
        return self.freqs[self._locked]

    def process(self, samples):
        """Return ``(mag, freq_hz)`` for the tracked tone in ``samples``."""
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
        raw_best = 0
        for i, m in enumerate(mags):
            self._energy[i] = (1.0 - alpha) * self._energy[i] + alpha * m
            if self._energy[i] > self._energy[raw_best]:
                raw_best = i

        self._track_lock(raw_best)

        return mags[self._locked], self.freqs[self._locked]

    def _track_lock(self, raw_best: int) -> None:
        """Only let ``raw_best`` take over the locked bin after it has
        led by ``SWITCH_MARGIN`` for ``SWITCH_HOLD_WINDOWS`` in a row."""
        if self._frozen:
            self._track_override(raw_best)
            return

        if raw_best == self._locked:
            self._challenger = None
            self._challenger_count = 0
            return

        if self._energy[raw_best] <= self._energy[self._locked] * self.SWITCH_MARGIN:
            self._challenger = None
            self._challenger_count = 0
            return

        if raw_best == self._challenger:
            self._challenger_count += 1
        else:
            self._challenger = raw_best
            self._challenger_count = 1

        if self._challenger_count >= self.SWITCH_HOLD_WINDOWS:
            self._locked = raw_best
            self._challenger = None
            self._challenger_count = 0

    def _track_override(self, raw_best: int) -> None:
        """While frozen, only let a hugely and persistently stronger
        bin break the lock (see ``OVERRIDE_MARGIN``/``OVERRIDE_HOLD_WINDOWS``
        above). Stays frozen afterwards — this just corrects a bad lock,
        it doesn't reopen the door to normal QRM-driven hopping."""
        if raw_best == self._locked:
            self._override_challenger = None
            self._override_count = 0
            return

        if self._energy[raw_best] <= self._energy[self._locked] * self.OVERRIDE_MARGIN:
            self._override_challenger = None
            self._override_count = 0
            return

        if raw_best == self._override_challenger:
            self._override_count += 1
        else:
            self._override_challenger = raw_best
            self._override_count = 1

        if self._override_count >= self.OVERRIDE_HOLD_WINDOWS:
            self._locked = raw_best
            self._override_challenger = None
            self._override_count = 0
            self._challenger = None
            self._challenger_count = 0


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
        peak_release=0.15,
    ):
        self.open_db = open_db
        self.close_db = close_db
        self.attack = attack       # floor tracking rate while idle
        self.release = release     # slow upward drift while signal present
        self.min_floor = min_floor
        self.hold_fraction = hold_fraction  # threshold hold relative to peak
        # Peak decay per window (~23ms half-life). Fast enough that a
        # real HF fade (QSB) is mostly forgotten within a couple of
        # elements, so the gate can re-open for the new, genuinely
        # weaker level instead of staying keyed to how loud the signal
        # used to be — but still slow enough to bridge a brief
        # within-element codec/encoder dropout and to resist noise
        # right after a real mark. A much faster decay (or dropping
        # peak from the open threshold entirely) recovers from QSB
        # quicker still, but was measured to make the decoder noticeably
        # less noise-robust on ordinary, non-fading signals.
        self.peak_release = peak_release
        self.floor = 0.0
        self.peak = 0.0
        self.is_open = False

    def process(self, mag):
        # peak with fast attack / slow decay (hang time): keeps the
        # threshold up across quiet gaps (e.g. Opus digital silence)
        if mag > self.peak:
            self.peak = mag
        else:
            self.peak *= 1.0 - self.peak_release

        # Decide open/close against the floor as it stood BEFORE this
        # window, then update the floor afterwards. Doing it the other
        # way round — updating the floor toward `mag` first and testing
        # against the already-nudged value — lets a weak-but-real signal
        # get partly absorbed into its own floor on the very window that
        # should have opened the gate for it. That shrinks the margin,
        # which fails to open, which lets the floor rise a bit more next
        # window, and so on: a runaway feedback loop that permanently
        # locks the gate shut for any signal not loud enough to clear
        # the threshold outright on window one.
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

        if not self.is_open or mag < self.floor * 1.5:
            # idle — follow the noise floor down quickly
            self.floor = (1.0 - self.attack) * self.floor + self.attack * mag
        else:
            # signal present — let the floor drift up very slowly
            self.floor *= 1.0 + self.release

        if self.floor < self.min_floor:
            self.floor = self.min_floor

        return self.is_open

    def level_db(self, mag):
        """Signal level above the noise floor, clamped to 0..40 dB."""
        if mag <= 0 or self.floor <= 0:
            return 0.0
        db = 20.0 * math.log10(mag / self.floor)
        return max(0.0, min(db, 40.0))