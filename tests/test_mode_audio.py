"""Mode defaults and decoded output routing, using distinguishable audio sources."""

import threading
import time
from unittest.mock import Mock

import av
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioSource, QMediaDevices, QtAudio

from conftest import feed_video
from framelink.media.model import State
from framelink.capture.audio import AudioCapture
from framelink.media.buffers import AudioBuffer
from framelink.storage.library import Library
from framelink.ui.window import MainWindow


@pytest.fixture
def studio(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    library = Library(tmp_path / "db")
    library.set_setting("auto_compress", False)
    window = MainWindow(library, tmp_path / "data", smoke=True)
    window.output = window.recorder.output = tmp_path / "videos"
    window.mic_device.clear()
    window.mic_device.addItem("Fixture microphone", "fixture-mic")
    monkeypatch.setattr(window.microphone, "start", Mock())
    yield app, window
    window.recorder.stop()
    if window.recorder.thread:
        window.recorder.thread.join(10)
    window.compression.stop()
    window.compression.thread.join(5)
    window.close()
    app.processEvents()


def test_mode_defaults_and_microphone_button(studio):
    _, window = studio
    for mode in ("screen", "phone", "both", "phone", "screen"):
        window.change_mode(mode)
        cfg = window.config()
        assert cfg.phone_audio == (mode == "phone")
        assert cfg.microphone == (mode != "phone")
        assert not cfg.system_audio
        assert window.transport.microphone.isChecked()
        window.transport.microphone.click()
        assert not window.config().phone_audio
        assert not window.config().microphone
        # Re-selecting the same mode must preserve an explicit mute.
        window.change_mode(mode)
        assert not window.transport.microphone.isChecked()
        selected = window.phone_audio if mode == "phone" else window.mic_enabled
        selected.setChecked(True)
        assert window.transport.microphone.isChecked()
    window.system_enabled.setChecked(True)
    assert window.transport.audio.isChecked()
    window.change_mode("phone")
    assert not window.transport.audio.isChecked()


def test_missing_computer_microphone_is_reported(studio):
    _, window = studio
    window.mic_device.clear()
    assert not window.start_recording()
    assert "No microphone input" in window.recorder.error
    window.microphone.start.assert_not_called()


def test_native_audio_error_enum_does_not_report_no_error(studio):
    capture = AudioCapture(AudioBuffer())
    errors = []
    capture.error.connect(errors.append)
    source = QAudioSource()
    capture.source = source
    capture._state_changed(None)
    assert not errors
    capture.source = Mock()
    capture.source.error.return_value = QtAudio.Error.IOError
    capture._state_changed(None)
    assert errors == ["Audio capture error: IOError"]
    capture.source = None


def test_computer_microphone_uses_system_default_not_first_device(studio, monkeypatch, tmp_path):
    devices = [Mock(), Mock()]
    for index, device in enumerate(devices):
        device.id.return_value = str(index).encode()
        device.description.return_value = f"Input {index}"
    monkeypatch.setattr(QMediaDevices, "audioInputs", lambda: devices)
    monkeypatch.setattr(QMediaDevices, "defaultAudioInput", lambda: devices[1])
    window = MainWindow(Library(tmp_path / "other-db"), tmp_path / "other-data", smoke=True)
    try:
        assert window.mic_device.currentData() is devices[1]
        assert window.config().microphone
    finally:
        window.compression.stop()
        window.compression.thread.join(5)
        window.close()


@pytest.mark.parametrize("mode,layout", [
    ("phone", "pip"), ("screen", "pip"), ("both", "pip"), ("both", "separate"),
])
def test_saved_mp4_contains_only_mode_selected_microphone(studio, mode, layout):
    app, window = studio
    window.change_mode(mode)
    window.layout_mode.setCurrentIndex(window.layout_mode.findData(layout))
    stop = threading.Event()
    origin = time.monotonic()

    def feed_audio():
        while not stop.is_set():
            timestamp = time.monotonic()
            sample_index = round((timestamp - origin) * 48000)
            phase = (np.arange(4800) + sample_index) / 48000
            for name, frequency in (("phone", 440), ("mic", 880), ("system", 1760)):
                tone = (0.15 * np.sin(2 * np.pi * frequency * phase)).astype(np.float32)
                window.sources.audio[name].put(np.vstack([tone, tone]), timestamp)
            stop.wait(0.02)

    audio_thread = threading.Thread(target=feed_audio)
    audio_thread.start()
    video_stop, video_thread = feed_video(window.sources, fps=30)

    def pump(predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError(window.recorder.error or "Audio recording timed out")

    try:
        pump(lambda: window.sources.screen.latest() is not None)
        assert window.start_recording(), window.recorder.error
        pump(lambda: window.recorder.metrics.get("encoded", 0) >= 30)
        window.tick()
        assert not window.mic_enabled.isEnabled()
        assert not window.phone_audio.isEnabled()
        assert window.transport.meter._level > 0
        window.stop_recording()
        pump(lambda: window.recorder.state == State.COMPLETED and not window.recorder.thread.is_alive())
        if mode == "phone":
            window.microphone.start.assert_not_called()
        else:
            window.microphone.start.assert_called_once_with("fixture-mic")
        rows = window.library.recordings()
        assert len(rows) == (2 if layout == "separate" else 1)
        for row in rows:
            assert row["path"].endswith(".mp4")
            with av.open(row["path"]) as container:
                assert container.streams.audio[0].codec_context.name == "aac"
                samples = np.concatenate([frame.to_ndarray()[0] for frame in container.decode(audio=0)])
            samples = samples[4800:-4800]
            spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
            frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
            expected = 440 if mode == "phone" else 880
            assert abs(frequencies[spectrum.argmax()] - expected) < 5
            for excluded in {440, 880, 1760} - {expected}:
                band = spectrum[np.abs(frequencies - excluded) < 8]
                assert band.max() < spectrum.max() * 0.05
    finally:
        stop.set()
        video_stop.set()
        audio_thread.join(2)
        video_thread.join(2)
