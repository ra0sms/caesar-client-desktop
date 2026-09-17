"""Single-line horizontally scrolling ticker widget ("бегущая строка").

Decoded text flows continuously from right to left. New characters are
appended to the stream and simply enter from the right edge when the
scroll position reaches them — the ticker is never reset to the start.
Text leaves the window at the left edge and only disappears there.
"""

import time

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import QWidget


class MarqueeLabel(QWidget):
    """One-line ticker that scrolls decoded text right-to-left."""

    SPEED_PX_PER_SEC = 45.0
    FPS = 30
    MAX_TEXT_CHARS = 20000  # safety cap; trimming preserves the scroll pos
    MAX_FRAME_DT = 0.2      # clamp a single tick's elapsed time (seconds)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._text = ""
        self._offset = 0.0          # pixels already scrolled (monotonic)
        self._last_tick = None

        self._bg = QColor("#0d1117")
        self._fg = QColor("#00ff88")

        font = QFont("Monospace")
        font.setPixelSize(15)
        font.setBold(True)
        self._font = font

        self.setMinimumHeight(32)
        self.setMaximumHeight(32)

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.FPS))
        self._timer.timeout.connect(self._tick)

    # ── Public API ──────────────────────────────────────────────────

    def append_text(self, s: str) -> None:
        """Append decoded text to the stream.

        The scroll position is never reset. If the previous content has
        already fully left the window, the position parks at the tail so
        the new text starts entering from the right edge.
        """
        if not s:
            return
        s = s.replace("\r", "").replace("\n", "  ·  ")

        old_width = self._text_width()
        self._text += s
        new_width = self._text_width()

        # everything scrolled out already -> park at the tail
        if self._offset >= old_width + self.width():
            self._offset = new_width

        self._trim_buffer()
        if not self._timer.isActive():
            # Anchor the clock to now before (re)starting: characters can
            # arrive in bursts (several decoded symbols delivered within
            # the same animation frame), and each burst must not disturb
            # an already-running clock — only a genuine restart from idle
            # needs a fresh reference, otherwise a stale timestamp from
            # before a long pause would produce one huge catch-up jump.
            self._last_tick = time.monotonic()
            self._timer.start()
        self.update()

    def clear(self) -> None:
        self._text = ""
        self._offset = 0.0
        self._last_tick = None
        self._timer.stop()
        self.update()

    # ── Internals ───────────────────────────────────────────────────

    def _text_width(self, fragment=None) -> int:
        fm = QFontMetrics(self._font)
        text = self._text if fragment is None else fragment
        if hasattr(fm, "horizontalAdvance"):
            return fm.horizontalAdvance(text)
        return fm.width(text)

    def _trim_buffer(self) -> None:
        """Drop the already-scrolled-out head, compensating the offset."""
        excess = len(self._text) - self.MAX_TEXT_CHARS
        if excess <= 0:
            return
        removed = self._text[:excess]
        self._offset -= self._text_width(removed)
        if self._offset < 0.0:
            self._offset = 0.0
        self._text = self._text[excess:]

    def _tick(self) -> None:
        now = time.monotonic()
        if self._last_tick is not None:
            # Clamp so a delayed/coalesced tick (GUI thread briefly busy)
            # cannot make the text jump straight past the window in one
            # frame, which would look like it vanished outright.
            dt = min(now - self._last_tick, self.MAX_FRAME_DT)
            self._offset += self.SPEED_PX_PER_SEC * dt
        self._last_tick = now
        self.update()

        # nothing left on screen -> pause until new text arrives
        if not self._text or self._offset >= self._text_width() + self.width():
            self._timer.stop()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg)
        if not self._text:
            return
        painter.setFont(self._font)
        painter.setPen(self._fg)
        fm = QFontMetrics(self._font)
        tw = self._text_width()
        if tw <= 0:
            return
        x = self.width() - int(self._offset)
        if x + tw <= 0:
            return  # fully scrolled out — nothing to draw
        baseline = (self.height() + fm.ascent() - fm.descent()) // 2
        painter.drawText(x, baseline, self._text)
        painter.end()