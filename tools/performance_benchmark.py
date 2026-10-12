"""Reproducible capture/recording performance benchmark.

The desktop source uses the application's real QScreenCapture backend. The
synthetic source is deliberately separate: it measures the recorder and
encoder without pretending to verify PipeWire or a physical phone.
"""

import argparse
import json
from pathlib import Path
import threading
import time

import numpy as np
import psutil

from framelink.media.buffers import Sources
from framelink.media.encode import inspect_media, probe_encoder
from framelink.media.model import RecordingConfig, State
from framelink.recording.engine import Recorder
from framelink.storage.library import Library


def wait_for_first_frame(pump_events, frame_ready, timeout=None, clock=time.monotonic, sleep=time.sleep):
    """Wait for an actual capture callback without charging approval time to recording."""
    started = clock()
    deadline = started + timeout if timeout and timeout > 0 else None
    while True:
        pump_events()
        now = clock()
        if frame_ready():
            return {"wait_seconds": now - started, "first_frame_at": now}
        if deadline is not None and now >= deadline:
            raise TimeoutError(f"No video frame arrived within {timeout:g} seconds")
        sleep(min(0.01, deadline - now) if deadline is not None else 0.01)


def wait_for_capture_stop(pump_events, is_active, timeout=30, clock=time.monotonic, sleep=time.sleep):
    """Pump Qt until the portal session reports inactive, or fail closed."""
    deadline = clock() + timeout
    while is_active():
        if clock() >= deadline:
            return False
        pump_events()
        sleep(min(0.01, max(0.0, deadline - clock())))
    return True


class SyntheticProducer:
    def __init__(self, sources, mode, width, height, fps):
        self.sources = sources
        self.mode = mode
        self.width = width
        self.height = height
        self.fps = fps
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="benchmark-source", daemon=True)
        self.frames = 0

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(5)

    def _run(self):
        interval = 1 / self.fps
        next_frame = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now < next_frame:
                self.stop_event.wait(next_frame - now)
                continue
            # A moving, deterministic RGB source; every sequence has distinct content.
            frame = np.empty((self.height, self.width, 3), dtype=np.uint8)
            frame[:, :, 0] = self.frames % 256
            frame[:, :, 1] = (self.frames * 3) % 256
            frame[:, :, 2] = (self.frames * 7) % 256
            if self.mode in {"screen", "both"}:
                self.sources.screen.put(frame, now)
            if self.mode in {"phone", "both"}:
                self.sources.phone.put(frame, now)
            self.frames += 1
            next_frame += interval
            if next_frame < now - interval:
                next_frame = now + interval


def run_benchmark(args):
    args.output.mkdir(parents=True, exist_ok=True)
    sources = Sources()
    library = Library(args.output / "library")
    recorder = Recorder(sources, library, args.output / "recordings")
    producer = None
    capture = None
    app = None
    preview = None
    process = psutil.Process()
    process.cpu_percent()
    resources = []
    error = ""
    capture_errors = []
    first_frame = None
    recording_elapsed = 0.0
    try:
        if args.source == "synthetic":
            producer = SyntheticProducer(sources, args.mode, args.width, args.height, args.fps)
            producer.start()
        else:
            from PySide6.QtWidgets import QApplication, QLabel
            from framelink.capture.desktop import DesktopCapture

            app = QApplication.instance() or QApplication([])
            capture = DesktopCapture(sources.screen)
            capture.error.connect(capture_errors.append)
            capture.start(app.primaryScreen(), fps=args.fps)
            if args.preview:
                preview = QLabel()
                preview.resize(640, 360)
                preview.show()

        def frame_ready():
            if capture_errors:
                raise RuntimeError(f"Capture backend error: {capture_errors[-1]}")
            return (
                capture.first_frame_at is not None
                if capture is not None
                else (sources.screen.latest() is not None or sources.phone.latest() is not None)
            )

        first_frame = wait_for_first_frame(
            lambda: app.processEvents() if args.source == "desktop" and app is not None else None,
            frame_ready,
            timeout=args.permission_timeout or None,
        )

        recorder.start(
            RecordingConfig(
                mode=args.mode,
                fps=args.fps,
                encoder=args.encoder,
                layout=args.layout,
            )
        )
        while recorder.state == State.PREPARING:
            if app is not None:
                app.processEvents()
            time.sleep(0.001)
        if recorder.state != State.RECORDING:
            raise RuntimeError(recorder.error or "Recorder did not initialize")
        recording_start = recorder.clock.started
        last_resource_sample = [recording_start - 1]

        def measure():
            now = time.monotonic()
            if args.source == "desktop" and app is not None:
                if preview is not None:
                    sample = sources.screen.latest()
                    if sample is not None:
                        from PySide6.QtGui import QImage, QPixmap

                        image = QImage(
                            sample.rgb.data,
                            sample.rgb.shape[1],
                            sample.rgb.shape[0],
                            sample.rgb.shape[1] * 3,
                            QImage.Format.Format_RGB888,
                        ).copy()
                        preview.setPixmap(QPixmap.fromImage(image).scaled(preview.size()))
            if now - last_resource_sample[0] >= 1:
                last_resource_sample[0] = now
                resources.append(
                    {
                        "time": now - recording_start,
                        "cpu_percent": process.cpu_percent(),
                        "memory_bytes": process.memory_info().rss,
                    }
                )

        if app is not None:
            from PySide6.QtCore import QEventLoop, QTimer, Qt

            loop = QEventLoop()
            sample_timer = QTimer()
            sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
            sample_timer.timeout.connect(measure)
            sample_timer.start(33)
            QTimer.singleShot(round(args.duration * 1000), loop.quit)
            loop.exec()
            sample_timer.stop()
        else:
            while time.monotonic() - recording_start < args.duration:
                measure()
                time.sleep(0.02)
        recording_elapsed = time.monotonic() - recording_start
        recorder.stop()
        recorder.thread.join(max(30, args.duration))
        if recorder.state != State.COMPLETED:
            error = recorder.error or f"Recorder ended in {recorder.state.value}"
        media = [item for item in library.recordings() if not item["original"]]
        report = {
            "source": args.source,
            "mode": args.mode,
            "layout": args.layout,
            "requested_fps": args.fps,
            "encoder": args.encoder,
            "duration_requested": args.duration,
            "producer_frames": producer.frames if producer else None,
            "resources": resources,
            "recorder_metrics": recorder.metrics,
            "capture_metrics": capture.metrics_snapshot() if capture else None,
            "capture_errors": capture_errors,
            "first_frame": first_frame,
            "recording_elapsed": recording_elapsed,
            "recordings": [],
            "encoder_probes": [
                probe_encoder(name, args.width, args.height)
                for name in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
            ],
            "error": error,
        }
        for item in media:
            path = Path(item["path"])
            report["recordings"].append({"path": str(path), "media": inspect_media(path, full=True)})
    except Exception as exc:
        report = {
            "source": args.source,
            "mode": args.mode,
            "error": str(exc),
            "resources": resources,
            "capture_metrics": capture.metrics_snapshot() if capture else None,
            "capture_errors": capture_errors,
            "first_frame": first_frame,
        }
    finally:
        if recorder.thread and recorder.thread.is_alive():
            recorder.stop()
            recorder.thread.join(max(30, args.duration))
        if producer:
            producer.stop()
        if capture:
            capture.stop()
            if app is not None:
                if not wait_for_capture_stop(
                    app.processEvents,
                    lambda: capture.active,
                    timeout=args.teardown_timeout,
                ):
                    capture_errors.append(
                        f"Capture session did not close within {args.teardown_timeout:g} seconds"
                    )
        if preview:
            preview.close()
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if not report.get("error") else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("synthetic", "desktop"), default="synthetic")
    parser.add_argument("--mode", choices=("screen", "phone", "both"), default="screen")
    parser.add_argument("--layout", choices=("pip", "separate"), default="pip")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--duration", type=float, default=5)
    parser.add_argument("--encoder", default="libx264")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument(
        "--permission-timeout",
        type=float,
        default=0,
        help="Seconds allowed for portal approval; 0 waits indefinitely",
    )
    parser.add_argument(
        "--teardown-timeout",
        type=float,
        default=30,
        help="Seconds allowed for the portal capture session to close",
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/performance-benchmark"))
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    args.report = args.report or args.output / "result.json"
    return run_benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
