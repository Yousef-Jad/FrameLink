from dataclasses import dataclass, asdict
from pathlib import Path
from fractions import Fraction
import json
import threading
import time
import uuid
import os
import av
from framelink.media.encode import MediaWriter, inspect_media, validate_media
from framelink.media.model import atomic_promote
from framelink.compression.quality import compare_quality


@dataclass(frozen=True)
class CompressionSettings:
    codec: str = "libx264"
    quality: int = 22
    preset: str = "medium"
    width: int = 0
    fps: int = 0
    audio_bitrate: int = 128000
    target_mb: int = 0

    def __post_init__(self):
        if self.codec not in {"libx264", "libx265", "libsvtav1", "libaom-av1"}:
            raise ValueError("Unsupported compression codec")
        if not 0 <= self.quality <= 51 or self.preset not in {
            "ultrafast",
            "veryfast",
            "fast",
            "medium",
            "slow",
        }:
            raise ValueError("Invalid quality or encoder preset")
        if self.width not in {0, 640, 1280, 1920, 2560, 3840} or self.fps not in {0, 24, 30, 60}:
            raise ValueError("Invalid export dimensions or frame rate")
        if not 32000 <= self.audio_bitrate <= 320000 or not 0 <= self.target_mb <= 100000:
            raise ValueError("Invalid audio bitrate or target size")

    @classmethod
    def preset_named(cls, name):
        presets = {
            "Fast": cls(quality=23, preset="veryfast"),
            "Balanced": cls(),
            "Maximum Compression": cls(quality=28, preset="slow"),
            "Telegram": cls(quality=26, preset="medium", width=1280, fps=30, audio_bitrate=96000),
        }
        return presets[name]


class Cancelled(Exception):
    pass


def compress(
    source,
    destination,
    settings,
    progress=lambda value: None,
    cancelled=lambda: False,
    wait_for_capture=lambda: None,
):
    info = inspect_media(source)
    started = time.monotonic()
    width = min(settings.width or info["width"], info["width"]) // 2 * 2
    height = max(2, round(info["height"] * width / info["width"]) // 2 * 2)
    fps = min(settings.fps or round(info["fps"]) or 30, round(info["fps"]) or 30)
    time_base = Fraction(1, fps) if settings.fps else Fraction(1, 90000)
    bitrate = None
    if settings.target_mb:
        bitrate = int(settings.target_mb * 1024 * 1024 * 8 * 0.96 / max(0.1, info["duration"])) - (
            settings.audio_bitrate if info["audio"] else 0
        )
        if bitrate < 100000:
            raise ValueError("The requested size is too small for this duration and audio bitrate")
    writer = MediaWriter(
        destination,
        width,
        height,
        fps,
        settings.codec,
        info["audio"],
        settings.quality,
        settings.preset,
        settings.audio_bitrate,
        bitrate,
        realtime=False,
        video_time_base=time_base,
    )
    last_video_pts = -1
    written_frames = 0
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
    audio_pts = 0
    audio_started = False
    origin = None
    try:
        with av.open(str(source)) as container:
            origin = float(container.start_time or 0) / av.time_base
            for packet in container.demux():
                wait_for_capture()
                if cancelled():
                    raise Cancelled("Compression cancelled; original retained")
                for frame in packet.decode():
                    if isinstance(frame, av.VideoFrame):
                        stamp = max(0, float(frame.time or 0) - origin)
                        pts = round(stamp / time_base)
                        if pts <= last_video_pts:
                            if not settings.fps:
                                raise ValueError("Non-monotonic source timestamps; original retained")
                            continue
                        frame = frame.reformat(width=width, height=height, format="rgb24")
                        writer.write_video(frame.to_ndarray(), pts)
                        written_frames += 1
                        last_video_pts = pts
                        progress(min(0.98, stamp / max(0.1, info["duration"])))
                    elif isinstance(frame, av.AudioFrame):
                        # Resampling changes the time base; write a continuous sample timeline.
                        if not audio_started:
                            audio_pts = round(max(0, float(frame.time or 0) - origin) * 48000)
                            audio_started = True
                        for out in resampler.resample(frame):
                            writer.write_audio(out.to_ndarray(), audio_pts)
                            audio_pts += out.samples
            if info["audio"]:
                for out in resampler.resample(None):
                    writer.write_audio(out.to_ndarray(), audio_pts)
                    audio_pts += out.samples
    finally:
        writer.close()
    if cancelled():
        raise Cancelled("Compression cancelled; original retained")
    result = validate_media(destination, info["duration"], info["audio"])
    if result["decoded_frames"] != written_frames:
        raise ValueError("Export lost frames during encoding or finalization")
    if result["width"] != width or result["height"] != height:
        raise ValueError("Export resolution does not match requested dimensions")
    if settings.target_mb and result["size"] > settings.target_mb * 1024 * 1024:
        raise ValueError(
            "Encoder exceeded the target size. Output retained as a partial file; lower the quality or increase the limit."
        )
    def checkpoint():
        wait_for_capture()
        if cancelled():
            raise Cancelled("Compression validation cancelled; original retained")

    checkpoint()
    reference = validate_media(source)
    result["quality_check"] = compare_quality(source, destination, reference, result, checkpoint)
    result["compression_seconds"] = time.monotonic() - started
    progress(1)
    return result


class CompressionQueue:
    def __init__(self, library, notify=lambda *args: None):
        self.library, self.notify = library, notify
        self.shutdown = threading.Event()
        self.cancelled = set()
        self.lock = threading.Lock()
        self.current = None
        self.capture_active = lambda: False
        self.thread = threading.Thread(target=self._run, name="compression", daemon=False)
        self.thread.start()

    def enqueue(self, recording_id, settings=None, automatic=False):
        settings = settings or CompressionSettings.preset_named(
            self.library.setting("compression_preset", "Balanced")
        )
        return self.library.enqueue(recording_id, asdict(settings), automatic=automatic)

    def cancel(self, ident):
        with self.lock:
            self.cancelled.add(ident)
        self.library.update_job(ident, "cancelled", error="Cancelled by user; original retained")

    def retry(self, ident):
        job = next(j for j in self.library.jobs() if j["id"] == ident)
        return self.library.enqueue(job["recording_id"], json.loads(job["settings"]), automatic=job["automatic"])

    def _run(self):
        while not self.shutdown.wait(0.2):
            if self.capture_active():
                continue
            jobs = [j for j in self.library.jobs() if j["status"] == "queued"]
            if not jobs:
                continue
            job = jobs[0]
            ident = job["id"]
            self.current = ident
            with self.lock:
                if ident in self.cancelled:
                    continue
            self.library.update_job(ident, "running")
            last_progress = [0.0]

            def progress(value):
                now = time.monotonic()
                if now - last_progress[0] > 0.5:
                    self.library.update_job(ident, "running", value)
                    last_progress[0] = now

            def cancelled():
                with self.lock:
                    return self.shutdown.is_set() or ident in self.cancelled

            def wait_for_capture():
                while self.capture_active() and not cancelled():
                    self.shutdown.wait(0.1)

            try:
                item = self.library.get(job["recording_id"])
                if item is None:
                    raise ValueError("Original recording is no longer in the library")
                source = Path(item["path"])
                source_identity = file_identity(source)
                suffix = uuid.uuid4().hex[:8]
                final = source.with_name(source.stem + f"-export-{suffix}.mp4")
                temp = final.with_name(final.stem + ".partial.mp4")
                info = compress(
                    source,
                    temp,
                    CompressionSettings(**json.loads(job["settings"])),
                    progress,
                    cancelled,
                    wait_for_capture,
                )
                if cancelled():
                    raise Cancelled()
                with self.library.mutation_lock:
                    current = self.library.get(item["id"])
                    if not current or current["path"] != str(source) or file_identity(source) != source_identity:
                        raise ValueError("Original changed during compression; no files removed")
                    before, after = source_identity[2], info["size"]
                    report = {
                        "before_bytes": before, "after_bytes": after,
                        "reduction_percent": (1 - after / before) * 100,
                        "saved_bytes": 0, "original_retained": True,
                        "original_path": str(source),
                        "quality": info["quality_check"],
                        "seconds": info["compression_seconds"],
                        "codec": info["codec"],
                        "reason": "Original retained",
                    }
                    info["compression"] = report
                    reject_quality = job["automatic"] and not info["quality_check"]["passed"]
                    if after >= before or reject_quality:
                        report["reason"] = (
                            "Export was not smaller; discarded new export" if after >= before
                            else "Automatic export failed quality gate; discarded new export"
                        )
                        report["export_discarded"] = True
                        self.library.update_metadata(item["id"], item["metadata"] | {"compression": report})
                        temp.unlink()  # Only this job's new, non-beneficial output.
                    else:
                        atomic_promote(temp, final)
                        durable_file(final)
                        eligible = (
                            current["cleanup_eligible"] and not source.is_symlink()
                            and not self.library.setting("keep_original", False)
                            and info["quality_check"]["passed"]
                        )
                        if eligible:
                            # The database must point at a valid durable export before unlink.
                            # A crash before unlink leaves an extra original, never a lost video.
                            report["reason"] = "Cleanup pending; original retained"
                            self.library.replace_with_export(item["id"], final, info)
                            try:
                                if (cancelled() or self.library.setting("keep_original", False)
                                        or file_identity(source) != source_identity):
                                    raise OSError("Cleanup interrupted or original changed")
                                source.unlink()
                            except OSError as exc:
                                report["reason"] = f"Original retained: {exc}"
                            else:
                                report.update(original_retained=False, saved_bytes=before - after,
                                              reason="Validated smaller MP4 replaced original")
                            try:
                                self.library.update_metadata(item["id"], info)
                            except Exception as exc:
                                # The durable export and its DB pointer already exist.
                                # Leave conservative pending statistics for crash recovery.
                                self.notify("error", f"Export saved; storage statistics update failed: {exc}")
                        else:
                            report["reason"] = (
                                "Existing/imported recording protected" if not current["cleanup_eligible"]
                                else "Keep Original enabled" if self.library.setting("keep_original", False)
                                else info["quality_check"]["reason"]
                            )
                            self.library.add(final, info, session=item["session"],
                                             thumbnail=item["thumbnail"], original=item["id"])
                            self.library.update_metadata(item["id"], item["metadata"] | {"compression": report})
                self.library.update_job(ident, "complete", 1)
                self.notify("compressed", report["reason"])
            except Cancelled:
                self.library.update_job(
                    ident, "interrupted" if self.shutdown.is_set() else "cancelled", error="Original retained"
                )
            except Exception as exc:
                self.library.update_job(ident, "failed", error=str(exc))
                self.notify("error", f"Compression failed: {exc}. Check the library for retained files.")
            finally:
                self.current = None

    def stop(self):
        self.shutdown.set()


def file_identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def durable_file(path):
    # Windows FlushFileBuffers requires a writable handle.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
