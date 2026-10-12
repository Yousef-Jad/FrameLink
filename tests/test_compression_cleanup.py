import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from conftest import wait_until
from framelink.compression.quality import frame_quality
from framelink.compression.queue import CompressionQueue, CompressionSettings
from framelink.media.encode import validate_media
from framelink.storage.library import Library


def run_job(library, ident, settings=None, automatic=False):
    queue = CompressionQueue(library)
    try:
        job = queue.enqueue(ident, settings, automatic=automatic)
        wait_until(lambda: next(j for j in library.jobs() if j["id"] == job)["status"]
                   in {"complete", "failed", "cancelled"}, 30)
        return next(j for j in library.jobs() if j["id"] == job)
    finally:
        queue.stop()
        queue.thread.join(30)


@pytest.mark.parametrize("extension", [".mkv", ".mp4"])
def test_future_recording_replaced_only_after_valid_smaller_export(media_file, tmp_path, extension):
    # Use a real remux when testing MP4; never label MKV bytes as MP4.
    if extension == ".mp4":
        import subprocess
        source = tmp_path / "new.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(media_file), "-c", "copy", str(source)], check=True)
    else:
        source = media_file
    library = Library(tmp_path / "db")
    original_info = validate_media(source)
    ident = library.add(source, original_info, cleanup_eligible=True)
    job = run_job(library, ident)
    assert job["status"] == "complete", job
    record = library.get(ident)
    report = record["metadata"]["compression"]
    assert report["quality"]["passed"], report
    assert report["after_bytes"] < report["before_bytes"]
    assert not source.exists()
    assert len(library.recordings()) == 1
    assert not record["cleanup_eligible"]
    assert report["saved_bytes"] == report["before_bytes"] - report["after_bytes"]
    info = validate_media(record["path"], original_info["duration"], True)
    assert info["decoded_frames"] == original_info["decoded_frames"]


@pytest.mark.parametrize("eligible,keep", [(False, False), (True, True)])
def test_existing_and_keep_original_recordings_are_protected(media_file, tmp_path, eligible, keep):
    library = Library(tmp_path / "db")
    library.set_setting("keep_original", keep)
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=eligible)
    assert run_job(library, ident)["status"] == "complete"
    assert media_file.exists()
    assert library.get(ident)["metadata"]["compression"]["saved_bytes"] == 0


def test_database_migration_never_opts_existing_records_into_cleanup(tmp_path):
    root = tmp_path / "db"
    root.mkdir()
    with sqlite3.connect(root / "library.sqlite3") as db:
        db.execute("CREATE TABLE recordings (id TEXT PRIMARY KEY, session TEXT, path TEXT UNIQUE, created REAL, metadata TEXT, thumbnail TEXT, original TEXT, status TEXT)")
        db.execute("INSERT INTO recordings VALUES ('old','old','old.mkv',0,'{}',NULL,NULL,'Original')")
    library = Library(root)
    assert library.get("old")["cleanup_eligible"] == 0
    assert not library.setting("keep_original", False)


@pytest.mark.parametrize("failure", ["corrupt", "larger", "quality", "frames", "database", "unlink", "changed"])
def test_failed_validation_or_cleanup_preserves_original(media_file, tmp_path, monkeypatch, failure):
    import framelink.compression.queue as module
    library = Library(tmp_path / "db")
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=True)
    real_compress = module.compress

    def compress(*args, **kwargs):
        result = real_compress(*args, **kwargs)
        if failure == "corrupt":
            Path(args[1]).write_bytes(b"damaged")
            validate_media(args[1])
        elif failure == "larger":
            with Path(args[1]).open("ab") as handle:
                handle.write(b"\0" * media_file.stat().st_size)
            result["size"] = Path(args[1]).stat().st_size
        elif failure == "quality":
            result["quality_check"] = {"passed": False, "reason": "Quality below threshold"}
        elif failure == "frames":
            raise ValueError("Export lost frames")
        elif failure == "changed":
            with media_file.open("ab") as handle:
                handle.write(b"changed")
        return result

    monkeypatch.setattr(module, "compress", compress)
    if failure == "database":
        monkeypatch.setattr(library, "replace_with_export", lambda *a: (_ for _ in ()).throw(OSError("DB failed")))
    if failure == "unlink":
        real_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == media_file:
                raise PermissionError("Sharing violation")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
    run_job(library, ident)
    assert media_file.exists()
    assert Path(library.get(ident)["path"]).exists()
    if failure == "larger":
        assert not list(tmp_path.glob("*-export-*"))
    if failure == "unlink":
        assert library.get(ident)["metadata"]["compression"]["saved_bytes"] == 0


def test_quality_gate_detects_destroyed_text_edges():
    source = np.zeros((128, 256), np.uint8)
    source[::4, :] = 235
    source[:, ::7] = 235
    exact = frame_quality(source, source)
    destroyed = frame_quality(source, np.full_like(source, 80))
    assert exact["ssim"] == pytest.approx(1)
    assert exact["edge_psnr_db"] == 100
    assert destroyed["ssim"] < 0.5
    assert destroyed["edge_psnr_db"] < 35


def test_variable_cadence_export_preserves_every_frame_and_timestamp(tmp_path):
    import av
    from framelink.media.encode import MediaWriter
    from framelink.compression.queue import compress
    source = tmp_path / "variable.mp4"
    target = tmp_path / "export.mp4"
    writer = MediaWriter(source, 320, 180, 30)
    timestamps = [i for i in range(90) if i % 7 != 0]
    for i in timestamps:
        writer.write_video(np.full((180, 320, 3), 40 + i, np.uint8), i)
    writer.close()
    info = compress(source, target, CompressionSettings())
    assert info["decoded_frames"] == len(timestamps)
    assert info["quality_check"]["passed"], info["quality_check"]
    with av.open(source) as a, av.open(target) as b:
        original = [float(f.time) for f in a.decode(video=0)]
        exported = [float(f.time) for f in b.decode(video=0)]
    assert np.allclose(np.array(original) - original[0], np.array(exported) - exported[0], atol=0.0001)


def test_compression_preserves_delayed_audio_start(tmp_path):
    from framelink.media.encode import MediaWriter
    from framelink.compression.queue import compress
    source = tmp_path / "delayed.mp4"
    writer = MediaWriter(source, 320, 180, 30, audio=True)
    for i in range(45):
        writer.write_video(np.full((180, 320, 3), 100, np.uint8), i)
        if i >= 9:
            phase = np.arange(i * 1600, (i + 1) * 1600) * 2 * np.pi * 440 / 48000
            tone = (np.sin(phase) * 0.1).astype(np.float32)
            writer.write_audio(np.vstack([tone, tone]), i * 1600)
    writer.close()
    original = validate_media(source)
    output = compress(source, tmp_path / "compressed.mp4", CompressionSettings())
    assert output["audio_start"] == pytest.approx(original["audio_start"], abs=0.025)
    assert output["quality_check"]["passed"], output


def test_downsampled_export_never_replaces_source(media_file, tmp_path):
    library = Library(tmp_path / "db")
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=True)
    job = run_job(library, ident, CompressionSettings(fps=24))
    assert job["status"] == "complete", job
    assert media_file.exists()
    report = library.get(ident)["metadata"]["compression"]
    assert report["original_retained"] and not report["quality"]["passed"]


def test_automatic_quality_failure_discards_only_new_export(media_file, tmp_path):
    library = Library(tmp_path / "db")
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=True)
    assert run_job(library, ident, CompressionSettings(fps=24), automatic=True)["status"] == "complete"
    assert media_file.exists()
    assert not list(tmp_path.glob("*-export-*"))
    report = library.get(ident)["metadata"]["compression"]
    assert report["export_discarded"] and report["saved_bytes"] == 0


def test_cancel_after_validation_never_deletes_original(media_file, tmp_path, monkeypatch):
    import framelink.compression.queue as module
    library = Library(tmp_path / "db")
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=True)
    real_compress = module.compress
    queue = CompressionQueue(library)

    def compress(*args, **kwargs):
        result = real_compress(*args, **kwargs)
        queue.cancel(queue.current)
        return result

    monkeypatch.setattr(module, "compress", compress)
    try:
        job = queue.enqueue(ident)
        wait_until(lambda: next(j for j in library.jobs() if j["id"] == job)["status"] == "cancelled", 30)
        assert media_file.exists()
        assert library.get(ident)["path"] == str(media_file)
    finally:
        queue.stop()
        queue.thread.join(30)


def test_statistics_failure_after_unlink_keeps_valid_indexed_export(media_file, tmp_path, monkeypatch):
    library = Library(tmp_path / "db")
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=True)
    monkeypatch.setattr(library, "update_metadata", lambda *args: (_ for _ in ()).throw(OSError("DB full")))
    job = run_job(library, ident)
    assert job["status"] == "complete"
    assert not media_file.exists()
    record = library.get(ident)
    validate_media(record["path"], audio=True)
    assert record["metadata"]["compression"]["saved_bytes"] == 0  # Conservative pending stats.


def test_enabling_keep_original_during_compression_prevents_unlink(media_file, tmp_path, monkeypatch):
    import framelink.compression.queue as module
    library = Library(tmp_path / "db")
    ident = library.add(media_file, validate_media(media_file), cleanup_eligible=True)
    real_compress = module.compress

    def compress(*args, **kwargs):
        result = real_compress(*args, **kwargs)
        library.set_setting("keep_original", True)
        return result

    monkeypatch.setattr(module, "compress", compress)
    assert run_job(library, ident)["status"] == "complete"
    assert media_file.exists()


def test_keep_original_ui_default_and_savings_label(tmp_path):
    from PySide6.QtWidgets import QApplication
    from framelink.ui.window import MainWindow
    from framelink.ui.library_view import RecordingTile
    app = QApplication.instance() or QApplication([])
    library = Library(tmp_path / "db")
    window = MainWindow(library, tmp_path / "data", smoke=True)
    try:
        assert not window.keep_original.isChecked()
        window.keep_original.setChecked(True)
        assert library.setting("keep_original") is True
        report = {"before_bytes": 20000000, "after_bytes": 10000000,
                  "saved_bytes": 10000000, "reduction_percent": 50,
                  "original_retained": False, "reason": "Verified"}
        ident = library.add(tmp_path / "result.mp4", {"compression": report})
        tile = RecordingTile(library.get(ident), [])
        assert "50.0%" in tile.savings.text() and "Disk saved" in tile.savings.text()
        json.dumps(library.recordings())
    finally:
        window.compression.stop()
        window.compression.thread.join(5)
        window.close()
        app.processEvents()


@pytest.mark.parametrize("keep_at_start", [False, True])
def test_recorder_snapshots_retention_policy_only_for_future_recordings(tmp_path, keep_at_start):
    from conftest import feed_video
    from framelink.media.buffers import Sources
    from framelink.media.model import RecordingConfig, State
    from framelink.recording.engine import Recorder
    library = Library(tmp_path / "db")
    old = library.add(tmp_path / "existing.mp4", {})
    library.set_setting("keep_original", keep_at_start)
    sources = Sources()
    stop, thread = feed_video(sources)
    recorder = Recorder(sources, library, tmp_path / "videos")
    try:
        wait_until(lambda: sources.screen.latest() is not None)
        recorder.start(RecordingConfig())
        library.set_setting("keep_original", not keep_at_start)
        wait_until(lambda: recorder.metrics["encoded"] >= 5)
        recorder.stop()
        recorder.thread.join(10)
        assert recorder.state == State.COMPLETED
        current = next(row for row in library.recordings() if row["id"] != old)
        assert bool(current["cleanup_eligible"]) is not keep_at_start
        assert not library.get(old)["cleanup_eligible"]
    finally:
        recorder.stop()
        if recorder.thread:
            recorder.thread.join(10)
        stop.set()
        thread.join(2)
