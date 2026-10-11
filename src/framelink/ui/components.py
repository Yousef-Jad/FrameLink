"""Shared functional controls. Media services are intentionally absent here."""

import os

from PySide6.QtCore import QSize, Qt, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton
from framelink.ui.icons import symbol
from framelink.ui.theme import motion_enabled, MOTION


class MaterialSurface(QFrame):
    def __init__(self, material="elevated", parent=None):
        super().__init__(parent)
        self.material = material
        self.setObjectName(f"material-{material}")


class StatusPill(QLabel):
    def __init__(self, text="Ready", parent=None):
        super().__init__(text, parent)
        self.setObjectName("status-pill")

    def set_status(self, text, tone="neutral"):
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class IconButton(QToolButton):
    def __init__(self, icon_name, tooltip, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setObjectName("icon-button")
        self.setIcon(symbol(icon_name))
        self.setIconSize(QSize(20, 20))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setAutoRaise(True)
        self.setFixedSize(36, 36)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def recolor(self, color):
        self.setIcon(symbol(self.icon_name, color))


class IconRailButton(IconButton):
    def __init__(self, icon_name, tooltip, parent=None):
        super().__init__(icon_name, tooltip, parent)
        self.setObjectName("rail-button")
        self.setCheckable(True)
        self.setFixedSize(48, 48)


class SegmentedControl(QFrame):
    valueChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mode-segment")
        self.buttons = {}
        self.group = []
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(4, 2, 4, 4)
        self.row.setSpacing(2)
        self._indicator = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(MOTION["mode_ms"])
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._animate)

    def _animate(self, value):
        self._indicator = value
        self.update()

    def add_item(self, label, value):
        button = QToolButton(self)
        button.setObjectName("segment")
        button.setText(label)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setAccessibleName(label + " recording mode")
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda checked=False: self.set_current_value(value))
        self.row.addWidget(button)
        self.buttons[value] = button
        self.group.append(button)
        if len(self.group) == 1:
            self.set_current_value(value, emit=False)

    def current_value(self):
        return next((value for value, button in self.buttons.items() if button.isChecked()), None)

    def set_current_value(self, value, emit=True):
        if value not in self.buttons:
            raise ValueError(f"Unknown segment: {value}")
        changed = self.current_value() != value
        for key, button in self.buttons.items():
            button.setChecked(key == value)
        target = float(list(self.buttons).index(value))
        self.animation.stop()
        if changed and motion_enabled() and self.isVisible():
            self.animation.setStartValue(self._indicator)
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self._animate(target)
        if emit and changed:
            self.valueChanged.emit(value)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.group:
            return
        i = min(int(self._indicator), len(self.group) - 1)
        a, b = self.group[i].geometry(), self.group[min(i + 1, len(self.group) - 1)].geometry()
        x = a.center().x() + (b.center().x() - a.center().x()) * (self._indicator - i)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#AFC5FF"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(x - 10), self.height() - 3, int(x + 10), self.height() - 3)


class FloatingTransport(MaterialSurface):
    recordRequested = Signal()
    pauseRequested = Signal()
    stopRequested = Signal()
    inspectorRequested = Signal()
    microphoneToggled = Signal(bool)
    audioToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__("floating", parent)
        self._background = QImage()
        self._reduce_transparency = os.environ.get("LUMACAPTURE_REDUCE_TRANSPARENCY", "").lower() in {"1", "true", "yes"}
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.state_value = "Ready"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        self.record = QToolButton(self)
        self.record.setObjectName("record-button")
        self.record.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.record.setIconSize(QSize(18, 18))
        self.record.setMinimumSize(112, 40)
        self.record.setCheckable(True)
        self.record.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.record.clicked.connect(self._record_clicked)
        layout.addWidget(self.record)
        self.timer = QLabel("00:00:00", self)
        self.timer.setObjectName("timer")
        self.timer.setMinimumWidth(104)
        layout.addWidget(self.timer)
        self.state = QLabel("Ready", self)
        self.state.setObjectName("muted")
        self.state.setMinimumWidth(64)
        layout.addWidget(self.state)
        self.pause = IconButton("pause", "Pause recording", self)
        self.pause.clicked.connect(self.pauseRequested)
        layout.addWidget(self.pause)
        self.stop = IconButton("stop", "Stop recording", self)
        self.stop.clicked.connect(self.stopRequested)
        layout.addWidget(self.stop)
        self.microphone = IconButton("microphone", "Microphone for next recording", self)
        self.microphone.setCheckable(True)
        self.microphone.toggled.connect(self.microphoneToggled)
        layout.addWidget(self.microphone)
        self.audio = IconButton("audio", "System audio for next recording", self)
        self.audio.setCheckable(True)
        self.audio.toggled.connect(self.audioToggled)
        layout.addWidget(self.audio)
        self.inspector = IconButton("settings", "Recording settings (Ctrl+I)", self)
        self.inspector.clicked.connect(self.inspectorRequested)
        layout.addWidget(self.inspector)
        self.set_state("Ready")

    def set_background_frame(self, image):
        """Keep a bounded, low-resolution blur source derived from the live preview."""

        if image is None or image.isNull() or self._reduce_transparency:
            self._background = QImage()
        else:
            self._background = image.scaled(
                QSize(24, 12), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)
        painter.setClipPath(path)
        if not self._background.isNull():
            painter.drawImage(self.rect(), self._background)
            painter.fillRect(self.rect(), QColor(15, 18, 25, 214))
        else:
            painter.fillRect(self.rect(), QColor(21, 25, 34, 245))
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)
        painter.end()

    def _record_clicked(self):
        if self.state_value in {"Recording", "Paused"}:
            self.stopRequested.emit()
        elif self.state_value in {"Ready", "Error"}:
            self.recordRequested.emit()

    def set_state(self, state, elapsed=0):
        active = state in {"Recording", "Paused", "Finalizing"}
        if self.state_value != state or not self.record.text():
            self.state_value = state
            self.record.setText("Stop" if active else "Record")
            self.record.setChecked(active)
            self.record.setIcon(symbol("stop" if active else "record", "#FFFFFF" if active else "#FF453A"))
            self.record.setProperty("recording", active)
            self.record.style().unpolish(self.record)
            self.record.style().polish(self.record)
            self.record.setToolTip("Stop and save recording (Ctrl+Shift+R)" if active else "Start recording (Ctrl+R)")
            self.record.setAccessibleName(self.record.toolTip())
            self.pause.setIcon(symbol("play" if state == "Paused" else "pause"))
            self.pause.setToolTip("Resume recording" if state == "Paused" else "Pause recording")
            self.pause.setAccessibleName(self.pause.toolTip())
        self.record.setEnabled(state != "Finalizing")
        self.pause.setEnabled(state in {"Recording", "Paused"})
        self.microphone.setEnabled(not active)
        self.audio.setEnabled(not active)
        self.state.setText(state)
        h, r = divmod(max(0, int(elapsed)), 3600)
        m, s = divmod(r, 60)
        self.timer.setText(f"{h:02}:{m:02}:{s:02}")
