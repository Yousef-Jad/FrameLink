from dataclasses import dataclass, asdict
from pathlib import Path
import json
import threading
import time
import uuid
import av
from lumacapture.media.encode import MediaWriter, inspect_media, validate_media
from lumacapture.media.model import atomic_promote


@dataclass(frozen=True)
class CompressionSettings:
    codec: str = "libx264"
    quality: int = 24
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


def compress(source, destination, settings, progress=lambda value: None, cancelled=lambda: False):
    info = inspect_media(source)
    width = min(settings.width or info["width"], info["width"]) // 2 * 2
    height = max(2, round(info["height"] * width / info["width"]) // 2 * 2)
    fps = min(settings.fps or round(info["fps"]) or 30, round(info["fps"]) or 30)
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
    )
    last_video_pts = -1
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
    audio_pts = 0
    origin = None
    try:
        with av.open(str(source)) as container:
            origin = float(container.start_time or 0) / av.time_base
            for packet in container.demux():
                if cancelled():
                    raise Cancelled("Compression cancelled; original retained")
                for frame in packet.decode():
                    if isinstance(frame, av.VideoFrame):
                        stamp = max(0, float(frame.time or 0) - origin)
                        pts = round(stamp * fps)
                        if pts <= last_video_pts:
                            continue
                        frame = frame.reformat(width=width, height=height, format="rgb24")
                        writer.write_video(frame.to_ndarray(), pts)
                        last_video_pts = pts
                        progress(min(0.98, stamp / max(0.1, info["duration"])))
                    elif isinstance(frame, av.AudioFrame):
                        # Resampling changes the time base; write a continuous sample timeline.
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
    if result["width"] != width or result["height"] != height:
        raise ValueError("Export resolution does not match requested dimensions")
    if settings.target_mb and result["size"] > settings.target_mb * 1024 * 1024:
        raise ValueError(
            "Encoder exceeded the target size. Output retained as a partial file; lower the quality or increase the limit."
        )
    progress(1)
    return result


class CompressionQueue:
    def __init__(self, library, notify=lambda *args: None):
        self.library, self.notify = library, notify
        self.shutdown = threading.Event()
        self.cancelled = set()
        self.lock = threading.Lock()
        self.current = None
        self.thread = threading.Thread(target=self._run, name="compression", daemon=False)
        self.thread.start()

    def enqueue(self, recording_id, settings=None):
        settings = settings or CompressionSettings.preset_named(
            self.library.setting("compression_preset", "Balanced")
        )
        return self.library.enqueue(recording_id, asdict(settings))

    def cancel(self, ident):
        with self.lock:
            self.cancelled.add(ident)
        self.library.update_job(ident, "cancelled", error="Cancelled by user; original retained")

    def retry(self, ident):
        job = next(j for j in self.library.jobs() if j["id"] == ident)
        return self.library.enqueue(job["recording_id"], json.loads(job["settings"]))

    def _run(self):
        while not self.shutdown.wait(0.2):
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

            try:
                item = self.library.get(job["recording_id"])
                if item is None:
                    raise ValueError("Original recording is no longer in the library")
                source = Path(item["path"])
                suffix = uuid.uuid4().hex[:8]
                final = source.with_name(source.stem + f"-export-{suffix}.mp4")
                temp = final.with_name(final.stem + ".partial.mp4")
                info = compress(
                    source, temp, CompressionSettings(**json.loads(job["settings"])), progress, cancelled
                )
                if cancelled():
                    raise Cancelled()
                atomic_promote(temp, final)
                self.library.add(
                    final, info, session=item["session"], thumbnail=item["thumbnail"], original=item["id"]
                )
                self.library.update_job(ident, "complete", 1)
                self.notify("compressed", f"Export saved: {final.name}")
            except Cancelled:
                self.library.update_job(
                    ident, "interrupted" if self.shutdown.is_set() else "cancelled", error="Original retained"
                )
            except Exception as exc:
                self.library.update_job(ident, "failed", error=str(exc))
                self.notify("error", f"Compression failed: {exc}. Original retained.")
            finally:
                self.current = None

    def stop(self):
        self.shutdown.set()
