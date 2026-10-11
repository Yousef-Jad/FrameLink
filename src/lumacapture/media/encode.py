from fractions import Fraction
from pathlib import Path
import time
import av
import numpy as np


RECORD_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")


def encoder_options(name, quality=20, preset="fast"):
    if name == "h264_nvenc":
        return {"preset": "p4", "rc": "vbr", "cq": str(quality), "bf": "0"}
    if name == "h264_qsv":
        return {"global_quality": str(quality), "preset": "veryfast", "bf": "0"}
    if name == "h264_amf":
        return {"quality": "speed", "rc": "cqp", "qp_i": str(quality), "qp_p": str(quality)}
    if "svtav1" in name:
        return {"crf": str(quality), "preset": "8" if preset == "fast" else "6"}
    if "aom" in name:
        return {"crf": str(quality), "cpu-used": "8" if preset == "fast" else "6"}
    options = {"crf": str(quality), "preset": preset}
    if name == "libx264":
        options.update(tune="zerolatency")
    if name == "libx265":
        options["x265-params"] = "log-level=error:pools=2"
    return options


def probe_encoder(name, width=640, height=360, frames=8):
    """Actually open and encode; an advertised codec is not proof of a working GPU."""
    start = time.perf_counter()
    try:
        ctx = av.CodecContext.create(name, "w")
        ctx.width, ctx.height = width, height
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, 30)
        ctx.framerate = Fraction(30)
        ctx.thread_count = 2
        ctx.options = encoder_options(name)
        ctx.open()
        rgb = np.zeros((height, width, 3), np.uint8)
        packets = 0
        for i in range(frames):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = i
            packets += len(ctx.encode(frame))
        packets += len(ctx.encode())
        return {
            "name": name,
            "available": packets > 0,
            "fps": frames / (time.perf_counter() - start),
            "error": "",
        }
    except Exception as exc:
        return {"name": name, "available": False, "fps": 0.0, "error": str(exc)}


class MediaWriter:
    def __init__(
        self,
        path: Path,
        width,
        height,
        fps,
        encoder="libx264",
        audio=False,
        quality=20,
        preset="veryfast",
        audio_bitrate=128000,
        bitrate=None,
    ):
        self.path = path
        self.width, self.height = width, height
        self.fps = fps
        self.closed = False
        self.container = av.open(
            str(path),
            "w",
            format="mp4" if path.suffix == ".mp4" else "matroska",
            options={"movflags": "+faststart"}
            if path.suffix == ".mp4"
            else {"cluster_time_limit": "1000", "flush_packets": "1"},
        )
        self.video = self.container.add_stream(encoder, rate=fps)
        self.video.width, self.video.height = width, height
        self.video.pix_fmt = "yuv420p"
        self.video.time_base = Fraction(1, fps)
        self.video.codec_context.thread_count = 2
        self.video.options = encoder_options(encoder, quality, preset)
        if bitrate:
            self.video.options.pop("crf", None)
            self.video.bit_rate = bitrate
        self.video.codec_context.gop_size = fps
        self.audio = self.container.add_stream("aac", rate=48000) if audio else None
        if self.audio:
            self.audio.layout = "stereo"
            self.audio.bit_rate = audio_bitrate
            self.audio.time_base = Fraction(1, 48000)
        try:
            self.container.start_encoding()
        except Exception:
            self.container.close()
            raise

    def write_video(self, rgb, pts):
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts = pts
        frame.time_base = Fraction(1, self.fps)
        for packet in self.video.encode(frame):
            self.container.mux(packet)

    def write_audio(self, samples, pts):
        if self.audio is None or not samples.shape[1]:
            return
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(samples, dtype=np.float32), format="fltp", layout="stereo"
        )
        frame.sample_rate = 48000
        frame.pts = pts
        frame.time_base = Fraction(1, 48000)
        for packet in self.audio.encode(frame):
            self.container.mux(packet)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            for packet in self.video.encode():
                self.container.mux(packet)
            if self.audio:
                for packet in self.audio.encode():
                    self.container.mux(packet)
        finally:
            self.container.close()


def inspect_media(path, full=False):
    with av.open(str(path)) as container:
        videos = container.streams.video
        if not videos:
            raise ValueError("Output contains no video stream")
        v = videos[0]
        result = {
            "width": v.width,
            "height": v.height,
            "codec": v.codec_context.name,
            "fps": float(v.average_rate or v.base_rate or 0),
            "duration": float(container.duration or 0) / av.time_base,
            "audio": bool(container.streams.audio),
            "size": Path(path).stat().st_size,
        }
        if full:
            counts = {s.index: 0 for s in container.streams if s.type in {"video", "audio"}}
            ends = {}
            starts = {}
            for packet in container.demux():
                for frame in packet.decode():
                    counts[packet.stream.index] = counts.get(packet.stream.index, 0) + 1
                    if frame.pts is not None:
                        stamp = float(frame.pts * frame.time_base)
                        starts.setdefault(packet.stream.index, stamp)
                        duration = (
                            frame.samples / frame.sample_rate
                            if isinstance(frame, av.AudioFrame)
                            else 1 / max(1, result["fps"])
                        )
                        ends[packet.stream.index] = stamp + duration
            if any(n == 0 for n in counts.values()):
                raise ValueError("Output has an empty media stream")
            result["decoded_frames"] = counts[v.index]
            result["duration"] = max(ends.values(), default=result["duration"])
            if container.streams.audio:
                a = container.streams.audio[0].index
                if abs(ends.get(a, 0) - ends.get(v.index, 0)) > 0.3:
                    raise ValueError("Audio and video end times differ by more than 300 ms")
            result["stream_starts"] = starts
        return result


def validate_media(path, expected_duration=None, audio=None):
    info = inspect_media(path, full=True)
    if info["duration"] <= 0 or info["size"] < 256:
        raise ValueError("Recording is empty")
    if expected_duration is not None and abs(info["duration"] - expected_duration) > max(
        0.35, expected_duration * 0.03
    ):
        raise ValueError(
            f"Duration mismatch: expected {expected_duration:.2f}s, found {info['duration']:.2f}s"
        )
    if audio is not None and info["audio"] != audio:
        raise ValueError("Output audio stream does not match the recording")
    return info
