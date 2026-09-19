"""Real-time Morse (CW) decoder.

Consumes 8 kHz mono S16LE PCM (produced by the RX audio tap branch in
``audio/rx.py``) and emits decoded characters / status updates via Qt
signals. All DSP is pure Python and deliberately cheap (tens of
thousands of operations per second), so it can safely run inside the
audio reader thread without disturbing the rest of the application.

Threading contract:
  * ``feed_pcm()`` is called from the RX reader thread (producer);
  * signals are emitted from that same thread; PyQt delivers them to
    the GUI thread through queued connections.

Timing strategy
---------------
The dot length is estimated adaptively from the durations of both marks
and spaces (dots and intra-character gaps are both one unit long). The
estimate is the lower-quartile of the recent candidates, which is
robust against glitches and adapts when the operator changes speed.
Symbol classification is delayed by one element, so the very first
elements of a transmission are classified with an already-converged
unit estimate (no cold-start errors).
"""

import array
import os
import threading
import time
from collections import deque

from PyQt5.QtCore import QObject, pyqtSignal

from morse.dsp import SAMPLE_RATE, ToneBank, ToneGate
from morse.table import MORSE_CODE

# Optional per-window diagnostic trace. Set this environment variable to
# a file path before starting the app to capture exactly what the gate
# and tone tracker are doing on real audio — mag/floor/peak/thresholds
# for every 5ms window, plus a note on every glitch dropped, mark
# classified, or character/space emitted. Meant for tracking down
# decode problems that don't reproduce with synthetic test tones; has
# no effect at all unless the variable is set.
DEBUG_LOG_PATH = os.environ.get("CAESAR_MORSE_DEBUG")

# Processing window: 5 ms @ 8 kHz (finer = better timing at high WPM)
INTEG_SAMPLES = 40
HOP_SAMPLES = 40
WINDOW_MS = HOP_SAMPLES * 1000.0 / SAMPLE_RATE

# Timing rules (relative to the estimated dot length)
MIN_MARK_FRACTION = 0.35   # elements shorter than this are ignored (clicks)
MIN_GLITCH_ABS_MS = 8.0    # absolute floor for the glitch filter
# A real dash is at most 3 units; anything much longer than that cannot be
# a legitimate CW element (e.g. a PTT/keying lead-in, or the gate briefly
# failing to release on a held carrier) and would otherwise be classified
# as one phantom dash glued onto whatever character follows it. Only
# checked once the unit estimator has real data of its own (see
# `_finish_mark`) — at the very start of a transmission, before any
# element has been measured, there is no trustworthy speed to compare
# against, so the absolute floor (the slowest speed this decoder
# supports at all) is used instead.
MAX_MARK_RATIO = 6.0
MAX_MARK_COLD_START_MS = 1800.0  # 3 units at the slowest supported speed
# Marks measure ~1 window longer, gaps ~1 window shorter than reality,
# so the dot/dash threshold sits above the measured dot-to-gap ratio.
DOT_DASH_RATIO = 2.6       # elements longer than this are dashes
CHAR_GAP_DOTS = 2.0        # gap after which a character is finalized
WORD_GAP_DOTS = 5.0        # gap after which a space is emitted
CHAR_GAP_FEED_RATIO = 2.5  # only shorter gaps feed the unit estimator
END_OF_TX_MS = 3000.0      # silence longer than this => end of transmission

# Unit (dot) length estimation guards
UNIT_MIN_MS = 12.0         # ~100 WPM
UNIT_MAX_MS = 600.0        # ~2 WPM
DEFAULT_UNIT_MS = 60.0     # ~20 WPM starting point
UNITS_WINDOW = 16          # how many recent candidates feed the estimator

STATUS_LED_HOLD_MS = 300.0  # activity LED stays lit across short gaps
MAX_PENDING_BYTES = 65536   # safety cap for the leftover-sample buffer


class MorseDecoder(QObject):
    """Decodes CW tones from raw PCM into text."""

    # One decoded symbol / space / newline at a time
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

        self._in_mark = False
        self._mark_ms = 0.0
        self._space_ms = 0.0
        self._symbols = ""
        self._units = deque(maxlen=UNITS_WINDOW)
        self._unit_est_ms = DEFAULT_UNIT_MS
        self._last_mark_ms = None

        self._got_mark = False
        self._emit_space_ok = False
        self._tx_ended = False

        self._status_active = False
        self._wpm = 0

        self._debug_log = None
        self._debug_t0 = None
        if DEBUG_LOG_PATH:
            self._debug_log = open(DEBUG_LOG_PATH, "a", buffering=1)
            self._debug_log.write(
                "\n# --- new session ---\n"
                "t_ms,mag,freq,floor,peak,thr_open,thr_close,is_open,"
                "in_mark,mark_ms,space_ms,unit_est_ms,event\n"
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
            f"{int(g.is_open)},{int(self._in_mark)},"
            f"{self._mark_ms:.1f},{self._space_ms:.1f},"
            f"{self._unit_est_ms:.1f},{event}\n"
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
            self._process_window(chunk)
        if len(self._pending) > MAX_PENDING_BYTES:
            del self._pending[: len(self._pending) - MAX_PENDING_BYTES]

    # ── Internals ───────────────────────────────────────────────────

    def _reset(self) -> None:
        self._pending.clear()
        self._bank = ToneBank(
            window_size=INTEG_SAMPLES, manual_tone=self._tone_hz
        )
        self._last_reported_tone = 0
        self._last_raw = False
        self._raw_count = 0
        self._in_mark = False
        self._mark_ms = 0.0
        self._space_ms = 0.0
        self._symbols = ""
        self._units.clear()
        self._unit_est_ms = DEFAULT_UNIT_MS
        self._last_mark_ms = None
        self._got_mark = False
        self._emit_space_ok = False
        self._tx_ended = False
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

        on = self._gate.process(mag)
        if self._debug_log:
            self._debug(mag)
        self._advance(on)

    def _advance(self, signal_on: bool) -> None:
        """Timing state machine with a 2-window debounce.

        State changes commit only after two consecutive identical gate
        decisions, so single-window noise spikes cannot split elements or
        create phantom marks. Both windows are credited to the new state,
        which keeps the measured durations exact.
        """
        if signal_on == self._last_raw:
            self._raw_count += 1
        else:
            self._last_raw = signal_on
            self._raw_count = 1

        if self._raw_count < 2:
            return  # decision not confirmed yet

        committed = self._last_raw
        if committed == self._in_mark:
            # same state continues
            if committed:
                self._mark_ms += WINDOW_MS
            else:
                self._space_ms += WINDOW_MS
                if self._space_ms > END_OF_TX_MS and not self._tx_ended:
                    self._end_of_tx()
        else:
            # transition: credit both debounce windows to the new state
            if committed:
                self._in_mark = True
                self._tx_ended = False
                self._finish_space()
                self._mark_ms = 2 * WINDOW_MS
            else:
                self._in_mark = False
                self._finish_mark()
                self._space_ms = 2 * WINDOW_MS

        # Activity LED: stays lit across short inter-element gaps
        led = self._in_mark or self._space_ms <= STATUS_LED_HOLD_MS
        self._set_status(led)

    # ── Unit (dot length) estimation ────────────────────────────────

    def _push_unit_candidate(self, ms: float) -> None:
        if UNIT_MIN_MS <= ms <= UNIT_MAX_MS:
            self._units.append(ms)
            if len(self._units) < 2:
                return  # not enough data yet
            if len(self._units) < 4:
                # cold start: the shortest element seen so far is the dot
                self._unit_est_ms = min(self._units)
            else:
                # lower quartile: robust against dashes/gaps/glitches
                s = sorted(self._units)
                self._unit_est_ms = s[len(s) // 4]

    # ── Element handling ────────────────────────────────────────────

    def _finish_mark(self) -> None:
        """A mark ended: feed the estimator, defer classification."""
        self._got_mark = True
        ms = self._mark_ms
        if ms < min(self._unit_est_ms * MIN_MARK_FRACTION, MIN_GLITCH_ABS_MS):
            self._last_mark_ms = None  # glitch — drop
            if self._debug_log:
                self._debug(0.0, f"mark_dropped_glitch({ms:.1f}ms)")
            return
        max_valid = (
            self._unit_est_ms * MAX_MARK_RATIO
            if self._units
            else MAX_MARK_COLD_START_MS
        )
        if ms > max_valid:
            self._last_mark_ms = None  # anomaly — drop, don't corrupt symbols
            if self._debug_log:
                self._debug(0.0, f"mark_dropped_anomaly({ms:.1f}ms)")
            return
        # A real mark confirms we've found the wanted signal — stop
        # re-electing the dominant bin so a competing station or noise
        # burst during the next gap can't steal the lock (see
        # ToneBank.freeze). Tested against delaying this to the first
        # full character instead: that gave the bin-lock hysteresis more
        # exposure to the competing signal's own gaps and made QRM lock
        # onto the wrong station more often, not less.
        self._bank.freeze()
        self._push_unit_candidate(ms)
        self._last_mark_ms = ms

    def _finish_space(self) -> None:
        """A space ended (next mark started): classify, maybe emit."""
        ms = self._space_ms
        prev = self._unit_est_ms
        drop_thr = min(prev * MIN_MARK_FRACTION, MIN_GLITCH_ABS_MS)
        # only intra-character-ish gaps feed the unit estimator; long
        # char/word gaps would otherwise inflate the estimate
        if ms >= drop_thr and ms <= CHAR_GAP_FEED_RATIO * prev:
            self._push_unit_candidate(ms)

        # classify the mark that ended just before this gap — the unit
        # estimate now already includes that mark and this gap
        if self._last_mark_ms is not None:
            self._classify_mark(self._last_mark_ms)
            self._last_mark_ms = None

        unit = self._unit_est_ms
        if ms >= WORD_GAP_DOTS * unit:
            self._emit_char()
            if self._emit_space_ok:
                self.text_decoded.emit(" ")
                self._emit_space_ok = False
        elif ms >= CHAR_GAP_DOTS * unit:
            self._emit_char()

    def _classify_mark(self, ms: float) -> None:
        if ms <= self._unit_est_ms * DOT_DASH_RATIO:
            self._symbols += "."
        else:
            self._symbols += "-"
        if self._debug_log:
            self._debug(0.0, f"classified({ms:.1f}ms->{self._symbols[-1]})")

    def _emit_char(self) -> bool:
        if not self._symbols:
            return False
        ch = MORSE_CODE.get(self._symbols, "?")
        if self._debug_log:
            self._debug(0.0, f"emit_char({self._symbols}->{ch!r})")
        self._symbols = ""
        self._emit_space_ok = True
        self.text_decoded.emit(ch)
        return True

    def _end_of_tx(self) -> None:
        self._tx_ended = True
        if self._last_mark_ms is not None:
            self._classify_mark(self._last_mark_ms)
            self._last_mark_ms = None
        emitted = self._emit_char()
        if self._got_mark or emitted:
            self.text_decoded.emit("\n")
        self._got_mark = False
        self._symbols = ""
        self._units.clear()
        self._unit_est_ms = DEFAULT_UNIT_MS
        self._emit_space_ok = False
        # Transmission is over — allow the next one to re-acquire its own
        # tone (it may come from a different station).
        self._bank.unfreeze()

    def _set_status(self, active: bool) -> None:
        wpm = int(round(1200.0 / self._unit_est_ms)) if self._unit_est_ms > 0 else 0
        if active != self._status_active or wpm != self._wpm:
            self._status_active = active
            self._wpm = wpm
            self.status_changed.emit(active, wpm)