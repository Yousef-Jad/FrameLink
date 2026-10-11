from collections import deque
from dataclasses import dataclass
import threading
import time
import numpy as np


@dataclass(frozen=True)
class VideoSample:
    rgb: np.ndarray
    timestamp: float
    sequence: int


class VideoBuffer:
    """Bounded receive queue. Capture never waits for disk or an encoder."""

    def __init__(self, capacity=4):
        self.frames = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.count = 0
        self.received = deque(maxlen=240)

    def put(self, rgb, timestamp=None):
        ts = time.monotonic() if timestamp is None else timestamp
        with self.lock:
            self.count += 1
            self.frames.append(VideoSample(rgb, ts, self.count))
            self.received.append(time.monotonic())

    def latest(self, before=None):
        with self.lock:
            if not self.frames:
                return None
            if before is None:
                return self.frames[-1]
            return next((f for f in reversed(self.frames) if f.timestamp <= before), None)

    def clear(self):
        with self.lock:
            self.frames.clear()
            self.received.clear()

    def fps(self):
        with self.lock:
            times = [t for t in self.received if t > time.monotonic() - 3]
        return (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0.0


class AudioBuffer:
    """Two seconds of 48 kHz stereo float audio, indexed by monotonic time."""

    def __init__(self):
        self.chunks = deque(maxlen=200)
        self.lock = threading.Lock()

    def put(self, samples, timestamp):
        if samples.ndim != 2 or samples.shape[0] != 2 or samples.shape[1] > 48000:
            raise ValueError("Audio must be stereo, planar, at most one second")
        with self.lock:
            self.chunks.append((timestamp, samples))
            while self.chunks and self.chunks[0][0] < timestamp - 2:
                self.chunks.popleft()

    def read(self, start, count):
        out = np.zeros((2, count), dtype=np.float32)
        with self.lock:
            for timestamp, samples in self.chunks:
                offset = round((timestamp - start) * 48000)
                a, b = max(0, offset), min(count, offset + samples.shape[1])
                if b > a:
                    out[:, a:b] = samples[:, a - offset : b - offset]
        return out

    def clear(self):
        with self.lock:
            self.chunks.clear()


class Sources:
    def __init__(self):
        self.screen = VideoBuffer()
        self.phone = VideoBuffer(8)
        self.audio = {name: AudioBuffer() for name in ("phone", "mic", "system")}
