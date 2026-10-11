"""Preview canvas used by Studio and the floating recording controls."""

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class PreviewWidget(QWidget):
    fullscreenRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = QImage()
        self._title = "Your studio is ready"
        self._detail = "Choose a source to begin"
        self._frame_info = ""
        self.setObjectName("preview-canvas")
        self.setMinimumSize(400, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Live recording preview. Double-click to enter fullscreen.")

    def set_frame(self, image: QImage):
        self._frame = image.copy() if not image.isNull() else QImage()
        self.update()

    def current_frame(self):
        return self._frame.copy()

    def set_empty_state(self, title: str, detail: str):
        self._title, self._detail = title, detail
        self._frame = QImage()
        self.update()

    def set_frame_info(self, text: str):
        self._frame_info = text
        self.update()

    def sizeHint(self):
        return QSize(960, 540)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.fullscreenRequested.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#080A0E"))
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)

        if not self._frame.isNull():
            available = self.rect().adjusted(8, 8, -8, -8)
            size = self._frame.size().scaled(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = available.x() + (available.width() - size.width()) // 2
            y = available.y() + (available.height() - size.height()) // 2
            painter.drawImage(
                x,
                y,
                self._frame.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation),
            )
            if self._frame_info:
                painter.setPen(QColor(242, 244, 248, 220))
                painter.drawText(
                    self.rect().adjusted(16, 10, -16, -10),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                    self._frame_info,
                )
        else:
            painter.setPen(QColor("#F2F4F8"))
            title_font = painter.font()
            title_font.setPointSize(17)
            title_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(title_font)
            painter.drawText(
                self.rect().adjusted(24, -12, -24, -12),
                Qt.AlignmentFlag.AlignCenter,
                self._title,
            )
            painter.setPen(QColor("#9299A8"))
            detail_font = painter.font()
            detail_font.setPointSize(10)
            detail_font.setWeight(QFont.Weight.Normal)
            painter.setFont(detail_font)
            painter.drawText(
                self.rect().adjusted(24, 42, -24, 42),
                Qt.AlignmentFlag.AlignCenter,
                self._detail,
            )
        painter.end()
