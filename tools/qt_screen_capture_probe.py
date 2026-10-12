"""Minimal native Qt screen-capture probe for Wayland/PipeWire diagnosis."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--screen-index", type=int, default=0)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--convert",
        action="store_true",
        help="Also run the application's QImage conversion and NumPy copy path",
    )
    args = parser.parse_args()

    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    os.environ.setdefault("QT_SCREEN_CAPTURE_BACKEND", "pipewire")

    from PySide6.QtCore import QCoreApplication, QTimer
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtMultimedia import QMediaCaptureSession, QScreenCapture, QVideoSink

    app = QGuiApplication(sys.argv[:1])
    screens = app.screens()
    if not screens:
        print(json.dumps({"event": "fatal", "error": "No Qt screens available"}), flush=True)
        return 2
    if args.screen_index >= len(screens):
        print(
            json.dumps(
                {
                    "event": "fatal",
                    "error": "Screen index out of range",
                    "screen_count": len(screens),
                }
            ),
            flush=True,
        )
        return 2

    screen = screens[args.screen_index]
    capture = QScreenCapture()
    session = QMediaCaptureSession()
    sink = QVideoSink()
    session.setScreenCapture(capture)
    session.setVideoSink(sink)
    started = time.monotonic()
    frames = []
    conversion_ms = []
    copy_ms = []
    callback_ms = []

    def enum_value(value):
        return getattr(value, "value", value)

    def emit(event: str, **values):
        print(
            json.dumps(
                {
                    "event": event,
                    "t_ms": round((time.monotonic() - started) * 1000, 3),
                    **values,
                },
                default=str,
            ),
            flush=True,
        )

    emit(
        "before_start",
        qt_qpa_platform=os.environ.get("QT_QPA_PLATFORM"),
        qt_media_backend=os.environ.get("QT_MEDIA_BACKEND"),
        qt_screen_capture_backend=os.environ.get("QT_SCREEN_CAPTURE_BACKEND"),
        wayland_display=os.environ.get("WAYLAND_DISPLAY"),
        dbus_session=os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
        screen_name=screen.name(),
        screen_size=[screen.geometry().width(), screen.geometry().height()],
        refresh_rate=screen.refreshRate(),
    )

    capture.activeChanged.connect(lambda active: emit("active_changed", active=active))
    capture.errorOccurred.connect(
        lambda error, message: emit(
            "error_occurred",
            error=str(error),
            error_value=enum_value(error),
            message=message,
            active=capture.isActive(),
            error_string=capture.errorString(),
        )
    )

    def on_frame(frame):
        callback_start = time.perf_counter()
        if not frame.isValid():
            return
        frames.append(time.monotonic())
        if len(frames) == 1:
            QTimer.singleShot(round(args.seconds * 1000), finish)
            emit(
                "first_video_frame",
                valid=frame.isValid(),
                width=frame.width(),
                height=frame.height(),
                pixel_format=str(frame.pixelFormat()),
                start_time=frame.startTime(),
                end_time=frame.endTime(),
                active=capture.isActive(),
            )
        if args.convert and frame.isValid():
            conversion_start = time.perf_counter()
            image = frame.toImage().convertToFormat(QImage.Format.Format_RGB888)
            conversion_ms.append((time.perf_counter() - conversion_start) * 1000)
            if not image.isNull():
                copy_start = time.perf_counter()
                raw = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
                raw[:, : image.width() * 3].reshape(image.height(), image.width(), 3).copy()
                copy_ms.append((time.perf_counter() - copy_start) * 1000)
        callback_ms.append((time.perf_counter() - callback_start) * 1000)

    sink.videoFrameChanged.connect(on_frame)

    capture.setMaximumFrameRate(args.fps)
    capture.setScreen(screen)
    emit("before_capture_start", active=capture.isActive(), error_string=capture.errorString())
    capture.start()
    emit(
        "after_capture_start",
        active=capture.isActive(),
        error_string=capture.errorString(),
        error=str(capture.error()),
        error_value=enum_value(capture.error()),
    )

    def heartbeat():
        emit(
            "heartbeat",
            active=capture.isActive(),
            frame_count=len(frames),
            error=str(capture.error()),
            error_value=enum_value(capture.error()),
            error_string=capture.errorString(),
        )

    heartbeat_timer = QTimer()
    heartbeat_timer.timeout.connect(heartbeat)
    heartbeat_timer.start(1000)

    def finish():
        heartbeat_timer.stop()
        capture.stop()
        intervals = sorted((right - left) * 1000 for left, right in zip(frames, frames[1:]))

        def summary(values):
            if not values:
                return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
            return {
                "count": len(values),
                "p50_ms": values[(len(values) - 1) // 2],
                "p95_ms": values[min(len(values) - 1, round((len(values) - 1) * 0.95))],
                "max_ms": values[-1],
            }

        emit(
            "finished",
            active=capture.isActive(),
            frame_count=len(frames),
            video_sink_fps=(len(frames) - 1) / (frames[-1] - frames[0]) if len(frames) > 1 else 0.0,
            video_sink_intervals_ms=summary(intervals),
            qimage_conversion_ms=summary(sorted(conversion_ms)),
            numpy_copy_ms=summary(sorted(copy_ms)),
            callback_ms=summary(sorted(callback_ms)),
        )
        QCoreApplication.quit()

    # Approval time is unbounded and separate from the requested measurement.
    # The first valid video callback starts the measurement timer above.
    app.exec()
    return 0 if frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
