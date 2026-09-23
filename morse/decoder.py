"""Real-time Morse (CW) decoder built on the DeepCW neural model.

Consumes 8 kHz mono S16LE PCM (produced by the RX audio tap branch in
``audio/rx.py``) and emits decoded text / status updates via Qt signals.

Decoding strategy
-----------------
This is a port of the streaming loop of the DeepCW web decoder
(``useStreamingDecode.ts``) on top of the deepcw-engine model (see
``morse/deepcw_engine.py``). Incoming audio accumulates in a pending
buffer; after every half second of new audio the whole pending buffer (up
to the model's 20 s limit) is decoded again from scratch.

Each character is shown as soon as it has settled: it is at least
EARLY_GUARD_S old, the model is confident about it, and the previous pass
saw the same character at the same place (typically ~1 s after the
character was sent). Audio is only dropped from the buffer at a word gap
safely before the end of the buffer, or once the tone has really been off
for a while (the transmission paused or ended), because the model needs
the surrounding context to decode reliably; whatever is still unsettled
at that point is shown then.

Threading contract:
  * ``feed_pcm()`` is called from the RX reader thread and only appends
    to the buffer;
  * model inference runs on a dedicated worker thread (or inline when
    constructed with ``threaded=False``, used by the offline self-test);
  * signals are emitted from that thread; PyQt delivers them to the GUI
    thread through queued connections.
"""

import os
import sys
import threading
import time
from typing import List, Optional

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from morse.table import CHAR_TO_MORSE

SAMPLE_RATE = 8000

ANALYSIS_STEP_S = 0.5     # re-decode after this much new audio
MIN_PENDING_S = 2.0       # don't bother decoding less than this
MAX_SEGMENT_S = 20.0      # longest window the engine model accepts
TAIL_GUARD_S = 1.25       # the newest audio may hold a partial character
EARLY_GUARD_S = 0.7       # a character this old (and seen twice) is shown
MIN_CONFIRMED_S = 2.0     # never commit a sliver shorter than this
TRAILING_QUIET_S = 1.5    # nothing decoded this long at the end: commit all
PAUSE_MIN_S = 1.0         # ...if the tone has also really been off this long
CHAR_TAIL_S = 0.5         # keep this much audio after the last committed char
END_OF_TX_S = 3.0         # no characters this long: end the line
IDLE_KEEP_S = 3.0         # with nothing decoded, keep only this much audio
LED_HOLD_S = 2.0          # activity LED stays lit this long after a character
MAX_BUFFER_S = 60.0       # hard cap if inference ever falls behind
CONFIRM_TOLERANCE_S = 0.25  # same character this close in the previous pass
MAX_POSTPONE = 2          # passes to wait for an unconfirmed character
# A shown character's label may drift a frame or two between passes; this
# must stay below the closest two letters can be (4 units, ~0.1 s at 40 WPM).
DEDUP_TOLERANCE_S = 0.08
# Model confidence (peak posterior of a character's label). Real characters
# score ~1.0 on any decent signal; hallucinations on pure noise came out at
# 0.12-0.67 in testing. Only confident characters take the fast path; and a
# window with no confident character at all is treated as noise.
FAST_PATH_MIN_PROB = 0.9
NOISE_WINDOW_MIN_PROB = 0.7

# Optional diagnostic trace: set this environment variable to a file path
# to log every analysis pass (and dump the raw PCM to <path>.pcm, headerless
# 8 kHz mono S16LE, e.g. `sox -r 8000 -e signed -b 16 -c 1 <path>.pcm out.wav`).
DEBUG_LOG_PATH = os.environ.get("CAESAR_MORSE_DEBUG")


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _estimate_wpm(chars, frame_seconds: float) -> int:
    """Speed from the spacing of committed characters (CTC fires near the
    end of each character, so end-to-end distance covers every element
    and gap after the first character)."""
    units = 0.0
    first_end = last_end = None
    word_gap = False
    for span in chars:
        if span.char == " ":
            word_gap = True
            continue
        code = CHAR_TO_MORSE.get(span.char)
        if not code:
            continue
        element_units = sum(3 if e == "-" else 1 for e in code) + len(code) - 1
        if first_end is None:
            first_end = span.end_frame
        else:
            units += (7 if word_gap else 3) + element_units
            last_end = span.end_frame
        word_gap = False
    if last_end is None or units <= 0:
        return 0
    duration_ms = (last_end - first_end) * frame_seconds * 1000.0
    if duration_ms <= 0:
        return 0
    wpm = int(round(1200.0 / (duration_ms / units)))
    return wpm if 3 <= wpm <= 80 else 0


class MorseDecoder(QObject):
    """Decodes CW from raw PCM into text with the DeepCW model."""

    # Decoded characters as they settle (with word spaces), "\n" at end of a line
    text_decoded = pyqtSignal(str)
    # signal_active (activity LED), estimated WPM
    status_changed = pyqtSignal(bool, int)
    # dominant CW tone frequency detected (Hz)
    tone_detected = pyqtSignal(int)

    def __init__(self, parent=None, threaded: bool = True):
        super().__init__(parent)
        self._threaded = threaded
        self._enabled = False
        self._tone_hz = 0.0            # 0 = automatic tone detection

        self._buf_lock = threading.Lock()
        self._buffer = bytearray()
        self._new_samples = 0
        self._dropped_samples = 0      # stream position of buffer start

        self._step_lock = threading.Lock()
        self._wake = threading.Condition()
        self._worker: Optional[threading.Thread] = None
        self._generation = 0           # bumped on every enable/disable

        self._engine = None
        self._engine_failed = False

        self._reset_state()

        self._debug_log = None
        self._debug_pcm = None
        self._debug_t0 = time.monotonic()
        if DEBUG_LOG_PATH:
            self._debug_pcm = open(DEBUG_LOG_PATH + ".pcm", "ab")
            self._debug_log = open(DEBUG_LOG_PATH, "a", buffering=1)
            self._debug_log.write(
                "\n# --- new session ---\n"
                "t_ms,pending_s,tone_hz,decoded,committed,event\n"
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
        return int(round(self._detected_tone or 0.0))

    def set_tone(self, freq_hz) -> None:
        """Pin the decoder to a fixed tone, or 0 for automatic detection."""
        freq = float(freq_hz or 0.0)
        if freq >= 0:
            self._tone_hz = freq

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            with self._buf_lock:
                self._buffer.clear()
                self._new_samples = 0
                self._dropped_samples = 0
            self._reset_state()
            self.status_changed.emit(False, 0)
            if self._threaded:
                self._generation += 1
                self._worker = threading.Thread(
                    target=self._run, args=(self._generation,), daemon=True,
                    name="morse-decoder",
                )
                self._worker.start()
        else:
            self._generation += 1
            with self._wake:
                self._wake.notify_all()
            self._worker = None
            with self._buf_lock:
                self._buffer.clear()

    def feed_pcm(self, data: bytes) -> None:
        """Feed a chunk of 8 kHz mono S16LE PCM (any size)."""
        if not self._enabled:
            return
        if self._debug_pcm:
            self._debug_pcm.write(data)
        with self._buf_lock:
            self._buffer.extend(data)
            self._new_samples += len(data) // 2
            overflow = len(self._buffer) - int(MAX_BUFFER_S * SAMPLE_RATE) * 2
            if overflow > 0:
                overflow -= overflow % 2
                del self._buffer[:overflow]
                self._dropped_samples += overflow // 2
            due = self._new_samples >= ANALYSIS_STEP_S * SAMPLE_RATE
        if not due:
            return
        if self._threaded:
            with self._wake:
                self._wake.notify()
        else:
            self._step()

    def flush(self) -> None:
        """Decode and commit everything still pending (end of stream)."""
        if self._enabled:
            self._step(final=True)

    # ── Internals ───────────────────────────────────────────────────

    def _reset_state(self) -> None:
        self._detected_tone: Optional[float] = None
        self._space_pending = False
        self._last_emitted_s = -1e9    # stream time of the last shown character
        self._line_open = False
        self._last_char_pos = 0        # stream position (samples)
        self._status_active = False
        self._wpm = 0
        self._prev_chars = []          # (char, stream time) from the last pass
        self._postponed = 0

    def _seen_before(self, char: str, at_s: float) -> bool:
        return any(
            c == char and abs(t - at_s) <= CONFIRM_TOLERANCE_S for c, t in self._prev_chars
        )

    def _run(self, generation: int) -> None:
        # A worker from a previous enable may still be waking up after a
        # quick disable/enable; it must exit rather than run alongside.
        while generation == self._generation:
            with self._wake:
                while generation == self._generation:
                    with self._buf_lock:
                        due = self._new_samples >= ANALYSIS_STEP_S * SAMPLE_RATE
                    if due:
                        break
                    self._wake.wait(timeout=0.5)
            if generation != self._generation:
                return
            try:
                self._step()
            except Exception:
                import traceback

                print("[morse] decode step failed:", file=sys.stderr)
                traceback.print_exc()
                time.sleep(1.0)

    def _get_engine(self):
        if self._engine is None and not self._engine_failed:
            try:
                from morse.deepcw_engine import DeepCwEngine

                self._engine = DeepCwEngine()
            except Exception:
                self._engine_failed = True
                import traceback

                print("[morse] failed to load the DeepCW model:", file=sys.stderr)
                traceback.print_exc()
        return self._engine

    def _update_tone(self, window: np.ndarray) -> Optional[float]:
        if self._tone_hz > 0:
            tone = self._tone_hz
        else:
            from morse.deepcw_engine import detect_tone

            found = detect_tone(window, SAMPLE_RATE)
            tone = found if found is not None else self._detected_tone
        if tone is not None and (
            self._detected_tone is None or abs(tone - self._detected_tone) >= 25.0
        ):
            self._detected_tone = tone
            self.tone_detected.emit(int(round(tone)))
        return self._detected_tone if self._tone_hz <= 0 else tone

    def _step(self, final: bool = False) -> None:
        with self._step_lock:
            engine = self._get_engine()
            with self._buf_lock:
                self._new_samples = 0
                raw = bytes(self._buffer[: int(MAX_SEGMENT_S * SAMPLE_RATE) * 2])
                pending = len(self._buffer) // 2
                stream_end = self._dropped_samples + pending
            if engine is None:
                return
            if pending < MIN_PENDING_S * SAMPLE_RATE and not final:
                return

            window = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            tone = self._update_tone(window)
            analysis = engine.analyze(window, SAMPLE_RATE, tone)
            window_s = len(window) / SAMPLE_RATE
            whole = pending * 2 <= len(raw)   # window covers the whole buffer

            chars = [c for c in analysis.chars]
            if not any(c.prob >= FAST_PATH_MIN_PROB for c in chars if c.char != " "):
                # no character the model is sure about: likely pure noise,
                # so keep only what it is at least fairly sure about
                chars = [c for c in chars if c.char == " " or c.prob >= NOISE_WINDOW_MIN_PROB]
                if not any(c.char != " " for c in chars):
                    chars = []
                analysis.chars = chars
            cut_s = None
            committed = []
            cut_at_gap = True   # False only when forced to cut mid-word
            if chars:
                last_s = analysis.frame_to_seconds(chars[-1].end_frame)
                if final or (
                    whole and last_s <= window_s - TRAILING_QUIET_S
                    and self._silent_to_end(analysis, chars[-1], window_s)
                ):
                    committed = chars
                    cut_s = window_s if final else self._end_of_char_cut(analysis, chars[-1], window_s)
                else:
                    cut_s, committed = self._word_space_cut(analysis, window_s, near_end=False)
                    if cut_s is None and not whole:
                        cut_s, committed = self._word_space_cut(analysis, window_s, near_end=True)
                        if cut_s is None:
                            cut_s, committed = window_s, chars
                            cut_at_gap = False
            elif final:
                cut_s = window_s
            elif pending > (IDLE_KEEP_S + ANALYSIS_STEP_S) * SAMPLE_RATE:
                cut_s = (pending / SAMPLE_RATE) - IDLE_KEEP_S

            # On pure noise the model now and then hallucinates a character,
            # but such phantoms rarely survive the next pass (the same audio
            # decoded in a different window), while real characters do. So
            # only commit characters the previous pass also saw at the same
            # place; otherwise wait a pass or two before giving up on them.
            base_s = self._dropped_samples / SAMPLE_RATE
            if committed:
                unconfirmed = [
                    c for c in committed
                    if c.char != " " and not self._seen_before(
                        c.char, base_s + analysis.frame_to_seconds(c.start_frame)
                    )
                ]
                if not unconfirmed:
                    self._postponed = 0
                elif self._postponed < MAX_POSTPONE and not final:
                    self._postponed += 1
                    cut_s, committed = None, []
                else:
                    self._postponed = 0
                    committed = [c for c in committed if all(c is not u for u in unconfirmed)]
            text = self._emit_settled(analysis, chars, committed, window_s, base_s)
            self._prev_chars = [
                (c.char, base_s + analysis.frame_to_seconds(c.start_frame))
                for c in chars if c.char != " "
            ]
            if (
                cut_s is not None and committed and cut_at_gap
                and self._last_emitted_s < base_s + cut_s
            ):
                # the word gap (or pause) goes away with the audio being cut,
                # so the next character starts a new word — unless it was
                # already shown (with its space) before this cut
                self._space_pending = True

            if chars:
                self._last_char_pos = max(
                    self._last_char_pos,
                    self._dropped_samples
                    + int(analysis.frame_to_seconds(chars[-1].end_frame) * SAMPLE_RATE),
                )
            quiet_s = (stream_end - self._last_char_pos) / SAMPLE_RATE
            if self._line_open and (final or (not chars and quiet_s >= END_OF_TX_S)):
                self.text_decoded.emit("\n")
                self._line_open = False
                self._space_pending = False

            active = bool(chars) and quiet_s <= LED_HOLD_S
            if active != self._status_active or text:
                self._status_active = active
                self.status_changed.emit(active, self._wpm)

            if cut_s is not None and cut_s > 0:
                cut = int(cut_s * SAMPLE_RATE)
                with self._buf_lock:
                    cut = min(cut, len(self._buffer) // 2)
                    del self._buffer[: cut * 2]
                    self._dropped_samples += cut

            if self._debug_log:
                decoded = _normalize("".join(c.char for c in chars))
                self._debug_log.write(
                    f"{(time.monotonic() - self._debug_t0) * 1000.0:.0f},"
                    f"{pending / SAMPLE_RATE:.2f},{tone or 0:.0f},"
                    f"{decoded!r},{text!r},{'final' if final else ''}\n"
                )

    def _emit_settled(self, analysis, chars, committed, window_s: float, base_s: float) -> str:
        """Show every character that can no longer change, one at a time.

        Audio is still only cut at word gaps (the model needs the context),
        but a character doesn't have to wait for the end of its word: once
        it is EARLY_GUARD_S old and the previous pass saw the same character
        at the same place, the model won't revise it. Characters that are
        part of this pass's commit are shown regardless. Emission stops at
        the first unsettled character so the text never gets reordered.
        """
        committed_ids = {id(c) for c in committed}
        commit_end = committed[-1].end_frame if committed else -1
        out = []
        space_between = False
        for c in chars:
            at_s = base_s + analysis.frame_to_seconds(c.start_frame)
            if c.char == " ":
                # the space label sits right behind the previous letter's
                # label, so it must not fall under the duplicate check below
                if at_s > self._last_emitted_s:
                    space_between = True
                continue
            if at_s <= self._last_emitted_s + DEDUP_TOLERANCE_S:
                continue
            settled = id(c) in committed_ids or (
                c.prob >= FAST_PATH_MIN_PROB
                and analysis.frame_to_seconds(c.end_frame) <= window_s - EARLY_GUARD_S
                and self._seen_before(c.char, at_s)
            )
            if not settled:
                if c.end_frame <= commit_end:
                    continue   # dropped as unconfirmed, audio is being cut anyway
                break
            if self._line_open and (space_between or self._space_pending):
                out.append(" ")
            out.append(c.char)
            space_between = False
            self._space_pending = False
            self._line_open = True
            self._last_emitted_s = at_s
            self._last_char_pos = max(
                self._last_char_pos,
                self._dropped_samples + int(analysis.frame_to_seconds(c.end_frame) * SAMPLE_RATE),
            )
        text = "".join(out)
        if text:
            wpm = _estimate_wpm(chars, analysis.frame_seconds)
            if wpm:
                self._wpm = wpm
            self.text_decoded.emit(text)
        return text

    def _unit_s(self, analysis) -> float:
        wpm = _estimate_wpm(analysis.chars, analysis.frame_seconds) or self._wpm or 20
        return 1.2 / wpm

    @staticmethod
    def _quiet_run_after(analysis, frame: int, min_len_s: float):
        """First stretch of real silence (tone envelope below a level halfway
        between noise and signal) starting at or after `frame` and lasting at
        least `min_len_s`: returns (start_s, end_s) or None."""
        env = analysis.envelope
        if len(env) == 0:
            return None
        floor = float(np.percentile(env, 25))
        peak = float(np.percentile(env, 95))
        if peak - floor <= 1e-6:
            return None
        quiet = env < floor + 0.4 * (peak - floor)
        min_frames = max(1, int(round(min_len_s / analysis.frame_seconds)))
        start = None
        for i in range(max(0, frame), len(env)):
            if quiet[i]:
                if start is None:
                    start = i
                if i - start + 1 >= min_frames:
                    end = i
                    while end + 1 < len(env) and quiet[end + 1]:
                        end += 1
                    return (analysis.frame_to_seconds(start), analysis.frame_to_seconds(end))
            else:
                start = None
        return None

    def _silent_to_end(self, analysis, char, window_s: float) -> bool:
        """Is the audio after `char` really silent up to the end of the window?

        "Nothing new decoded for a while" is not enough on its own: at slow
        speeds a long character (a digit takes ~2 s at 10 WPM) is still
        being sent when that holds, and cutting then splits a word."""
        run = self._quiet_run_after(analysis, char.start_frame, 2.0 * self._unit_s(analysis))
        if run is None:
            return False
        reaches_end = run[1] >= window_s - 2.0 * analysis.frame_seconds
        return reaches_end and window_s - run[0] >= PAUSE_MIN_S

    def _end_of_char_cut(self, analysis, char, window_s: float) -> float:
        """Cut point just after the audio of `char` really ends.

        The model's label for a character does not reliably sit on its very
        last element, so cutting a fixed distance after the label can leave
        the tail of a dash behind, which then decodes as a phantom
        character. Instead look in the tone envelope for the first silence
        at least two units long (longer than any gap inside a character)."""
        run = self._quiet_run_after(analysis, char.start_frame, 2.0 * self._unit_s(analysis))
        if run is None:
            return min(window_s, analysis.frame_to_seconds(char.end_frame) + CHAR_TAIL_S)
        return min(window_s, run[0] + min(0.15, (run[1] - run[0]) / 2.0))

    def _word_space_cut(self, analysis, window_s: float, near_end: bool):
        """Latest word space safely inside the window: (cut_s, chars before it).

        The engine model fires its space label at the very start of a word
        gap, so cutting on the label itself leaves a sliver of the previous
        element in the next window (decoded as a phantom "E"). Cut in the
        middle of the actual silence that follows it instead."""
        unit_s = self._unit_s(analysis)
        limit = window_s if near_end else max(MIN_CONFIRMED_S, window_s - TAIL_GUARD_S)
        for span in reversed(analysis.word_spaces):
            run = self._quiet_run_after(analysis, span.start_frame - 2, 2.0 * unit_s)
            if run is not None and run[0] <= analysis.frame_to_seconds(span.end_frame) + 3.0 * unit_s:
                split_s = (run[0] + run[1]) / 2.0
            else:
                split_s = analysis.frame_to_seconds(span.start_frame) + 3.5 * unit_s
            if MIN_CONFIRMED_S <= split_s <= limit:
                chars: List = [c for c in analysis.chars if c.end_frame <= span.end_frame]
                return split_s, chars
        return None, []
