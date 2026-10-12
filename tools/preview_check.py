"""Render the actual Studio preview with clearly identified orientation fixtures."""

from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication
from framelink.storage.library import Library
from framelink.ui.window import MainWindow


def main():
    output = Path("artifacts/critical-fixes/preview")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        window = MainWindow(Library(root / "db"), root / "data", smoke=True)
        window.mode = "phone"
        window.mode_selector.blockSignals(True)
        window.mode_selector.set_current_value("phone")
        window.mode_selector.blockSignals(False)
        window.start_phone_server_auto = lambda: None
        window.show()
        for name, size in [("landscape", (1920, 1080)), ("portrait", (1080, 1920))]:
            frame = Image.new("RGB", size, "#274650")
            draw = ImageDraw.Draw(frame)
            for x in range(0, size[0], 120):
                draw.line((x, 0, x, size[1]), fill="#40626c", width=3)
            for y in range(0, size[1], 120):
                draw.line((0, y, size[0], y), fill="#40626c", width=3)
            cx, cy = size[0] // 2, size[1] // 2
            draw.ellipse((cx - 220, cy - 220, cx + 220, cy + 220), fill="#b0d6a2")
            draw.text((cx - 140, cy), f"{name.upper()} TEST FIXTURE", fill="#172a21", font_size=22)
            window.sources.phone.put(np.asarray(frame).copy(), time.monotonic())
            window.tick()
            app.processEvents()
            window.grab().save(str(output / f"{name}-fill.png"))
            window.preview.grab().save(str(output / f"{name}-canvas.png"))
        window.compression.stop()
        window.compression.thread.join(5)
        window.close()
        app.processEvents()
    print(output.resolve())


if __name__ == "__main__":
    main()
