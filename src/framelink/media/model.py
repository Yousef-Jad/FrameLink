from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import threading
import time


class State(str, Enum):
    IDLE = "Ready"
    PREPARING = "Preparing"
    RECORDING = "Recording"
    PAUSED = "Paused"
    STOPPING = "Stopping"
    FINALIZING = "Finalizing recording"
    COMPLETED = "Completed"
    ERROR = "Error"

    @property
    def active(self):
        return self in {self.PREPARING, self.RECORDING, self.PAUSED, self.STOPPING, self.FINALIZING}


class SessionClock:
    """Monotonic session time; all output files share this clock."""

    def __init__(self, now=time.monotonic):
        self.now = now
        self.started = now()
        self.paused_at = None
        self.paused_total = 0.0

    def elapsed(self):
        return (
            (self.paused_at if self.paused_at is not None else self.now()) - self.started - self.paused_total
        )

    def pause(self):
        if self.paused_at is None:
            self.paused_at = self.now()

    def resume(self):
        if self.paused_at is not None:
            self.paused_total += self.now() - self.paused_at
            self.paused_at = None


class StateMachine:
    allowed = {
        State.IDLE: {State.PREPARING},
        State.PREPARING: {State.RECORDING, State.STOPPING, State.ERROR},
        State.RECORDING: {State.PAUSED, State.STOPPING, State.ERROR},
        State.PAUSED: {State.RECORDING, State.STOPPING, State.ERROR},
        State.STOPPING: {State.FINALIZING, State.ERROR},
        State.FINALIZING: {State.COMPLETED, State.ERROR},
        State.COMPLETED: {State.IDLE},
        State.ERROR: {State.IDLE},
    }

    def __init__(self):
        self.state = State.IDLE
        self.lock = threading.RLock()
        self.history = [State.IDLE]

    def transition(self, state):
        with self.lock:
            if state not in self.allowed[self.state]:
                raise ValueError(f"Invalid recording transition: {self.state.value} → {state.value}")
            self.state = state
            self.history.append(state)


class TimestampNormalizer:
    """Map a source clock onto monotonic receive time, rejecting backwards timestamps."""

    def __init__(self):
        self.offset = None
        self.last = float("-inf")

    def map(self, pts, arrival):
        if self.offset is None:
            self.offset = arrival - pts
        mapped = pts + self.offset
        if abs(mapped - arrival) > 2:
            self.offset = arrival - pts
            mapped = arrival
        mapped = max(self.last + 0.000001, min(mapped, arrival + 0.1))
        self.last = mapped
        return mapped


@dataclass(frozen=True)
class RecordingConfig:
    mode: str = "screen"
    layout: str = "pip"
    fps: int = 30
    width: int = 0
    height: int = 0
    encoder: str = "libx264"
    quality: int = 20
    overlay_size: float = 0.25
    overlay_position: str = "bottom-right"
    opacity: float = 1.0
    radius: int = 24
    border: int = 2
    mirror: bool = False
    disconnect_policy: str = "hide"
    phone_audio: bool = False
    microphone: bool = False
    system_audio: bool = False
    phone_gain: float = 1.0
    mic_gain: float = 1.0
    system_gain: float = 1.0

    def __post_init__(self):
        if self.mode not in {"screen", "phone", "both"} or self.layout not in {"pip", "separate"}:
            raise ValueError("Unknown recording mode")
        if self.fps not in {15, 30, 60, 120}:
            raise ValueError("FPS must be 15, 30, 60 or 120")
        if (
            (self.width == 0) != (self.height == 0)
            or not 0 <= self.width <= 7680
            or not 0 <= self.height <= 4320
        ):
            raise ValueError("Invalid output dimensions")
        if self.width and (self.width < 64 or self.height < 64 or self.width % 2 or self.height % 2):
            raise ValueError("Output dimensions must be even and at least 64")
        if not 0.1 <= self.overlay_size <= 0.6 or not 0 <= self.opacity <= 1:
            raise ValueError("Invalid overlay settings")
        if self.disconnect_policy not in {"hide", "freeze", "pause", "stop"}:
            raise ValueError("Unknown disconnection policy")
        if self.encoder not in {"libx264", "h264_nvenc", "h264_qsv", "h264_amf"}:
            raise ValueError("Unsupported recording encoder")


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:120]
    if not value or value.upper().split(".")[0] in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(10)),
        *(f"LPT{i}" for i in range(10)),
    }:
        raise ValueError("Choose a valid filename")
    return value


def atomic_promote(source: Path, destination: Path):
    """Never replace an existing recording, even after an interrupted finalization."""
    import os

    # Windows FlushFileBuffers requires a writable file handle.
    with source.open("r+b") as handle:
        os.fsync(handle.fileno())
    # Hard-link creation is atomic and fails if the destination already exists.
    os.link(source, destination)
    source.unlink()
    if os.name != "nt":
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
