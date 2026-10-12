import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description="FrameLink local recording studio")
    parser.add_argument(
        "--smoke-test", action="store_true", help="Create the actual UI, verify media runtime, then exit"
    )
    parser.add_argument("--screenshot", type=Path, help="Save an image of the actual application window")
    parser.add_argument("--data-dir", type=Path, help="Override application data directory")
    parser.add_argument(
        "--capture-test", type=Path, help="Capture a selected desktop for five seconds, validate, then exit"
    )
    args = parser.parse_args()
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    if sys.platform.startswith("linux"):
        # Qt can select a non-portal fallback on Wayland. Require its native
        # PipeWire path rather than attempting to grab a compositor window.
        wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        os.environ["QT_SCREEN_CAPTURE_BACKEND"] = "pipewire" if wayland else "x11"
    from PySide6.QtCore import QStandardPaths, QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from framelink.storage.library import Library
    from framelink.ui.window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("FrameLink")
    app.setOrganizationName("FrameLink")
    data_root = args.data_dir or Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )
    data_root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_root / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.warning(
            None,
            "FrameLink is already open",
            "Close the existing FrameLink instance before starting another.",
        )
        return 1
    window = MainWindow(Library(data_root), data_root, smoke=args.smoke_test)
    window.show()
    exit_code = [0]
    if args.screenshot:

        def screenshot():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))

        QTimer.singleShot(1200, screenshot)
    if args.smoke_test:

        def smoke():
            from framelink.media.encode import MediaWriter, validate_media
            import numpy as np

            try:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "runtime.mp4"
                    writer = MediaWriter(path, 320, 180, 30, fragmented=True)
                    for i in range(30):
                        writer.write_video(np.full((180, 320, 3), i * 5, np.uint8), i)
                    writer.close()
                    result = validate_media(path, 1, False)
                    print(json.dumps({"ui": "created", "media": result}), flush=True)
            except Exception as exc:
                print(str(exc), file=sys.stderr, flush=True)
                exit_code[0] = 1
            window.close()

        QTimer.singleShot(1600, smoke)
    if args.capture_test:
        args.capture_test.mkdir(parents=True, exist_ok=True)
        window.output = args.capture_test
        window.recorder.output = args.capture_test
        window.library.set_setting("auto_compress", False)
        if not window.desktop.active:
            window.toggle_preview()
        started = [False]
        deadline = __import__("time").monotonic() + 90
        test_timer = QTimer(window)

        def capture_test():
            from framelink.media.model import State

            now = __import__("time").monotonic()
            if not started[0] and window.sources.screen.latest():
                window.start_recording()
                started[0] = True
            if (
                started[0]
                and window.recorder.state == State.RECORDING
                and window.recorder.clock.elapsed() >= 5
            ):
                window.stop_recording()
            if (
                started[0]
                and not window.benchmark_running
                and window.recorder.clock is not None
                and window.recorder.state in {State.COMPLETED, State.ERROR}
            ):
                test_timer.stop()
                print(
                    json.dumps(
                        {
                            "state": window.recorder.state.value,
                            "error": window.recorder.error,
                            "recordings": window.library.recordings(),
                        }
                    ),
                    flush=True,
                )
                if window.recorder.state == State.ERROR:
                    exit_code[0] = 1
                window.close()
            elif now > deadline:
                test_timer.stop()
                exit_code[0] = 1
                print(
                    "Capture test timed out waiting for portal permission or media",
                    file=sys.stderr,
                    flush=True,
                )
                window.close()

        test_timer.timeout.connect(capture_test)
        test_timer.start(200)
    app.exec()
    lock.unlock()
    return exit_code[0]


if __name__ == "__main__":
    raise SystemExit(main())
