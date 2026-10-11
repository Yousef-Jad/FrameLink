from pathlib import Path
import shutil
import subprocess
import json
import time
import numpy as np
import pytest
from lumacapture.media.encode import validate_media, inspect_media
from lumacapture.media.model import RecordingConfig, State
from lumacapture.media.buffers import Sources
from lumacapture.compression.queue import compress, CompressionSettings, CompressionQueue, Cancelled
from lumacapture.recording.engine import Recorder, recover_recordings
from lumacapture.storage.library import Library
from conftest import wait_until


@pytest.mark.integration
def test_media_stream_integrity(media_file):
    info = validate_media(media_file, 1.5, True)
    assert info["codec"] == "h264" and info["width"] == 320 and info["height"] == 180
    assert info["decoded_frames"] == 45
    if shutil.which("ffprobe"):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media_file)],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = json.loads(result.stdout)["streams"]
        assert {s["codec_type"] for s in streams} == {"video", "audio"}
        assert next(s for s in streams if s["codec_type"] == "video")["codec_name"] == "h264"


@pytest.mark.integration
def test_compression_valid_mp4(media_file, tmp_path):
    result = compress(media_file, tmp_path / "export.mp4", CompressionSettings(fps=24, preset="veryfast"))
    assert result["codec"] == "h264" and result["audio"]
    assert result["duration"] == pytest.approx(1.5, abs=0.1)


def test_compression_cancellation_retains_original(media_file, tmp_path):
    original = media_file.read_bytes()
    with pytest.raises(Cancelled):
        compress(media_file, tmp_path / "cancel.partial.mp4", CompressionSettings(), cancelled=lambda: True)
    assert media_file.read_bytes() == original


@pytest.mark.parametrize(
    "mode,layout,count",
    [("screen", "pip", 1), ("phone", "pip", 1), ("both", "pip", 1), ("both", "separate", 2)],
)
@pytest.mark.integration
def test_recording_modes_pause_and_library(tmp_path, mode, layout, count):
    sources = Sources()
    rgb = np.full((180, 320, 3), 80, np.uint8)
    sources.screen.put(rgb)
    sources.phone.put(rgb)
    library = Library(tmp_path / "library")
    recorder = Recorder(sources, library, tmp_path / "videos")
    recorder.start(RecordingConfig(mode=mode, layout=layout))
    wait_until(lambda: recorder.metrics["encoded"] >= 5)
    recorder.pause()
    paused = recorder.clock.elapsed()
    time.sleep(0.15)
    assert recorder.clock.elapsed() == paused
    recorder.resume()
    wait_until(lambda: recorder.metrics["encoded"] >= 12)
    recorder.stop()
    recorder.thread.join(15)
    assert recorder.state == State.IDLE, recorder.error
    items = library.recordings()
    assert len(items) == count
    for item in items:
        assert Path(item["path"]).is_file()
        validate_media(item["path"], audio=False)
    if count == 2:
        assert items[0]["session"] == items[1]["session"]
        assert abs(items[0]["metadata"]["duration"] - items[1]["metadata"]["duration"]) < 0.04


@pytest.mark.integration
def test_phone_disconnection_pause_and_resume(tmp_path):
    sources = Sources()
    sources.screen.put(np.zeros((180, 320, 3), np.uint8))
    sources.phone.put(np.ones((180, 320, 3), np.uint8))
    recorder = Recorder(sources, Library(tmp_path / "library"), tmp_path / "video")
    recorder.start(RecordingConfig(mode="both", disconnect_policy="pause"))
    wait_until(lambda: recorder.metrics["encoded"] >= 4)
    sources.phone.clear()
    wait_until(lambda: recorder.state == State.PAUSED)
    sources.phone.put(np.ones((180, 320, 3), np.uint8))
    recorder.resume()
    wait_until(lambda: recorder.metrics["encoded"] >= 8)
    recorder.stop()
    recorder.thread.join(15)
    assert recorder.state == State.IDLE


@pytest.mark.integration
def test_queue_and_library(media_file, tmp_path):
    library = Library(tmp_path / "library")
    ident = library.add(media_file, inspect_media(media_file))
    queue = CompressionQueue(library)
    job = queue.enqueue(ident, CompressionSettings(preset="veryfast"))
    try:
        wait_until(
            lambda: next(j for j in library.jobs() if j["id"] == job)["status"] in {"complete", "failed"}, 20
        )
        result = next(j for j in library.jobs() if j["id"] == job)
        assert result["status"] == "complete", result["error"]
        assert len(library.recordings()) == 2 and media_file.exists()
    finally:
        queue.stop()
        queue.thread.join(20)


@pytest.mark.integration
def test_interrupted_recording_recovery(media_file, tmp_path):
    output = tmp_path / "recordings"
    output.mkdir()
    partial = output / "LumaCapture-interrupted.partial.mkv"
    shutil.copyfile(media_file, partial)
    library = Library(tmp_path / "library")
    recovered, failures = recover_recordings(output, library)
    assert not failures and len(recovered) == 1
    assert partial.exists()
    assert len(recover_recordings(output, library)[0]) == 0
    assert len(library.recordings()) == 1


def test_failed_output_validation_preserves_original(media_file, tmp_path):
    with pytest.raises(ValueError):
        validate_media(media_file, expected_duration=60)
    assert media_file.exists()


def test_library_safe_rename_and_metadata(media_file, tmp_path):
    library = Library(tmp_path / "library")
    ident = library.add(media_file, inspect_media(media_file))
    library.rename(ident, "New session")
    renamed = library.get(ident)
    assert Path(renamed["path"]).name == "New session.mkv"
    assert renamed["metadata"]["width"] == 320
    library.set_setting("auto_compress", False)
    assert library.setting("auto_compress") is False
