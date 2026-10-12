import threading
import numpy as np
import pytest
from framelink.media.model import (
    StateMachine,
    State,
    SessionClock,
    TimestampNormalizer,
    safe_filename,
    atomic_promote,
    RecordingConfig,
)
from framelink.media.buffers import VideoBuffer, AudioBuffer
from framelink.capture.desktop import backend_name
from framelink.media.encode import probe_encoder
from framelink.security.pairing import PairingTokens, RateLimiter
from framelink.networking.interfaces import local_address


def test_recording_transitions():
    machine = StateMachine()
    with pytest.raises(ValueError):
        machine.transition(State.PAUSED)
    for state in [
        State.PREPARING,
        State.RECORDING,
        State.PAUSED,
        State.RECORDING,
        State.STOPPING,
        State.FINALIZING,
        State.COMPLETED,
        State.IDLE,
    ]:
        machine.transition(state)
    machine.transition(State.PREPARING)
    machine.transition(State.RECORDING)
    machine.transition(State.ERROR)
    with pytest.raises(ValueError):
        machine.transition(State.RECORDING)
    machine.transition(State.IDLE)


def test_pause_clock():
    now = [10.0]
    clock = SessionClock(lambda: now[0])
    now[0] = 12
    clock.pause()
    now[0] = 50
    assert clock.elapsed() == 2
    clock.resume()
    now[0] = 53
    assert clock.elapsed() == 5


def test_timestamp_discontinuity_and_reconnection():
    normalizer = TimestampNormalizer()
    assert normalizer.map(42, 10) == 10
    assert normalizer.map(43, 11) == 11
    assert normalizer.map(1, 12) == 12
    assert normalizer.map(0.5, 12.01) > 12
    reconnect = TimestampNormalizer()
    assert reconnect.map(0, 14) == 14


def test_atomic_finalization_does_not_overwrite(tmp_path):
    source, dest = tmp_path / "recording.partial", tmp_path / "recording.mkv"
    source.write_bytes(b"new")
    dest.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        atomic_promote(source, dest)
    assert source.read_bytes() == b"new" and dest.read_bytes() == b"original"
    dest.unlink()
    atomic_promote(source, dest)
    assert dest.read_bytes() == b"new" and not source.exists()


@pytest.mark.parametrize("name", ["CON", "aux.txt", "..", "  ", "LPT1"])
def test_unsafe_filename(name):
    with pytest.raises(ValueError):
        safe_filename(name)


def test_safe_filename():
    assert "/" not in safe_filename("../../video")
    assert safe_filename("Session 01") == "Session 01"


def test_backend_selection():
    assert "portal" in backend_name("linux", {"WAYLAND_DISPLAY": "wayland-1"})
    assert "X11" in backend_name("linux", {})
    assert "DXGI" in backend_name("win32", {})
    with pytest.raises(RuntimeError):
        backend_name("unsupported", {})


def test_bounded_video_buffer():
    buffer = VideoBuffer(2)
    for i in range(10):
        buffer.put(np.zeros((8, 8, 3), np.uint8), i)
    assert len(buffer.frames) == 2 and buffer.latest().sequence == 10
    assert buffer.latest(before=8).timestamp == 8
    assert buffer.latest(before=2) is None
    buffer.clear()
    assert buffer.latest() is None


def test_audio_timestamp_mixing():
    buffer = AudioBuffer()
    buffer.put(np.ones((2, 480), np.float32) * 0.5, 1)
    result = buffer.read(0.995, 960)
    assert np.all(result[:, :240] == 0)
    assert np.all(result[:, 240:720] == 0.5)
    assert np.all(result[:, 720:] == 0)


def test_token_single_use_expiry_and_rotation():
    now = [1]
    tokens = PairingTokens(ttl=2, now=lambda: now[0])
    old = tokens.issue()
    token = tokens.issue()
    assert not tokens.consume(old) and not tokens.consume("invalid")
    assert tokens.consume(token) and not tokens.consume(token)
    expired = tokens.issue()
    now[0] = 3
    assert not tokens.consume(expired)
    assert not tokens.consume(None)


def test_token_consumption_is_atomic():
    tokens = PairingTokens()
    token = tokens.issue()
    results = []
    threads = [threading.Thread(target=lambda: results.append(tokens.consume(token))) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 1


def test_network_restrictions():
    for ip in ["192.168.1.2", "10.1.1.1", "172.16.2.3"]:
        assert local_address(ip)
    for ip in ["8.8.8.8", "0.0.0.0", "127.0.0.1", "224.0.0.1", "garbage"]:
        assert not local_address(ip)
    assert local_address("127.0.0.1", allow_loopback=True)
    limiter = RateLimiter(2)
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")


def test_capability_and_codec_failure():
    assert not probe_encoder("nonexistent_encoder")["available"]
    assert probe_encoder("libx264")["available"]


def test_invalid_configuration():
    with pytest.raises(ValueError):
        RecordingConfig(width=1921, height=1080)
    with pytest.raises(ValueError):
        RecordingConfig(mode="imaginary")
    with pytest.raises(ValueError):
        RecordingConfig(fps=144)
