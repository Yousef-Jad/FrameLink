"""Run sequential native measurements with one user-approved portal session.

Qt's event loop stays running during measurement and encoder finalization.
No permission waiting is included in stage clocks; no restored approval is assumed.
"""

import argparse
import json
from pathlib import Path
import time

import psutil
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from framelink.capture.desktop import DesktopCapture
from framelink.compression.queue import CompressionQueue
from framelink.media.buffers import Sources
from framelink.media.model import RecordingConfig, State
from framelink.media.performance import GpuUtilization
from framelink.recording.engine import Recorder
from framelink.storage.library import Library
from framelink.ui.preview import PreviewWidget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--stages", default="record30,record60,preview30,preview-record30")
    parser.add_argument("--native-fps", type=int, default=60)
    parser.add_argument("--compress", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    sources = Sources()
    library = Library(args.output / "library")
    recorder = Recorder(sources, library, args.output / "recordings")
    compression = CompressionQueue(library) if args.compress else None
    if compression:
        compression.capture_active = lambda: recorder.state.active
        recorder.on_saved = compression.enqueue
    phone = None
    if any(name.startswith(("pip", "separate")) for name in args.stages.split(",")):
        from performance_benchmark import SyntheticProducer

        phone = SyntheticProducer(sources, "phone", 640, 360, 30)
        phone.start()
    capture = DesktopCapture(sources.screen)
    preview = PreviewWidget()
    preview.setWindowTitle("FrameLink measured preview")
    preview.resize(640, 360)
    process = psutil.Process()
    gpu = GpuUtilization()
    errors = []
    capture.error.connect(errors.append)
    requested = time.monotonic()
    stages = iter(args.stages.split(","))
    report = {"backend": "native Qt QScreenCapture", "stages": [], "errors": errors}
    report["phone_source"] = "synthetic 640x360 moving fixture" if phone else "none"
    report["native_requested_fps"] = args.native_fps
    state = {"phase": "permission", "stage": None, "preview_sequence": 0}

    def persist():
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")

    def announce(text):
        print(text, flush=True)

    def finish():
        ticker.stop()
        sampler.stop()
        preview_timer.stop()
        capture.stop()
        preview.close()
        gpu.close()
        if phone:
            phone.stop()
        if compression:
            compression.stop()
        persist()
        # Process the native close request before releasing the capture objects.
        QTimer.singleShot(1000, app.quit)

    def next_stage():
        name = next(stages, None)
        if name is None:
            announce("All measurements finalized")
            finish()
            return
        fps = 60 if name.endswith("60") else 30
        visible = name.startswith("preview")
        record = name.startswith(("record", "preview-record", "pip", "separate"))
        capture.set_rate(fps)
        capture._reset_metrics()
        capture._metrics_started = time.monotonic()
        sources.screen.clear()
        preview.setVisible(visible)
        state.update(phase="warmup", name=name, fps=fps, record=record, preview=visible)
        announce("Waiting for fresh capture frame: " + name)

    def begin():
        state["stage"] = {
            "name": state["name"],
            "requested_fps": state["fps"],
            "duration_requested": args.duration,
            "resources": [],
            "preview_frames": 0,
        }
        state["preview_sequence"] = 0
        state["started"] = time.monotonic()
        process.cpu_percent()
        if state["record"]:
            recorder.start(
                RecordingConfig(
                    mode="both" if state["name"].startswith(("pip", "separate")) else "screen",
                    layout="separate" if state["name"].startswith("separate") else "pip",
                    fps=state["fps"],
                )
            )
            state["phase"] = "preparing"
        else:
            state["phase"] = "measuring"
        announce("Measurement started: " + state["name"])

    def tick():
        try:
            if errors:
                raise RuntimeError(errors[-1])
            phase = state["phase"]
            if phase == "permission":
                if sources.screen.latest() is not None:
                    report["permission_wait_seconds"] = time.monotonic() - requested
                    next_stage()
            elif phase == "warmup":
                if sources.screen.latest() is not None:
                    begin()
            elif phase == "preparing":
                if recorder.state == State.ERROR:
                    raise RuntimeError(recorder.error)
                if recorder.state == State.RECORDING:
                    state["started"] = recorder.clock.started
                    state["phase"] = "measuring"
            elif phase == "measuring":
                if time.monotonic() - state["started"] >= args.duration:
                    stage = state["stage"]
                    stage["elapsed_seconds"] = time.monotonic() - state["started"]
                    stage["capture"] = capture.metrics_snapshot()
                    if state["record"]:
                        recorder.stop()
                    state["phase"] = "finalizing"
                    announce("Finalizing: " + state["name"])
            elif phase == "finalizing":
                if state["record"] and recorder.thread.is_alive():
                    return
                stage = state["stage"]
                if state["record"]:
                    if recorder.state != State.COMPLETED:
                        raise RuntimeError(recorder.error or recorder.state.value)
                    stage["recorder"] = recorder.metrics.copy()
                    stage["recording"] = next(row for row in library.recordings() if not row["original"])
                    stage["recordings"] = [
                        row
                        for row in library.recordings()
                        if not row["original"] and row["session"] == stage["recording"]["session"]
                    ]
                    if compression:
                        jobs = library.jobs()
                        if any(job["status"] not in {"complete", "failed", "cancelled"} for job in jobs):
                            return
                        stage["compression_jobs"] = jobs
                        if any(job["status"] != "complete" for job in jobs):
                            raise RuntimeError("Native recording compression did not complete")
                report["stages"].append(stage)
                persist()
                next_stage()
        except Exception as exc:
            errors.append(str(exc))
            announce("Benchmark failed: " + str(exc))
            recorder.stop()
            if recorder.thread and recorder.thread.is_alive():
                state["phase"] = "failed"
                return
            finish()

    def sample():
        if state["phase"] == "failed":
            if not recorder.thread or not recorder.thread.is_alive():
                finish()
        if state["phase"] != "measuring":
            return
        state["stage"]["resources"].append(
            {
                "time": time.monotonic() - state["started"],
                "cpu_percent": process.cpu_percent(),
                "rss_bytes": process.memory_info().rss,
                "gpu_percent": gpu.read(),
            }
        )

    def paint_preview():
        if state["phase"] != "measuring" or not state.get("preview"):
            return
        sample = sources.screen.latest()
        if sample is None or sample.sequence == state["preview_sequence"]:
            return
        state["preview_sequence"] = sample.sequence
        rgb = sample.rgb
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888)
        preview.set_frame(image)
        state["stage"]["preview_frames"] += 1

    ticker = QTimer()
    ticker.setTimerType(Qt.TimerType.PreciseTimer)
    ticker.timeout.connect(tick)
    ticker.start(10)
    sampler = QTimer()
    sampler.timeout.connect(sample)
    sampler.start(1000)
    preview_timer = QTimer()
    preview_timer.setTimerType(Qt.TimerType.PreciseTimer)
    preview_timer.timeout.connect(paint_preview)
    preview_timer.start(33)
    # Qt applies maximumFrameRate only when starting the native stream. Receive
    # at display rate for this suite; application admission changes per stage.
    capture.start(app.primaryScreen(), fps=min(args.native_fps, round(app.primaryScreen().refreshRate())))
    announce("Waiting for portal permission and first valid frame; measurement clock has not started")
    app.exec()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
