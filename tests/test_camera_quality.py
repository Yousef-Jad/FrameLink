from pathlib import Path
import shutil
import subprocess

import pytest


def test_browser_bitrate_policy_and_diagnostics():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser quality policy regression tests")
    subprocess.run([node, "tests/camera_quality.cjs"], cwd=Path(__file__).resolve().parents[1], check=True)


def test_phone_recording_controls():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for phone recording controls")
    subprocess.run([node, "tests/phone_recording.cjs"], cwd=Path(__file__).resolve().parents[1], check=True)
