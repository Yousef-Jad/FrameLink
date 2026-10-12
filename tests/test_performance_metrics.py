import time

import numpy as np

from framelink.media.buffers import VideoBuffer
from framelink.media.compositor import compose_rgb
from framelink.media.encode import MediaWriter, validate_media
from framelink.media.model import RecordingConfig


def test_auto_rate_respects_real_capture_not_only_encoder_or_refresh():
    from framelink.media.performance import stable_frame_rate

    assert stable_frame_rate(220, 35, 60) == (30, False)
    assert stable_frame_rate(220, 20, 144) == (15, False)
    assert stable_frame_rate(220, 0, 144) == (15, True)
    assert stable_frame_rate(40, 120, 144) == (30, False)
    assert stable_frame_rate(220, 59.9, 60) == (60, False)


def test_video_buffer_reports_overflow_and_capture_health():
    buffer = VideoBuffer(capacity=2)
    buffer.next_after(0)  # Register a recorder before the producer outruns it.
    frame = np.zeros((8, 8, 3), np.uint8)
    buffer.put(frame, 1.0)
    buffer.put(frame, 1.05)
    buffer.put(frame, 1.10)

    report = buffer.metrics_snapshot()
    assert report["received"] == 3
    assert report["dropped"] == 1
    assert report["queue_depth"] == 2
    assert report["max_queue_depth"] == 2


def test_consumed_history_eviction_is_not_a_recording_drop():
    buffer = VideoBuffer(capacity=2)
    frame = np.zeros((8, 8, 3), np.uint8)
    sequence = 0
    for index in range(20):
        buffer.put(frame, index / 30)
        sequence = buffer.next_after(sequence).sequence
    report = buffer.metrics_snapshot()
    assert report["dropped"] == 0
    assert report["evicted_history"] == 18
    assert report["queue_depth"] == 0
    assert report["retained_frames"] == 2
    assert report["max_queue_depth"] == 1
    buffer.release_consumer()
    for index in range(20):
        buffer.put(frame, 2 + index / 30)
    assert buffer.metrics_snapshot()["dropped"] == 0


def test_capture_admission_preserves_cadence_under_callback_jitter():
    from framelink.capture.desktop import FrameAdmission

    gate = FrameAdmission()
    # Alternating slightly early/late delivery used to reset the deadline and
    # throw away valid 30 FPS frames. Fixed time slots retain them all.
    stamps = [10 + index / 30 + (0.004 if index % 2 else 0) for index in range(300)]
    assert sum(gate.accept(stamp, 30) for stamp in stamps) == 300
    gate.reset()
    assert 299 <= sum(gate.accept(10 + index / 60, 30) for index in range(600)) <= 301
    assert gate.accept(30, 60)  # Rate change establishes a new phase.


def test_cpu_frame_conversion_matches_qt_and_owns_pixels():
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtMultimedia import QVideoFrame
    from framelink.capture.desktop import frame_image

    for fmt in (QImage.Format.Format_RGB32, QImage.Format.Format_RGBA8888, QImage.Format.Format_RGB888):
        image = QImage(321, 181, fmt)
        image.fill(QColor(19, 89, 213))
        frame = QVideoFrame(image)
        result = frame_image(frame)
        expected = frame.toImage().convertToFormat(QImage.Format.Format_RGB888)
        assert result == expected
        del frame, image
        assert result.pixelColor(320, 180) == QColor(19, 89, 213)


def test_portal_permission_wait_does_not_shift_capture_timestamps(monkeypatch):
    from PySide6.QtGui import QImage
    from PySide6.QtMultimedia import QVideoFrame
    from framelink.capture.desktop import DesktopCapture

    clock = [200.0]
    monkeypatch.setattr("framelink.capture.desktop.time.monotonic", lambda: clock[0])
    buffer = VideoBuffer()
    capture = DesktopCapture(buffer)
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0)
    frame = QVideoFrame(image)
    frame.setStartTime(0)
    frame.setEndTime(90_000_000)  # Permission was granted after 90 seconds.
    capture._frame(frame)
    first = buffer.latest()
    clock[0] += 0.033333
    frame.setStartTime(90_000_000)
    frame.setEndTime(90_033_333)
    capture._frame(frame)
    second = buffer.latest()
    assert abs(second.timestamp - first.timestamp - 0.033333) < 0.000001
    assert abs(second.timestamp - clock[0]) < 0.000001


def test_video_buffer_returns_each_queued_source_frame_once():
    buffer = VideoBuffer(capacity=4)
    frame = np.zeros((8, 8, 3), np.uint8)
    for index in range(3):
        buffer.put(frame + index, 1.0 + index / 30)

    first = buffer.next_after(0)
    second = buffer.next_after(first.sequence)
    third = buffer.next_after(second.sequence)
    assert [sample.sequence for sample in (first, second, third)] == [1, 2, 3]
    assert buffer.next_after(third.sequence) is None


def test_media_writer_exposes_conversion_encode_and_mux_timings(tmp_path):
    path = tmp_path / "timed.mkv"
    writer = MediaWriter(path, 320, 180, 30, preset="ultrafast")
    frame = np.zeros((180, 320, 3), np.uint8)
    for pts in range(3):
        writer.write_video(frame, pts)
    writer.close()

    telemetry = writer.telemetry_snapshot()
    assert telemetry["video_calls"] == 3
    assert telemetry["packets"] > 0
    assert telemetry["encode_ms"] >= 0
    assert telemetry["mux_ms"] >= 0
    assert telemetry["disk_flush_ms"] >= 0
    assert validate_media(path, expected_duration=3 / 30, audio=False)["decoded_frames"] == 3


def test_media_writer_timings_are_not_recorded_as_capture_fps(tmp_path):
    path = tmp_path / "timed.mkv"
    writer = MediaWriter(path, 320, 180, 30, preset="ultrafast")
    frame = np.zeros((180, 320, 3), np.uint8)
    writer.write_video(frame, 0)
    time.sleep(0.01)
    writer.write_video(frame, 1)
    writer.close()
    report = writer.telemetry_snapshot()
    assert report["video_calls"] == 2
    assert report["source_fps"] is None


def test_combined_native_compositor_preserves_dimensions_and_motion():
    screen = np.zeros((180, 320, 3), np.uint8)
    phone = np.zeros((180, 320, 3), np.uint8)
    screen[:, :, 1] = 80
    phone[:, :, 2] = 220
    image = compose_rgb(screen, phone, (320, 180), RecordingConfig(mode="both"))
    assert image.shape == (180, 320, 3)
    assert image.dtype == np.uint8
    assert image[5, 5, 1] == 80
    assert image[160, 280, 2] > 100
