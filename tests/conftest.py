import time
import pytest
import numpy as np
from framelink.media.encode import MediaWriter


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "source.mkv"
    writer = MediaWriter(path, 320, 180, 30, audio=True)
    for i in range(45):
        rgb = np.zeros((180, 320, 3), np.uint8)
        rgb[:, :, 1] = 50
        rgb[30:70, (i * 5) % 270 : (i * 5) % 270 + 40, 0] = 240
        writer.write_video(rgb, i)
        samples = (
            np.sin(np.arange(i * 1600, (i + 1) * 1600) * 2 * np.pi * 440 / 48000).astype(np.float32) * 0.1
        )
        writer.write_audio(np.vstack([samples, samples]), i * 1600)
    writer.close()
    return path


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for condition")
