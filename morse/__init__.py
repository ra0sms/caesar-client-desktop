"""Automatic CW (Morse code) decoder for the RX audio tap.

The decoder consumes the raw 8 kHz mono S16LE PCM that the RX pipeline
branches off via ``tee`` + ``fdsink`` (see ``audio/rx.py``) and decodes
it with the DeepCW neural model (``morse/deepcw_engine.py``), emitting
the decoded text through Qt signals.
"""

from morse.decoder import MorseDecoder  # noqa: F401

__all__ = ["MorseDecoder"]