from pathlib import Path
import shutil
import subprocess
import json
import time
import numpy as np
import pytest
from framelink.media.encode import validate_media, inspect_media
from framelink.media.model import RecordingConfig, State
from framelink.media.buffers import Sources
from framelink.compression.queue import compress, CompressionSettings, CompressionQueue, Cancelled
from framelink.recording.engine import Recorder, recover_recordings
from framelink.storage.library import Library
from conftest import feed_video, wait_until


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


@pytest.mark.integration
@pytest.mark.parametrize("encoder,codec", [("libx265", "hevc"), ("libsvtav1", "av1")])
def test_advanced_compression_streams(media_file, tmp_path, encoder, codec):
    import av

    if encoder not in av.codecs_available:
        pytest.skip(f"{encoder} not included in this platform's FFmpeg build")
    result = compress(
        media_file, tmp_path / "advanced.mp4", CompressionSettings(codec=encoder, preset="fast")
    )
    assert result["codec"] == codec and result["audio"]
    assert result["decoded_frames"] == 45
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
    feeder_stop, feeder_thread = feed_video(sources, fps=60)
    library = Library(tmp_path / "library")
    recorder = Recorder(sources, library, tmp_path / "videos")
    try:
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
        assert recorder.state == State.COMPLETED, recorder.error
        items = library.recordings()
        assert len(items) == count
        for item in items:
            assert Path(item["path"]).is_file()
            validate_media(item["path"], audio=False)
        if count == 2:
            assert items[0]["session"] == items[1]["session"]
            assert abs(items[0]["metadata"]["duration"] - items[1]["metadata"]["duration"]) < 0.04
    finally:
        feeder_stop.set()
        feeder_thread.join(2)


@pytest.mark.integration
def test_phone_disconnection_pause_and_resume(tmp_path):
    sources = Sources()
    sources.screen.put(np.zeros((180, 320, 3), np.uint8))
    sources.phone.put(np.ones((180, 320, 3), np.uint8))
    screen_stop, screen_thread = feed_video(sources, names=("screen",), fps=30)
    phone_stop, phone_thread = feed_video(sources, names=("phone",), fps=30)
    recorder = Recorder(sources, Library(tmp_path / "library"), tmp_path / "video")
    try:
        recorder.start(RecordingConfig(mode="both", disconnect_policy="pause"))
        wait_until(lambda: recorder.metrics["encoded"] >= 4)
        phone_stop.set()
        phone_thread.join(2)
        sources.phone.clear()
        wait_until(lambda: recorder.state == State.PAUSED)
        phone_stop, phone_thread = feed_video(sources, names=("phone",), fps=30)
        recorder.resume()
        wait_until(lambda: recorder.metrics["encoded"] >= 8)
        recorder.stop()
        recorder.thread.join(15)
        assert recorder.state == State.COMPLETED
    finally:
        screen_stop.set()
        phone_stop.set()
        screen_thread.join(2)
        phone_thread.join(2)


@pytest.mark.integration
def test_queue_and_library(media_file, tmp_path):
    library = Library(tmp_path / "library")
    ident = library.add(media_file, inspect_media(media_file))
    queue = CompressionQueue(library)
    queue.capture_active = lambda: True
    job = queue.enqueue(ident, CompressionSettings(preset="veryfast"))
    try:
        time.sleep(0.4)
        assert next(j for j in library.jobs() if j["id"] == job)["status"] == "queued"
        queue.capture_active = lambda: False
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
    partial = output / "FrameLink-interrupted.partial.mkv"
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


@pytest.mark.integration
def test_encoder_initialization_falls_back_to_real_software(tmp_path, monkeypatch):
    import framelink.recording.engine as module

    original_writer = module.MediaWriter

    def writer(path, width, height, fps, encoder, *args, **kwargs):
        if encoder == "h264_nvenc":
            raise RuntimeError("Injected unavailable GPU")
        return original_writer(path, width, height, fps, encoder, *args, **kwargs)

    monkeypatch.setattr(module, "MediaWriter", writer)
    sources = Sources()
    feeder_stop, feeder_thread = feed_video(sources, names=("screen",), fps=30)
    library = Library(tmp_path / "library")
    recorder = Recorder(sources, library, tmp_path / "recordings")
    try:
        recorder.start(RecordingConfig(encoder="h264_nvenc"))
        wait_until(lambda: recorder.metrics["encoded"] >= 6)
        recorder.stop()
        recorder.thread.join(10)
        assert recorder.state == State.COMPLETED, recorder.error
        assert recorder.metrics["encoder"] == "libx264"
        validate_media(library.recordings()[0]["path"])
    finally:
        feeder_stop.set()
        feeder_thread.join(2)


@pytest.mark.integration
def test_recording_audio_mixer_writes_actual_samples(tmp_path):
    import av
    import threading

    sources = Sources()
    feeder_stop, feeder_thread = feed_video(sources, names=("screen",), fps=30)
    done = threading.Event()

    def feed_audio():
        while not done.wait(0.01):
            stamp = time.monotonic()
            tone = np.sin(np.arange(480) * 2 * np.pi * 440 / 48000).astype(np.float32) * 0.2
            sources.audio["mic"].put(np.vstack([tone, tone]), stamp - 0.01)

    producer = threading.Thread(target=feed_audio)
    producer.start()
    library = Library(tmp_path / "library")
    recorder = Recorder(sources, library, tmp_path / "recordings")
    try:
        recorder.start(RecordingConfig(microphone=True))
        wait_until(lambda: recorder.metrics["encoded"] >= 18)
        recorder.pause()
        time.sleep(0.1)
        recorder.resume()
        wait_until(lambda: recorder.metrics["encoded"] >= 25)
        recorder.stop()
        recorder.thread.join(10)
        assert recorder.state == State.COMPLETED, recorder.error
        path = library.recordings()[0]["path"]
        validate_media(path, audio=True)
        with av.open(path) as container:
            amplitudes = [float(np.max(np.abs(f.to_ndarray()))) for f in container.decode(audio=0)]
        assert max(amplitudes) > 0.1
    finally:
        done.set()
        producer.join()
        feeder_stop.set()
        feeder_thread.join(2)
        recorder.stop()
        if recorder.thread:
            recorder.thread.join(10)


@pytest.mark.integration
@pytest.mark.parametrize("suffix", ["mkv", "mp4"])
@pytest.mark.parametrize("audio", [False, True])
def test_actual_process_interruption_recovery(tmp_path, suffix, audio):
    import sys

    script = r"""
import sys, time
from pathlib import Path
import numpy as np
from framelink.media.encode import MediaWriter
path=Path(sys.argv[1])
audio=sys.argv[2]=="True"
writer=MediaWriter(path,320,180,30,audio=audio,fragmented=path.suffix==".mp4")
for i in range(1000):
    writer.write_video(np.full((180,320,3),i%255,np.uint8),i)
    if audio: writer.write_audio(np.zeros((2,1600),np.float32),i*1600)
    if i==95: print("ready",flush=True)
    time.sleep(.005)
"""
    partial = tmp_path / f"FrameLink-killed.partial.{suffix}"
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(partial), str(audio)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout.readline().strip() == "ready"
    finally:
        process.kill()
        process.wait(timeout=5)
    library = Library(tmp_path / "library")
    recovered, failures = recover_recordings(tmp_path, library)
    assert recovered and not failures, failures
    assert partial.exists()
    assert Path(library.recordings()[0]["path"]).suffix == ".mp4"
    assert validate_media(library.recordings()[0]["path"])["decoded_frames"] > 20
    assert validate_media(library.recordings()[0]["path"])["audio"] == audio


def test_mp4_recovery_ignores_exports_and_indexes_orphan_original(tmp_path):
    from framelink.media.encode import MediaWriter

    original = tmp_path / "FrameLink-orphan.mp4"
    writer = MediaWriter(original, 320, 180, 30, fragmented=True)
    for pts in range(30):
        writer.write_video(np.full((180, 320, 3), pts, np.uint8), pts)
    writer.close()
    export = tmp_path / "FrameLink-orphan-export-test.partial.mp4"
    shutil.copyfile(original, export)
    library = Library(tmp_path / "library")
    recovered, failures = recover_recordings(tmp_path, library)
    assert len(recovered) == 1 and not failures
    assert Path(library.recordings()[0]["path"]) == original
    assert not recover_recordings(tmp_path, library)[0]
    assert export.exists()


def test_audio_intervals_remain_contiguous_under_encoder_jitter(tmp_path, monkeypatch):
    from framelink.media.encode import MediaWriter

    sources = Sources()
    feeder_stop, feeder_thread = feed_video(sources, names=("screen",), fps=30)
    intervals = []
    original_read = sources.audio["mic"].read
    original_write = MediaWriter.write_video

    def read(start, count):
        intervals.append((start, count))
        return original_read(start, count)

    def write(writer, rgb, pts):
        time.sleep(0.018 if pts % 2 else 0.002)
        return original_write(writer, rgb, pts)

    monkeypatch.setattr(sources.audio["mic"], "read", read)
    monkeypatch.setattr(MediaWriter, "write_video", write)
    recorder = Recorder(sources, Library(tmp_path / "library"), tmp_path / "recordings")
    try:
        recorder.start(RecordingConfig(microphone=True))
        wait_until(lambda: recorder.metrics["encoded"] >= 12)
    finally:
        recorder.stop()
        recorder.thread.join(10)
        feeder_stop.set()
        feeder_thread.join(2)
    assert recorder.state == State.COMPLETED, recorder.error
    for (start, count), (following, _) in zip(intervals, intervals[1:]):
        assert following == pytest.approx(start + count / 48000, abs=1 / 48000, rel=0)
