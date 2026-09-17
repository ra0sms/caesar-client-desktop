"""Offline self-test for the Morse decoder.

Generates synthetic CW audio (no network, no GStreamer) and feeds it
straight into :class:`MorseDecoder`, printing the decoded text.

Usage:
    python -m morse.self_test                      # sweep tones/WPM/noise
    python -m morse.self_test "CQ CQ DE RA0SMS" --wpm 22 --tone 700 --noise 0.05
"""

import argparse
import array
import math
import random
import sys

from PyQt5.QtCore import QCoreApplication

from morse.decoder import MorseDecoder, SAMPLE_RATE
from morse.table import CHAR_TO_MORSE

DEFAULT_MESSAGE = "CAESAR TEST 123"
AMPLITUDE = 8000.0

# (wpm, tone_hz, noise_fraction)
SWEEP_CASES = [
    (8, 300, 0.02),
    (8, 1150, 0.02),
    (10, 350, 0.02),
    (10, 500, 0.02),
    (12, 900, 0.03),
    (15, 450, 0.05),
    (15, 700, 0.02),
    (18, 1200, 0.02),
    (20, 300, 0.05),
    (20, 600, 0.02),
    (22, 850, 0.05),
    (25, 750, 0.05),
    (30, 400, 0.02),
    (30, 950, 0.08),
    (35, 650, 0.08),
    (40, 550, 0.02),
    (40, 1100, 0.08),
]


def _add_tone(samples, duration_s, tone, amplitude=AMPLITUDE):
    n = int(round(SAMPLE_RATE * duration_s))
    for i in range(n):
        samples.append(amplitude * math.sin(2.0 * math.pi * tone * i / SAMPLE_RATE))


def _add_silence(samples, duration_s):
    n = int(round(SAMPLE_RATE * duration_s))
    samples.extend([0.0] * n)


def generate_message(message, wpm, tone=600.0, amplitude=AMPLITUDE):
    """Generate PCM samples for a text message at the given speed."""
    unit_s = 1.2 / wpm
    samples = []

    for wi, word in enumerate(message.split()):
        for ci, ch in enumerate(word):
            code = CHAR_TO_MORSE[ch.upper()]
            for ei, elem in enumerate(code):
                # A dash is three units long, a dot is one unit
                element_units = 1 if elem == "." else 3
                _add_tone(samples, element_units * unit_s, tone, amplitude)
                if ei < len(code) - 1:
                    _add_silence(samples, unit_s)  # intra-character gap
            if ci < len(word) - 1:
                _add_silence(samples, 3.0 * unit_s)  # character gap
        if wi < len(message.split()) - 1:
            _add_silence(samples, 7.0 * unit_s)  # word gap

    _add_silence(samples, 3.5)  # end of transmission
    return samples


def run(message, wpm, tone, noise_frac) -> bool:
    """Feed synthetic CW to the decoder (tone is auto-detected)."""
    samples = generate_message(message, wpm, tone)
    buf = array.array("h")
    for s in samples:
        v = s + random.uniform(-1.0, 1.0) * AMPLITUDE * noise_frac
        v = max(-32768, min(32767, int(v)))
        buf.append(v)

    data = buf.tobytes()

    decoder = MorseDecoder()
    collected = []
    decoder.text_decoded.connect(collected.append)
    decoder.set_enabled(True)

    for i in range(0, len(data), 4096):
        decoder.feed_pcm(data[i : i + 4096])

    out = "".join(collected)
    normalized = out.replace("\n", " ").replace("  ", " ").strip()
    ok = normalized == message.upper()

    print(
        f"{'PASS' if ok else 'FAIL'}  wpm={wpm:2d} tone={tone:4.0f}Hz "
        f"noise={noise_frac:.2f}  ->  {normalized or '(nothing)'}"
    )
    return ok


def main() -> int:
    app = QCoreApplication([])  # noqa: F841 - needed for Qt signals

    parser = argparse.ArgumentParser(description="Morse decoder offline self-test")
    parser.add_argument("message", nargs="?", default=DEFAULT_MESSAGE)
    parser.add_argument("--wpm", type=int, default=None)
    parser.add_argument("--tone", type=float, default=600.0)
    parser.add_argument("--noise", type=float, default=0.05)
    args = parser.parse_args()

    if args.wpm is not None:
        cases = [(args.wpm, args.tone, args.noise)]
    else:
        cases = SWEEP_CASES

    ok = True
    for wpm, tone, noise in cases:
        ok = run(args.message, wpm, tone, noise) and ok

    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())