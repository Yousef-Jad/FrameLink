import os
import sys
import time
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QScreenCapture, QMediaCaptureSession, QVideoSink


def backend_name(platform=None, environment=None):
    platform = platform or sys.platform
    env = os.environ if environment is None else environment
    if platform == "win32":
        return "Windows Graphics Capture / DXGI"
    if platform.startswith("linux"):
        return (
            "PipeWire · desktop portal"
            if env.get("XDG_SESSION_TYPE") == "wayland" or env.get("WAYLAND_DISPLAY")
            else "X11 · Qt Multimedia"
        )
    raise RuntimeError("This operating system is not supported")


class DesktopCapture(QObject):
    error = Signal(str)
    changed = Signal(str)

    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer
        self.capture = None
        self.session = None
        self.sink = None
        self.native = None
        self.control = None
        self.active = False
        self.last_frame = 0
        self.max_fps = 60

    def _frame(self, frame):
        now = time.monotonic()
        if not frame.isValid() or now - self.last_frame < 0.95 / self.max_fps:
            return
        self.last_frame = now
        image = frame.toImage().convertToFormat(QImage.Format.Format_RGB888)
        if image.isNull():
            return
        raw = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
        rgb = raw[:, : image.width() * 3].reshape(image.height(), image.width(), 3).copy()
        self.buffer.put(rgb, now)

    def start(self, screen, index=0, cursor=True, fps=60, force_fallback=False):
        self.stop()
        self.max_fps = fps
        if sys.platform == "win32" and not force_fallback:
            try:
                from windows_capture import WindowsCapture

                self.native = WindowsCapture(cursor_capture=cursor, draw_border=True, monitor_index=index + 1)

                @self.native.event
                def on_frame_arrived(frame, capture_control):
                    now = time.monotonic()
                    if now - self.last_frame >= 0.95 / self.max_fps:
                        self.last_frame = now
                        self.buffer.put(frame.frame_buffer[:, :, [2, 1, 0]].copy(), now)

                @self.native.event
                def on_closed():
                    self.error.emit("Windows capture source closed; restart preview or select DXGI fallback.")

                self.control = self.native.start_free_threaded()
                self.active = True
                self.changed.emit("Windows Graphics Capture")
                return
            except Exception as exc:
                self.changed.emit(f"WGC unavailable ({exc}); using DXGI")
                self.native = None
        if self.capture is None:
            if sys.platform.startswith("linux") and "portal" in backend_name():
                from PySide6.QtDBus import QDBusConnection, QDBusInterface

                portal = QDBusInterface(
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.ScreenCast",
                    QDBusConnection.sessionBus(),
                )
                if not portal.isValid():
                    raise RuntimeError(
                        "No ScreenCast portal is available. Install and start the portal for your compositor."
                    )
                os.environ["QT_SCREEN_CAPTURE_BACKEND"] = "pipewire"
            self.capture = QScreenCapture(self)
            self.session = QMediaCaptureSession(self)
            self.sink = QVideoSink(self)
            self.session.setScreenCapture(self.capture)
            self.session.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self._frame)
            self.capture.errorOccurred.connect(lambda code, message: self.error.emit(message))
        self.capture.setMaximumFrameRate(fps)
        self.capture.setScreen(screen)
        self.capture.start()
        self.active = True
        self.changed.emit("DXGI Desktop Duplication" if sys.platform == "win32" else backend_name())

    def stop(self):
        if self.control is not None:
            self.control.stop()
            self.control = None
            self.native = None
        if self.capture:
            self.capture.stop()
        self.active = False
        self.buffer.clear()
