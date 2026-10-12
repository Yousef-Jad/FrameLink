"""Visible moving Qt source used for native capture measurements."""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class MotionSource(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FrameLink Native Motion Source")
        self.setMinimumSize(960, 540)
        self.resize(960, 540)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.timer.start(16)

    def advance(self):
        self.phase += 0.11
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#101827"))
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(12):
            x = int((math.sin(self.phase * 0.7 + index * 0.8) * 0.42 + 0.5) * width)
            y = int((math.cos(self.phase * 0.53 + index * 0.6) * 0.35 + 0.5) * height)
            size = 28 + (index % 4) * 12
            painter.setBrush(QColor.fromHsv((index * 31 + int(self.phase * 15)) % 360, 210, 245))
            painter.drawEllipse(x - size // 2, y - size // 2, size, size)
        painter.setPen(QColor("#f8fafc"))
        painter.setFont(QFont("sans", 24, QFont.Weight.Bold))
        painter.drawText(28, 44, f"FrameLink native capture motion {self.phase:.1f}")
        painter.setFont(QFont("sans", 16))
        painter.drawText(30, height - 28, "Continuous moving content · do not minimize this window")


app = QApplication(sys.argv[:1])
window = MotionSource()
window.show()
app.exec()
