"""Single-line horizontally scrolling ticker widget ("бегущая строка").

Model: each character records the scroll distance at the moment it was
decoded ("birth"). Its on-screen position is always
``width() - (current_distance - birth)`` — nothing more. The distance
only ever counts up, driven by a timer that runs for the widget's whole
lifetime and is never stopped, started, or rewound.

This means a character's position depends only on its own birth time,
never on any other character or on how long the ticker was idle before
it. There is no "reset", "jump to tail", or "catch up" special case
anywhere: a character always enters exactly at the right edge the
moment it is decoded (current_distance ≈ its own birth), drifts left at
a constant speed, and is dropped once it is fully past the left edge.
A long silence simply shows as blank space for that long — it can
never cause older text to vanish or jump.
"""

import time
from collections import deque

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import QWidget


class MarqueeLabel(QWidget):
    """One-line ticker that scrolls decoded text right-to-left."""

    SPEED_PX_PER_SEC = 45.0
    FPS = 30
    MAX_FRAME_DT = 0.2   # clamp a single tick's elapsed time (seconds)
    MAX_CHARS = 2000     # generous safety cap, well beyond what's ever visible

    def __init__(self, parent=None):
        super().__init__(parent)

        self._chars = deque()   # (char, birth_distance_px), oldest first
        self._distance = 0.0    # total scroll distance so far (monotonic)
        self._last_tick = time.monotonic()

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
        self._timer.start()  # runs for the widget's whole lifetime

    # ── Public API ──────────────────────────────────────────────────

    def append_text(self, s: str) -> None:
        """Append decoded text. The first new character enters at the
        right edge the moment it's added, regardless of how long the
        ticker was idle before this call. Characters within the same
        call are spaced out by one character width each, exactly like
        normal text, instead of landing on top of each other."""
        if not s:
            return
        s = s.replace("\r", "").replace("\n", "  ·  ")
        advance = self._char_advance()
        birth = self._distance
        if self._chars:
            birth = max(birth, self._chars[-1][1] + advance)
        for ch in s:
            self._chars.append((ch, birth))
            birth += advance
        while len(self._chars) > self.MAX_CHARS:
            self._chars.popleft()
        self.update()

    def clear(self) -> None:
        self._chars.clear()
        self.update()

    # ── Internals ───────────────────────────────────────────────────

    def _char_advance(self) -> float:
        fm = QFontMetrics(self._font)
        if hasattr(fm, "horizontalAdvance"):
            return fm.horizontalAdvance("M")
        return fm.width("M")

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, self.MAX_FRAME_DT)
        self._last_tick = now
        self._distance += self.SPEED_PX_PER_SEC * dt

        # Housekeeping only: drop characters once fully past the left
        # edge. Has no effect on what's drawn for anything still visible.
        advance = self._char_advance()
        width = self.width()
        while self._chars:
            _, birth = self._chars[0]
            x = width - (self._distance - birth)
            if x + advance < 0:
                self._chars.popleft()
            else:
                break

        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg)
        if not self._chars:
            return
        painter.setFont(self._font)
        painter.setPen(self._fg)
        fm = QFontMetrics(self._font)
        baseline = (self.height() + fm.ascent() - fm.descent()) // 2
        width = self.width()
        for ch, birth in self._chars:
            x = width - (self._distance - birth)
            if x > width or x < -20:
                continue  # off-screen, cheap to skip
            painter.drawText(int(x), baseline, ch)
        painter.end()
