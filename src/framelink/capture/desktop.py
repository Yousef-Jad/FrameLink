import os
import sys
import time
from collections import deque
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import (
    QScreenCapture,
    QMediaCaptureSession,
    QVideoSink,
    QVideoFrame,
    QVideoFrameFormat,
)


class FrameAdmission:
    """Rate-limit against a stable grid, without resetting phase on every callback."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.origin = None
        self.slot = -1
        self.fps = None

    def accept(self, stamp, fps):
        if self.origin is None or self.fps != fps or stamp < self.origin:
            self.origin, self.slot, self.fps = stamp, -1, fps
        slot = int((stamp - self.origin) * fps + 0.5)
        if slot <= self.slot:
            return False
        self.slot = slot
        return True


def frame_image(frame):
    """Convert CPU RGB frames without toImage's general video-rendering path.

    QImage owns the returned pixels before unmap; unsupported/GPU/YUV layouts
    retain Qt's conversion path. Never keep a borrowed PipeWire buffer alive.
    """
    image_format = QVideoFrameFormat.imageFormatFromPixelFormat(frame.pixelFormat())
    if (
        image_format != QImage.Format.Format_Invalid
        and frame.rotation().value == 0
        and not frame.mirrored()
        and frame.map(QVideoFrame.MapMode.ReadOnly)
    ):
        try:
            image = QImage(frame.bits(0), frame.width(), frame.height(), frame.bytesPerLine(0), image_format)
            converted = image.convertToFormat(QImage.Format.Format_RGB888)
            # Conversion may share memory when the input already is RGB888.
            return converted.copy() if image_format == QImage.Format.Format_RGB888 else converted
        finally:
            frame.unmap()
    return frame.toImage().convertToFormat(QImage.Format.Format_RGB888)


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
        self._admission = FrameAdmission()
        self._reset_metrics()
        self._metrics_started = time.monotonic()

    def _reset_metrics(self):
        self.metrics = {
            "callbacks": 0,
            "accepted": 0,
            "throttled": 0,
            "invalid": 0,
            "qimage_conversion_ms": deque(maxlen=4096),
            "numpy_copy_ms": deque(maxlen=4096),
            "callback_ms": deque(maxlen=4096),
        }
        self.first_frame_at = None
        self._video_frame_times = deque(maxlen=4096)
        self._accepted_frame_times = deque(maxlen=4096)
        self._source_frame_times = deque(maxlen=4096)
        self._timestamp_offset = None
        self._last_source_time = None
        self._admission.reset()

    @staticmethod
    def _interval_summary(times):
        intervals = sorted((right - left) * 1000 for left, right in zip(times, list(times)[1:]))
        if not intervals:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
        return {
            "count": len(intervals),
            "p50_ms": intervals[(len(intervals) - 1) // 2],
            "p95_ms": intervals[min(len(intervals) - 1, round((len(intervals) - 1) * 0.95))],
            "p99_ms": intervals[round((len(intervals) - 1) * 0.99)],
            "max_ms": intervals[-1],
        }

    @staticmethod
    def _rate(times):
        return (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0.0

    def _video_frame_arrived(self, now):
        self._video_frame_times.append(now)

    def _accepted_frame(self, now):
        self.metrics["accepted"] += 1
        self._accepted_frame_times.append(now)

    def metrics_snapshot(self):
        result = dict(self.metrics)
        for key in ("qimage_conversion_ms", "numpy_copy_ms", "callback_ms"):
            values = sorted(self.metrics.get(key, []))
            result[key] = {
                "count": len(values),
                "p50_ms": values[(len(values) - 1) // 2] if values else 0.0,
                "p95_ms": values[min(len(values) - 1, round((len(values) - 1) * 0.95))] if values else 0.0,
                "max_ms": max(values, default=0.0),
                "p99_ms": values[round((len(values) - 1) * 0.99)] if values else 0.0,
            }
        result["video_sink_fps"] = self._rate(self._video_frame_times)
        result["accepted_fps"] = self._rate(self._accepted_frame_times)
        result["capture_fps"] = result["accepted_fps"]
        result["source_timestamp_intervals_ms"] = self._interval_summary(self._source_frame_times)
        result["video_sink_intervals_ms"] = self._interval_summary(self._video_frame_times)
        result["accepted_intervals_ms"] = self._interval_summary(self._accepted_frame_times)
        result["first_frame_delay_ms"] = (
            (self.first_frame_at - self._metrics_started) * 1000 if self.first_frame_at is not None else None
        )
        return result

    def _frame(self, frame):
        callback_start = time.perf_counter()
        self.metrics["callbacks"] += 1
        now = time.monotonic()
        if not frame.isValid():
            self.metrics["invalid"] += 1
            return
        self._video_frame_arrived(now)
        # Qt's screen grabber sets startTime to the previous grab's end. The
        # first interval can include a long portal permission wait; use this
        # grab's completion timestamp instead of introducing that wait as drift.
        native_time = frame.endTime()
        if native_time < 0:
            native_time = frame.startTime()
        stamp = now
        if native_time >= 0:
            source_time = native_time / 1000000
            if self._timestamp_offset is None or (
                self._last_source_time is not None and source_time < self._last_source_time
            ):
                self._timestamp_offset = now - source_time
                self._admission.reset()
            self._last_source_time = source_time
            stamp = self._timestamp_offset + source_time
        self._source_frame_times.append(stamp)
        if not self._admission.accept(stamp, self.max_fps):
            self.metrics["throttled"] += 1
            return
        self.last_frame = now
        conversion_start = time.perf_counter()
        self.metrics["pixel_format"] = str(frame.pixelFormat())
        image = frame_image(frame)
        self.metrics["qimage_conversion_ms"].append((time.perf_counter() - conversion_start) * 1000)
        if image.isNull():
            self.metrics["invalid"] += 1
            return
        copy_start = time.perf_counter()
        raw = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
        rgb = raw[:, : image.width() * 3].reshape(image.height(), image.width(), 3).copy()
        self.metrics["numpy_copy_ms"].append((time.perf_counter() - copy_start) * 1000)
        self.buffer.put(rgb, stamp)
        self._accepted_frame(now)
        if self.first_frame_at is None:
            self.first_frame_at = now
        self.metrics["callback_ms"].append((time.perf_counter() - callback_start) * 1000)

    def start(self, screen, index=0, cursor=True, fps=60, force_fallback=False):
        self.stop()
        self._reset_metrics()
        self._metrics_started = time.monotonic()
        self.max_fps = fps
        if sys.platform == "win32" and not force_fallback:
            try:
                from windows_capture import WindowsCapture

                self.native = WindowsCapture(cursor_capture=cursor, draw_border=True, monitor_index=index + 1)

                @self.native.event
                def on_frame_arrived(frame, capture_control):
                    now = time.monotonic()
                    self.metrics["callbacks"] += 1
                    self._video_frame_arrived(now)
                    if self._admission.accept(now, self.max_fps):
                        self.last_frame = now
                        self._accepted_frame(now)
                        copy_start = time.perf_counter()
                        self.buffer.put(frame.frame_buffer[:, :, [2, 1, 0]].copy(), now)
                        if self.first_frame_at is None:
                            self.first_frame_at = now
                        self.metrics["numpy_copy_ms"].append((time.perf_counter() - copy_start) * 1000)
                    else:
                        self.metrics["throttled"] += 1

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

    def set_rate(self, fps):
        self.max_fps = fps
        if self.capture:
            self.capture.setMaximumFrameRate(fps)
