"""Capture screenshots from the real FrameLink window for visual review."""

import argparse
import os
from pathlib import Path
import tempfile
import time

import numpy as np

if not os.environ.get("QT_QPA_PLATFORM") and not os.environ.get("DISPLAY"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

from framelink.storage.library import Library
from framelink.media.model import State
from framelink.ui.window import MainWindow


PAGE_NAMES = ("studio", "devices", "library", "settings")
PAGE_INDEX = {name: index for index, name in enumerate(PAGE_NAMES)}


def seed_library(library, data_root):
    thumbnail = Path(data_root) / "ui-fixture-thumbnail.jpg"
    image = Image.new("RGB", (640, 360), "#20293A")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((58, 54, 582, 306), radius=18, outline="#AFC5FF", width=4)
    draw.ellipse((286, 128, 354, 196), fill="#FF453A")
    draw.text((78, 244), "FrameLink · local session", fill="#F2F4F8")
    image.save(thumbnail)
    media = Path(data_root) / "FrameLink-visual-review.mkv"
    media.touch()
    library.add(
        media,
        {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "measured_fps": 30.0,
            "duration": 82.4,
            "size": 184 * 1024 * 1024,
            "codec": "h264",
            "audio": True,
        },
        thumbnail=thumbnail,
    )


def capture_active_scene(window, app, output, mode):
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:, :, 0] = 24
    frame[:, :, 1] = 38
    frame[:, :, 2] = 62
    frame[72:288, 168:472, 2] = 210
    window.library.set_setting("auto_compress", False)
    window.output = window.data_root / "visual-recordings"
    window.output.mkdir(parents=True, exist_ok=True)
    window.recorder.output = window.output
    window.change_mode(mode)
    window.fps.setCurrentIndex(window.fps.findData(30))
    window.sources.screen.put(frame)
    if mode == "both":
        phone = frame.copy()
        phone[:, :, 0] = 215
        phone[:, :, 1] = 74
        window.sources.phone.put(phone)
    window.start_recording()
    deadline = time.monotonic() + 0.7
    while time.monotonic() < deadline:
        app.processEvents()
        window.tick()
        time.sleep(0.03)
    window.grab().save(str(output / f"studio-{mode}-active.png"))
    window.stop_recording()
    if window.recorder.thread:
        window.recorder.thread.join(10)
    if window.recorder.state != State.IDLE:
        window.recorder.reset()


def capture(output, width, height, active_scenes=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="framelink-ui-") as data:
        data_root = Path(data)
        library = Library(data_root / "library")
        seed_library(library, data_root)
        window = MainWindow(library, data_root, smoke=True)
        window.resize(width, height)
        window.show()
        app.processEvents()
        window.refresh_tables()
        app.processEvents()
        for name, index in PAGE_INDEX.items():
            window.navigate(index)
            app.processEvents()
            window.grab().save(str(output / f"{name}.png"))
            if name == "studio":
                window.toggle_inspector()
                app.processEvents()
                window.grab().save(str(output / "studio-inspector.png"))
                window.toggle_inspector()
        if active_scenes:
            window.navigate(0)
            app.processEvents()
            capture_active_scene(window, app, output, "screen")
            capture_active_scene(window, app, output, "both")
        window.close()
        window.compression.stop()
        window.compression.thread.join(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1240)
    parser.add_argument("--height", type=int, default=870)
    parser.add_argument("--active-scenes", action="store_true")
    args = parser.parse_args()
    capture(args.output, args.width, args.height, args.active_scenes)


if __name__ == "__main__":
    main()
