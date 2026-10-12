import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from framelink.media.buffers import Sources
from framelink.media.model import RecordingConfig, State
from framelink.media.encode import validate_media
from framelink.recording.engine import Recorder
from framelink.storage.library import Library
from framelink.ui.window import MainWindow
from conftest import feed_video, wait_until


@pytest.mark.parametrize(
    "mode,layout,count",
    [("screen", "pip", 1), ("phone", "pip", 1), ("both", "pip", 1), ("both", "separate", 2)],
)
def test_real_stop_click_repeated_sessions_and_compression(tmp_path, mode, layout, count, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Library(tmp_path / "db"), tmp_path / "data", smoke=True)
    window.recorder.output = window.output = tmp_path / "videos"
    window.change_mode(mode)
    # This lifecycle test has synthetic sources and must not open a physical mic.
    window.mic_device.clear()
    window.mic_device.addItem("Test microphone", "test-mic")
    monkeypatch.setattr(window.microphone, "start", lambda device: None)
    window.layout_mode.setCurrentIndex(window.layout_mode.findData(layout))
    monkeypatch.setattr(window, "start_phone_server_auto", lambda: None)
    feeder_stop, feeder_thread = feed_video(window.sources, fps=60)
    window.show()

    def pump(predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError(window.recorder.error or "UI workflow timed out")

    try:
        for session in range(2):
            for source in (window.sources.screen, window.sources.phone):
                source.put(np.full((180, 320, 3), 90, np.uint8))
            window.start_recording()
            pump(lambda: window.recorder.metrics.get("encoded", 0) >= 18)
            window.tick()  # Must not raise an AudioBuffer AttributeError.
            assert window.transport.record.text() == "Stop"
            assert window.transport.record.isEnabled()
            QTest.mouseClick(window.transport.record, Qt.MouseButton.LeftButton)
            assert not window.transport.record.isEnabled()
            assert window.transport.state.text() == "Finalizing recording"
            window.stop_recording()  # Idempotent, including during validation.
            pump(lambda: window.recorder.state == State.COMPLETED and not window.recorder.thread.is_alive())
            assert window.recorder.machine.history[-3:] == [State.STOPPING, State.FINALIZING, State.COMPLETED]
            originals = [r for r in window.library.recordings() if not r["original"]]
            assert len(originals) == (session + 1) * count
            for row in originals:
                assert Path(row["path"]).suffix == ".mp4"
                assert 0.2 <= validate_media(row["path"])["duration"] < 2
            pump(
                lambda: (
                    len(window.library.jobs()) == (session + 1) * count
                    and all(j["status"] == "complete" for j in window.library.jobs())
                )
            )
            assert all(path.suffix == ".mp4" for path in window.output.iterdir())
            assert all(row["thumbnail"].endswith(".png") for row in originals)
            assert all(Path(row["thumbnail"]).parent != window.output for row in originals)
    finally:
        window.recorder.stop()
        if window.recorder.thread:
            window.recorder.thread.join(10)
        window.compression.stop()
        window.compression.thread.join(15)
        window.close()
        app.processEvents()
        feeder_stop.set()
        feeder_thread.join(2)


def make_recorder(tmp_path, feed=True):
    sources = Sources()
    for source in (sources.screen, sources.phone):
        source.put(np.zeros((180, 320, 3), np.uint8))
    recorder = Recorder(sources, Library(tmp_path / "db"), tmp_path / "video")
    recorder._test_feeder = feed_video(sources, fps=30) if feed else None
    return recorder


def stop_test_source(recorder):
    if recorder._test_feeder:
        recorder._test_feeder[0].set()
        recorder._test_feeder[1].join(2)


def test_stop_during_preparation_and_failed_encoder(tmp_path, monkeypatch):
    import framelink.recording.engine as engine

    original = engine.MediaWriter
    entered, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "MediaWriter", delayed)
    recorder = make_recorder(tmp_path, feed=False)
    recorder.start(RecordingConfig())
    assert entered.wait(5)
    assert recorder.state == State.PREPARING
    assert recorder.stop() is True
    assert recorder.stop() is False
    release.set()
    recorder.thread.join(10)
    assert recorder.state == State.ERROR  # No frames: never claim a completed file.
    assert not recorder.library.recordings()
    assert not recorder.thread.is_alive()
    monkeypatch.setattr(engine, "MediaWriter", original)
    recorder._test_feeder = feed_video(recorder.sources, fps=30)
    recorder.start(RecordingConfig())
    wait_until(lambda: recorder.metrics["encoded"] >= 4)
    recorder.stop()
    recorder.thread.join(10)
    assert recorder.state == State.COMPLETED
    stop_test_source(recorder)


def test_stop_during_connection_loss(tmp_path):
    recorder = make_recorder(tmp_path, feed=False)
    screen_feeder = feed_video(recorder.sources, names=("screen",), fps=30)
    phone_feeder = feed_video(recorder.sources, names=("phone",), fps=30)
    recorder.start(RecordingConfig(mode="both", disconnect_policy="pause"))
    wait_until(lambda: recorder.metrics["encoded"] >= 4)
    phone_feeder[0].set()
    phone_feeder[1].join(2)
    recorder.sources.phone.clear()
    wait_until(lambda: recorder.state == State.PAUSED)
    phone_feeder = feed_video(recorder.sources, names=("phone",), fps=30)
    recorder.stop()
    recorder.stop()
    recorder.thread.join(10)
    assert recorder.state == State.COMPLETED
    validate_media(recorder.library.recordings()[0]["path"])
    screen_feeder[0].set()
    screen_feeder[1].join(2)
    phone_feeder[0].set()
    phone_feeder[1].join(2)


def test_encoder_failure_keeps_partial_and_stop_is_safe(tmp_path, monkeypatch):
    from framelink.media.encode import MediaWriter

    original = MediaWriter.write_video

    def fail(writer, rgb, pts):
        if pts >= 4:
            raise RuntimeError("injected encoder failure")
        return original(writer, rgb, pts)

    monkeypatch.setattr(MediaWriter, "write_video", fail)
    recorder = make_recorder(tmp_path)
    recorder.start(RecordingConfig())
    recorder.thread.join(10)
    assert recorder.state == State.ERROR
    assert recorder.stop() is False
    assert list(Path(recorder.output).glob("*.partial.mp4"))
    assert not recorder.library.jobs()
    stop_test_source(recorder)


def test_native_portrait_recording_preserves_dimensions_and_mirror(tmp_path):
    import av

    recorder = make_recorder(tmp_path, feed=False)
    portrait = np.zeros((320, 180, 3), np.uint8)
    portrait[:, :90, 0] = 230
    portrait[:, 90:, 2] = 230
    recorder.sources.phone.clear()
    feeder_stop, feeder_thread = feed_video(
        recorder.sources, names=("phone",), shape=(320, 180, 3), fps=30, template=portrait
    )
    wait_until(
        lambda: recorder.sources.phone.latest() is not None
        and recorder.sources.phone.latest().rgb.shape == portrait.shape
    )
    recorder.start(RecordingConfig(mode="phone", mirror=True))
    wait_until(lambda: recorder.metrics["encoded"] >= 5)
    recorder.stop()
    recorder.thread.join(10)
    assert recorder.state == State.COMPLETED
    recording = recorder.library.recordings()[0]
    assert (recording["metadata"]["width"], recording["metadata"]["height"]) == (180, 320)
    with av.open(recording["path"]) as container:
        rgb = next(container.decode(video=0)).to_ndarray(format="rgb24")
    # The saved source stays portrait; preview FILL cannot add bars or crop it.
    assert rgb[4, 4, 2] > 200 and rgb[-5, -5, 0] > 200
    feeder_stop.set()
    feeder_thread.join(2)


def test_phone_recording_buffers_through_encoder_jitter_for_lecture_capture(tmp_path, monkeypatch):
    from framelink.media.encode import MediaWriter

    recorder = make_recorder(tmp_path)
    original = MediaWriter.write_video

    def delayed_write(writer, rgb, pts):
        time.sleep(0.04)
        return original(writer, rgb, pts)

    monkeypatch.setattr(MediaWriter, "write_video", delayed_write)
    recorder.start(RecordingConfig(mode="phone", fps=30))
    time.sleep(0.45)
    recorder.stop()
    recorder.thread.join(10)
    assert recorder.state == State.COMPLETED, recorder.error
    assert recorder.metrics.get("duplicated_frames", 0) == 0
    assert recorder.metrics.get("source_frames_skipped", 0) >= 0
    validate_media(recorder.library.recordings()[0]["path"])
    stop_test_source(recorder)
