"""Real-time Morse (CW) decoder.

Consumes 8 kHz mono S16LE PCM (produced by the RX audio tap branch in
``audio/rx.py``) and emits decoded characters / status updates via Qt
signals.

Decoding strategy
------------------
A lightweight tone-presence gate (``ToneBank`` + ``ToneGate``, see
``morse/dsp.py``) tracks the CW passband purely to answer two coarse
questions in real time: "is a signal present right now" (drives the
activity LED) and "has it been quiet long enough that this transmission
is over" (drives when to hand the audio off for decoding). It does not
attempt to time individual dots, dashes, or gaps itself any more.

The actual character decoding is delegated to ``pycw``'s numpy-only
model, which analyzes each complete transmission's raw audio as a
whole (Otsu-style level separation over the whole buffer, not a
per-5ms-window adaptive threshold). Months of tuning a live per-window
Schmitt trigger for real HF conditions (weak signals, strong signals
with realistic background noise, PTT lead-in artifacts) kept trading
one failure mode for another; batch analysis of the complete waveform
sidesteps that entirely, at the cost of only emitting text once a
transmission ends rather than character-by-character while it's still
being received.

Threading contract:
  * ``feed_pcm()`` is called from the RX reader thread (producer);
  * ``pycw`` decoding runs synchronously on that same thread once a
    transmission ends (measured well under 1 second even for several
    minutes of audio, so this does not stall the audio pipeline);
  * signals are emitted from that same thread; PyQt delivers them to
    the GUI thread through queued connections.
"""

import array
import os
import sys
import threading
import time

from PyQt5.QtCore import QObject, pyqtSignal

from morse.dsp import SAMPLE_RATE, ToneBank, ToneGate
from morse.table import CHAR_TO_MORSE

# Optional per-window diagnostic trace. Set this environment variable to
# a file path before starting the app to capture what the presence gate
# is doing on real audio — mag/floor/peak/thresholds for every 5ms
# window, plus a note whenever a transmission starts or gets handed off
# for decoding. Meant for tracking down segmentation problems that don't
# reproduce with synthetic test tones; has no effect unless set.
DEBUG_LOG_PATH = os.environ.get("CAESAR_MORSE_DEBUG")

# Processing window: 5 ms @ 8 kHz
INTEG_SAMPLES = 40
HOP_SAMPLES = 40
WINDOW_MS = HOP_SAMPLES * 1000.0 / SAMPLE_RATE

STATUS_LED_HOLD_MS = 300.0    # activity LED stays lit across short gaps
# Silence longer than this ends a transmission and triggers the decode.
# In the old live-streaming decoder this delay only affected when the
# trailing newline appeared — already-decoded characters were visible
# immediately. Now it gates when *any* text appears at all, so it needs
# to be short enough to feel responsive while still comfortably longer
# than a real inter-word gap (7 units) at plausible speeds — 1.8s covers
# down to ~4.7 WPM with margin, well below any speed this app's own
# tests exercise (8-40 WPM).
END_OF_TX_MS = 1800.0
MAX_UTTERANCE_S = 30.0        # force a decode after this long even mid-tone
# Was originally 180s. A real capture showed the gate staying "open"
# continuously for 29 seconds (unrelated to Morse timing — something
# upstream, outside this decoder, kept feeding it activity well after
# the operator said they had stopped keying), which then went to pycw
# as one giant buffer and came back as an unreadable wall of garbage.
# Splitting on a shorter, fixed ceiling bounds the worst case: at worst
# it cuts one long transmission into a couple of chunks (which pycw
# each still decodes on its own merits) instead of accumulating an
# ever-growing blob that gets harder to decode the longer it runs. 30s
# is chosen to clear this app's own slowest tested speed (8 WPM, ~24s
# for the self-test's message) with room to spare — a tighter cap would
# start splitting ordinary slow-speed messages mid-word (confirmed by
# testing: 12s cut the 8-12 WPM self-test cases into garbled fragments).
MIN_UTTERANCE_MS = 40.0       # shorter than this can't be a real element
MAX_PENDING_BYTES = 65536     # safety cap for the leftover-sample buffer
# An unbroken tone this long before the very first real gap cannot be a
# legitimate first CW element (even 2 WPM's slowest dash is under 2s) —
# it's most likely a PTT/keying lead-in artifact. Audio recorded before
# that first gap is dropped rather than handed to pycw, which otherwise
# reads it as one long, phantom leading dash.
MAX_LEAD_ON_MS = 2000.0


def _wpm_from_text(text: str, duration_ms: float) -> int:
    """Rough WPM estimate from decoded text and the transmission's audio
    duration. Not exact (one wrong character skews it) — just enough for
    a live readout: reconstructs the total dot-units the standard Morse
    timing rules imply for this text, then divides the known duration by
    that.
    """
    if not text or duration_ms <= 0:
        return 0
    units = 0.0
    words = text.split(" ")
    for wi, word in enumerate(words):
        chars = [c for c in word if c in CHAR_TO_MORSE]
        for ci, ch in enumerate(chars):
            code = CHAR_TO_MORSE[ch]
            for ei, elem in enumerate(code):
                units += 3.0 if elem == "-" else 1.0
                if ei < len(code) - 1:
                    units += 1.0
            if ci < len(chars) - 1:
                units += 3.0
        if wi < len(words) - 1:
            units += 7.0
    if units <= 0:
        return 0
    unit_ms = duration_ms / units
    return int(round(1200.0 / unit_ms)) if unit_ms > 0 else 0


class MorseDecoder(QObject):
    """Detects CW transmissions and decodes each with pycw."""

    # Decoded text for one transmission at a time (plus a trailing "\n")
    text_decoded = pyqtSignal(str)
    # signal_active (activity LED), estimated WPM
    status_changed = pyqtSignal(bool, int)
    # dominant CW tone frequency detected (Hz); 0 = auto scan mode
    tone_detected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._enabled = False

        self._tone_hz = 0.0            # 0 = automatic tone scanning
        self._bank = ToneBank(window_size=INTEG_SAMPLES)
        self._gate = ToneGate()
        self._last_reported_tone = 0

        # debounce: state changes commit only after two identical windows
        self._last_raw = False
        self._raw_count = 0

        self._pending = bytearray()
        self._reported_process_error = False

        self._recording = False        # currently buffering a transmission
        self._silence_ms = 0.0
        self._utterance = bytearray()
        self._lead_only = False        # no real gap seen yet this recording
        self._lead_ms = 0.0

        self._status_active = False
        self._wpm = 0

        self._pycw_decoder = None      # lazy: only built once actually used

        self._debug_log = None
        self._debug_t0 = None
        if DEBUG_LOG_PATH:
            self._debug_log = open(DEBUG_LOG_PATH, "a", buffering=1)
            self._debug_log.write(
                "\n# --- new session ---\n"
                "t_ms,mag,freq,floor,peak,thr_open,thr_close,is_open,"
                "recording,silence_ms,event\n"
            )

    def _debug(self, mag: float, event: str = "") -> None:
        if not self._debug_log:
            return
        now = time.monotonic()
        if self._debug_t0 is None:
            self._debug_t0 = now
        g = self._gate
        self._debug_log.write(
            f"{(now - self._debug_t0) * 1000.0:.1f},"
            f"{mag:.2f},"
            f"{self._bank.dominant_freq:.0f},"
            f"{g.floor:.2f},{g.peak:.2f},"
            f"{g.last_thr_open:.2f},{g.last_thr_close:.2f},"
            f"{int(g.is_open)},{int(self._recording)},"
            f"{self._silence_ms:.1f},{event}\n"
        )

    # ── Public API ──────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tone_hz(self) -> float:
        return self._tone_hz

    @property
    def detected_tone_hz(self) -> int:
        """Currently tracked dominant tone in Hz (0 when no signal)."""
        return int(round(self._bank.dominant_freq))

    def set_tone(self, freq_hz) -> None:
        """Pin the decoder to a fixed tone, or 0 to auto-scan the passband."""
        freq = float(freq_hz or 0.0)
        if freq < 0:
            return
        self._tone_hz = freq
        self._bank = ToneBank(
            window_size=INTEG_SAMPLES, manual_tone=freq if freq > 0 else 0.0
        )
        self._last_reported_tone = 0

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled == self._enabled:
                return
            self._enabled = enabled
            if enabled:
                self._reset()
                self.status_changed.emit(False, self._wpm)
            else:
                self._pending.clear()
                self._utterance.clear()
                self._recording = False

    def feed_pcm(self, data: bytes) -> None:
        """Feed a chunk of 8 kHz mono S16LE PCM (any size)."""
        if not self._enabled:
            return
        self._pending.extend(data)
        # consume HOP_SAMPLES per step, evaluating INTEG_SAMPLES from the
        # head of the buffer (INTEG == HOP: no overlap)
        while len(self._pending) >= INTEG_SAMPLES * 2:
            chunk = bytes(self._pending[: INTEG_SAMPLES * 2])
            del self._pending[: HOP_SAMPLES * 2]
            try:
                self._process_window(chunk)
            except Exception:
                # The RX reader thread that calls feed_pcm() swallows any
                # exception raised here (so a decoder bug can't take down
                # audio playback) — which also means one would otherwise
                # vanish without a trace. Report it once so it's not
                # mistaken for "nothing was received".
                if not self._reported_process_error:
                    self._reported_process_error = True
                    import traceback

                    print("[morse] window processing failed:", file=sys.stderr)
                    traceback.print_exc()
        if len(self._pending) > MAX_PENDING_BYTES:
            del self._pending[: len(self._pending) - MAX_PENDING_BYTES]

    # ── Internals ───────────────────────────────────────────────────

    def _reset(self) -> None:
        self._pending.clear()
        self._reported_process_error = False
        self._bank = ToneBank(
            window_size=INTEG_SAMPLES, manual_tone=self._tone_hz
        )
        self._last_reported_tone = 0
        self._last_raw = False
        self._raw_count = 0
        self._recording = False
        self._silence_ms = 0.0
        self._utterance.clear()
        self._lead_only = False
        self._lead_ms = 0.0
        self._status_active = False
        self._wpm = 0
        self.status_changed.emit(False, 0)

    def _process_window(self, chunk: bytes) -> None:
        raw = array.array("h")
        raw.frombytes(chunk)
        mag, freq = self._bank.process(raw)

        # report the tracked tone only when it moves meaningfully
        if abs(freq - self._last_reported_tone) >= 100.0:
            self._last_reported_tone = freq
            self.tone_detected.emit(int(round(freq)))

        raw_on = self._gate.process(mag)
        if self._debug_log:
            self._debug(mag)
        self._advance(raw_on, chunk)

    def _advance(self, raw_on: bool, chunk: bytes) -> None:
        """Two-window debounce, then feed the transmission buffer.

        Precise per-element timing no longer matters here — that's
        pycw's job, working from the raw waveform, so once a
        transmission is being recorded every window's audio is kept
        unconditionally (no gaps): only *starting* a fresh recording is
        debounced, to avoid a single noise blip kicking one off.
        """
        if raw_on == self._last_raw:
            self._raw_count += 1
        else:
            self._last_raw = raw_on
            self._raw_count = 1
        confirmed_on = self._raw_count >= 2 and self._last_raw

        if self._recording:
            if confirmed_on:
                self._silence_ms = 0.0
                if self._lead_only:
                    self._lead_ms += WINDOW_MS
                    if self._lead_ms > MAX_LEAD_ON_MS:
                        self._utterance.clear()  # drop the anomalous lead-in
                        if self._debug_log:
                            self._debug(0.0, "lead_in_discarded")
                    else:
                        self._utterance.extend(chunk)
                else:
                    self._utterance.extend(chunk)
                if not self._status_active:
                    self._status_active = True
                    self.status_changed.emit(True, self._wpm)
            else:
                self._lead_only = False  # a real gap: normal buffering now
                self._utterance.extend(chunk)
                self._silence_ms += WINDOW_MS
                if self._status_active and self._silence_ms > STATUS_LED_HOLD_MS:
                    self._status_active = False
                    self.status_changed.emit(False, self._wpm)
                if self._silence_ms > END_OF_TX_MS:
                    self._flush_utterance()
                    return
            if len(self._utterance) > MAX_UTTERANCE_S * SAMPLE_RATE * 2:
                self._flush_utterance()
        elif confirmed_on:
            self._recording = True
            self._silence_ms = 0.0
            self._lead_only = True
            self._lead_ms = 0.0
            self._utterance.clear()
            self._utterance.extend(chunk)
            if self._debug_log:
                self._debug(0.0, "utterance_start")
            if not self._status_active:
                self._status_active = True
                self.status_changed.emit(True, self._wpm)

    def _flush_utterance(self) -> None:
        pcm = bytes(self._utterance)
        self._utterance.clear()
        self._recording = False
        self._silence_ms = 0.0

        duration_ms = len(pcm) / 2.0 / SAMPLE_RATE * 1000.0
        if duration_ms < MIN_UTTERANCE_MS:
            if self._debug_log:
                self._debug(0.0, f"utterance_dropped_short({duration_ms:.0f}ms)")
            return

        text = self._decode_utterance(pcm)
        if self._debug_log:
            self._debug(0.0, f"utterance_flush({duration_ms:.0f}ms->{text!r})")
        if text:
            self._wpm = _wpm_from_text(text, duration_ms)
            self.status_changed.emit(False, self._wpm)
            self.text_decoded.emit(text)
            self.text_decoded.emit("\n")

    def _decode_utterance(self, pcm: bytes) -> str:
        try:
            import numpy as np
            import pycw
            from pycw.decoder.features import detect_tone

            if self._pycw_decoder is None:
                self._pycw_decoder = pycw.Decoder()
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            samples /= 32768.0
            if self._tone_hz > 0:
                tone = self._tone_hz
            else:
                # pycw's own auto-detect only searches 350-1700 Hz, which
                # misses low CW tones some operators use (300-350 Hz is
                # within this app's own passband); redo its own precise
                # FFT-based search with that floor lowered, rather than
                # feeding it a hint from our own coarse ~200 Hz-resolution
                # tracker, which isn't precise enough for the coherent
                # demodulation pycw's feature extraction does at whatever
                # frequency it's given (an imprecise hint measurably
                # corrupts the decode, confirmed by testing).
                tone = detect_tone(samples, SAMPLE_RATE, lo=250, hi=1700)
            text = self._pycw_decoder.decode(samples, SAMPLE_RATE, tone=tone)
        except Exception:
            # Surfaced instead of silently swallowed: a missing/broken
            # pycw install must not look identical to "nothing was said".
            import traceback

            print("[morse] pycw decode failed:", file=sys.stderr)
            traceback.print_exc()
            if self._debug_log:
                self._debug(0.0, f"decode_error({traceback.format_exc()!r})")
            return ""
        return text.strip().upper()
