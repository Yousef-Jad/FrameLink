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
        self.capacity = capacity
        self.lock = threading.Lock()
        self.count = 0
        self.dropped = 0
        self.evicted = 0
        self.consumer_sequence = None
        self.max_depth = 0
        self.received = deque(maxlen=240)
        self.intervals_ms = deque(maxlen=240)

    def put(self, rgb, timestamp=None):
        ts = time.monotonic() if timestamp is None else timestamp
        with self.lock:
            if len(self.frames) == self.capacity:
                self.evicted += 1
                if self.consumer_sequence is not None and self.frames[0].sequence > self.consumer_sequence:
                    self.dropped += 1
            arrival = time.monotonic()
            if self.received:
                self.intervals_ms.append((arrival - self.received[-1]) * 1000)
            self.count += 1
            self.frames.append(VideoSample(rgb, ts, self.count))
            self.received.append(arrival)
            if self.consumer_sequence is not None:
                pending = sum(f.sequence > self.consumer_sequence for f in self.frames)
                self.max_depth = max(self.max_depth, pending)

    def latest(self, before=None):
        with self.lock:
            if not self.frames:
                return None
            if before is None:
                return self.frames[-1]
            return next((f for f in reversed(self.frames) if f.timestamp <= before), None)

    def next_after(self, sequence):
        """Return the oldest buffered sample after a consumed sequence."""
        with self.lock:
            self.consumer_sequence = sequence
            candidates = [sample for sample in self.frames if sample.sequence > sequence]
            if not candidates:
                return None
            # If the producer outran the recorder and the deque evicted frames,
            # jump to the newest sample instead of replaying stale backlog.
            sample = candidates[-1] if candidates[0].sequence > sequence + 1 else candidates[0]
            self.consumer_sequence = sample.sequence
            return sample

    def release_consumer(self):
        with self.lock:
            self.consumer_sequence = None

    def clear(self):
        with self.lock:
            self.frames.clear()
            self.received.clear()
            self.intervals_ms.clear()
            self.consumer_sequence = None

    def fps(self):
        with self.lock:
            times = [t for t in self.received if t > time.monotonic() - 3]
        return (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0.0

    def metrics_snapshot(self):
        with self.lock:
            times = list(self.received)
            intervals = sorted(self.intervals_ms)
            if len(times) > 1 and times[-1] > times[0]:
                fps = (len(times) - 1) / (times[-1] - times[0])
            else:
                fps = 0.0

            def percentile(value):
                if not intervals:
                    return 0.0
                return intervals[min(len(intervals) - 1, round((len(intervals) - 1) * value))]

            return {
                "received": self.count,
                "dropped": self.dropped,
                "evicted_history": self.evicted,
                "retained_frames": len(self.frames),
                "queue_depth": sum(f.sequence > self.consumer_sequence for f in self.frames)
                if self.consumer_sequence is not None
                else 0,
                "max_queue_depth": self.max_depth,
                "capture_fps": fps,
                "interval_p50_ms": percentile(0.50),
                "interval_p95_ms": percentile(0.95),
                "interval_p99_ms": percentile(0.99),
            }


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
        # At 1080p one RGB frame is roughly 6 MB; retaining 240 phone frames
        # could consume more than a GB before the encoder catches up.
        self.screen = VideoBuffer(8)
        self.phone = VideoBuffer(16)
        # Preview is latest-value only and must not compete with recording.
        self.phone_preview = VideoBuffer(2)
        self.audio = {name: AudioBuffer() for name in ("phone", "mic", "system")}
