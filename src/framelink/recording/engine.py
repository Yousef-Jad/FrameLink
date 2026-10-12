from dataclasses import replace
from pathlib import Path
import json
import shutil
import threading
import time
import uuid
import numpy as np
import psutil
from PIL import Image
from framelink.media.model import SessionClock, StateMachine, State, atomic_promote
from framelink.media.compositor import compose, compose_rgb, fit, as_rgb
from framelink.media.encode import MediaWriter, validate_media


class Recorder:
    def __init__(
        self, sources, library, output: Path, notify=lambda *args: None, on_saved=lambda ident: None
    ):
        self.sources, self.library, self.output = sources, library, Path(output)
        self.notify, self.on_saved = notify, on_saved
        self.machine = StateMachine()
        self.clock = None
        self.thread = None
        self.stop_event = threading.Event()
        self.stop_target_pts = None
        self.metrics = {}
        self.config = None
        self.error = ""
        self.performance_path = None
        self._performance_samples = {}
        self._source_baseline = {}
        self._last_source = {}
        self._last_output_pts = None
        self.process = psutil.Process()

    @property
    def state(self):
        return self.machine.state

    def start(self, config):
        self.reset()
        if self.state != State.IDLE:
            raise ValueError("A recording is already active")
        for name in {"screen", "phone"} if config.mode == "both" else {config.mode}:
            sample = getattr(self.sources, name).latest()
            if sample is None or (name == "phone" and time.monotonic() - sample.timestamp > 3):
                raise ValueError(f"Start {name} preview before recording")
        self.output.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.output).free < 512 * 1024 * 1024:
            raise ValueError("At least 512 MB of free disk space is required")
        self.config = config
        self.cleanup_eligible = not self.library.setting("keep_original", False)
        self.clock = SessionClock()
        self.error = ""
        self.stop_event.clear()
        self.stop_target_pts = None
        self.metrics = {"encoded": 0, "skipped": 0, "latency_ms": 0, "bytes": 0, "encoder": config.encoder}
        self._performance_samples = {
            name: []
            for name in (
                "capture_to_encoder_ms",
                "conversion_ms",
                "encode_ms",
                "mux_ms",
                "source_interval_ms",
                "output_interval_ms",
            )
        }
        self._source_baseline = {
            name: getattr(self.sources, name).metrics_snapshot()["received"] for name in ("screen", "phone")
        }
        self._drop_baseline = {name: getattr(self.sources, name).dropped for name in ("screen", "phone")}
        self.process.cpu_percent()
        self._last_source = {"screen": None, "phone": None}
        self._last_output_pts = None
        self.performance_path = None
        self.machine.transition(State.PREPARING)
        self.thread = threading.Thread(target=self._run, name="recording", daemon=False)
        self.thread.start()

    def pause(self):
        with self.machine.lock:
            if self.state != State.RECORDING:
                raise ValueError("Only an active recording can be paused")
            self.clock.pause()
            self.machine.transition(State.PAUSED)

    def resume(self):
        with self.machine.lock:
            if self.state != State.PAUSED:
                raise ValueError("Recording is not paused")
            self.clock.resume()
            self.machine.transition(State.RECORDING)

    def stop(self):
        with self.machine.lock:
            if self.state in {State.PREPARING, State.RECORDING, State.PAUSED}:
                self.machine.transition(State.STOPPING)
                if self.clock:
                    if self.config:
                        self.stop_target_pts = max(0, int(self.clock.elapsed() * self.config.fps))
                    self.clock.pause()
                self.stop_event.set()
                return True
            return False

    def reset(self):
        if self.state in {State.ERROR, State.COMPLETED} and (
            self.thread is None or not self.thread.is_alive()
        ):
            self.machine.transition(State.IDLE)

    def _run(self):
        cfg = self.config
        writers = []
        manifest = None
        try:
            session = uuid.uuid4().hex
            base = time.strftime("FrameLink-%Y%m%d-%H%M%S") + "-" + session[:6]
            cache = self.library.root / "recording-cache"
            cache.mkdir(parents=True, exist_ok=True)
            self.performance_path = cache / f"{base}.performance.json"
            primary = getattr(self.sources, "phone" if cfg.mode == "phone" else "screen").latest()
            native = (primary.rgb.shape[1] // 2 * 2, primary.rgb.shape[0] // 2 * 2)
            size = (cfg.width, cfg.height) if cfg.width else native
            if size[0] > native[0] or size[1] > native[1]:
                raise ValueError("Output dimensions exceed the source; upscaling is disabled")
            audio_enabled = cfg.phone_audio or cfg.microphone or cfg.system_audio
            tracks = [("screen" if cfg.mode == "both" and cfg.layout == "separate" else cfg.mode, size)]
            if cfg.mode == "both" and cfg.layout == "separate":
                phone = self.sources.phone.latest().rgb
                tracks.append(("phone", (phone.shape[1] // 2 * 2, phone.shape[0] // 2 * 2)))
            for name, dimensions in tracks:
                partial = self.output / f"{base}-{name}.partial.mp4"
                try:
                    writer = MediaWriter(
                        partial,
                        *dimensions,
                        cfg.fps,
                        cfg.encoder,
                        audio_enabled,
                        cfg.quality,
                        preset="ultrafast" if cfg.encoder == "libx264" else "veryfast",
                        fragmented=True,
                    )
                except Exception:
                    if cfg.encoder == "libx264":
                        raise
                    # Opening failed before any input was written; preserve it for inspection.
                    self.notify("warning", f"{cfg.encoder} could not initialize. Using software H.264.")
                    cfg = replace(cfg, encoder="libx264")
                    partial = self.output / f"{base}-{name}-software.partial.mp4"
                    writer = MediaWriter(
                        partial,
                        *dimensions,
                        cfg.fps,
                        cfg.encoder,
                        audio_enabled,
                        cfg.quality,
                        preset="ultrafast",
                        fragmented=True,
                    )
                    self.metrics["encoder"] = cfg.encoder
                writers.append((name, writer))
            manifest = cache / f"{base}.session.json"
            manifest.write_text(
                json.dumps(
                    {
                        "session": session,
                        "files": [str(w.path) for _, w in writers],
                        "started": time.time(),
                        "mode": cfg.mode,
                    }
                ),
                encoding="utf-8",
            )
            with self.machine.lock:
                self.clock = SessionClock()
                if self.stop_event.is_set():
                    self.clock.pause()
                else:
                    self.machine.transition(State.RECORDING)
            pts = 0
            audio_pts = 0
            thumbnail = cache / f"{base}.png"
            last_phone = self.sources.phone.latest()
            last_disk_check = 0.0
            window_started = time.monotonic()
            window_encoded = 0
            last_written_pts = -1
            primary_name = "phone" if cfg.mode == "phone" else "screen"
            first_sample = getattr(self.sources, primary_name).latest()
            primary_sequence = max(0, first_sample.sequence - 1) if first_sample else 0
            source_origin = None
            source_paused_total = 0.0
            pause_started = None
            was_paused = False
            while True:
                stopping = self.stop_event.is_set()
                if stopping:
                    break
                with self.machine.lock:
                    paused = self.state == State.PAUSED
                if paused:
                    if not was_paused:
                        pause_started = time.monotonic()
                        was_paused = True
                    self.stop_event.wait(0.03)
                    continue
                if was_paused:
                    source_paused_total += time.monotonic() - pause_started
                    pause_started = None
                    was_paused = False
                primary = getattr(self.sources, primary_name).next_after(primary_sequence)
                if primary is None:
                    self.stop_event.wait(0.002)
                    continue
                primary_sequence = primary.sequence
                if source_origin is None:
                    source_origin = primary.timestamp
                pts = round((primary.timestamp - source_origin - source_paused_total) * cfg.fps)
                if pts <= last_written_pts:
                    self.metrics["skipped"] += 1
                    continue
                now = time.monotonic()
                screen = primary if cfg.mode != "phone" else self.sources.screen.latest()
                phone = (
                    primary if cfg.mode == "phone" else self.sources.phone.latest(before=primary.timestamp)
                )
                phone = phone or self.sources.phone.latest()
                primary = phone if cfg.mode == "phone" else screen
                if primary is not None:
                    previous = self._last_source[cfg.mode if cfg.mode == "phone" else "screen"]
                    if previous is not None:
                        if primary.sequence == previous.sequence:
                            self.metrics["duplicated_frames"] = self.metrics.get("duplicated_frames", 0) + 1
                        elif primary.sequence > previous.sequence + 1:
                            self.metrics["source_frames_skipped"] = (
                                self.metrics.get("source_frames_skipped", 0)
                                + primary.sequence
                                - previous.sequence
                                - 1
                            )
                        self._performance_samples["source_interval_ms"].append(
                            (primary.timestamp - previous.timestamp) * 1000
                        )
                    self._last_source[cfg.mode if cfg.mode == "phone" else "screen"] = primary
                    self._performance_samples["capture_to_encoder_ms"].append(
                        (now - primary.timestamp) * 1000
                    )
                connected = phone is not None and now - phone.timestamp < 3
                if connected:
                    last_phone = phone
                if cfg.mode in {"phone", "both"} and not connected:
                    if cfg.disconnect_policy == "stop":
                        self.stop()
                        break
                    if cfg.disconnect_policy == "pause":
                        with self.machine.lock:
                            if self.stop_event.is_set():
                                break
                            self.pause()
                        self.notify("warning", "Phone disconnected. Recording paused; reconnect and resume.")
                        continue
                    phone = last_phone if cfg.disconnect_policy == "freeze" else None
                begin = time.perf_counter()
                conversion_begin = begin
                rgb_phone = phone.rgb if phone else None
                rgb_screen = screen.rgb if screen else None
                for name, writer in writers:
                    source = rgb_phone if name == "phone" else rgb_screen if name == "screen" else None
                    if source is not None and source.shape[:2] == (writer.height, writer.width):
                        # A native single source needs no Pillow conversion or canvas.
                        # Preview cropping is intentionally absent from this path.
                        frame_rgb = np.ascontiguousarray(
                            source[:, ::-1] if name == "phone" and cfg.mirror else source
                        )
                    elif cfg.layout == "separate" and cfg.mode == "both":
                        source = rgb_phone if name == "phone" else rgb_screen
                        image = (
                            fit(source, (writer.width, writer.height))
                            if source is not None
                            else compose(
                                None, None, (writer.width, writer.height), replace(cfg, mode="phone")
                            )
                        )
                        frame_rgb = as_rgb(image)
                    else:
                        frame_rgb = compose_rgb(rgb_screen, rgb_phone, size, cfg)
                    self._performance_samples["conversion_ms"].append(
                        (time.perf_counter() - conversion_begin) * 1000
                    )
                    before_writer = writer.telemetry_snapshot()
                    writer.write_video(frame_rgb, pts)
                    after_writer = writer.telemetry_snapshot()
                    self._performance_samples["encode_ms"].append(
                        after_writer["encode_ms"] - before_writer["encode_ms"]
                    )
                    self._performance_samples["mux_ms"].append(
                        after_writer["mux_ms"] - before_writer["mux_ms"]
                    )
                    if pts == 0 and name == writers[0][0]:
                        Image.fromarray(frame_rgb).resize(
                            (320, max(1, round(writer.height * 320 / writer.width)))
                        ).save(thumbnail)
                if audio_enabled:
                    wanted = round((pts + 1) * 48000 / cfg.fps)
                    # Keep audio continuous through dropped video frames. Pauses remove both timelines.
                    count = wanted - audio_pts
                    # Read adjacent intervals on the source clock. Sampling relative
                    # to each loop's wall time would duplicate or skip audio on jitter.
                    start = source_origin + source_paused_total + audio_pts / 48000
                    mixed = np.zeros((2, count), np.float32)
                    for key, enabled, gain in (
                        ("phone", cfg.phone_audio, cfg.phone_gain),
                        ("mic", cfg.microphone, cfg.mic_gain),
                        ("system", cfg.system_audio, cfg.system_gain),
                    ):
                        if enabled:
                            mixed += self.sources.audio[key].read(start, count) * gain
                    np.clip(mixed, -1, 1, out=mixed)
                    for _, writer in writers:
                        writer.write_audio(mixed, audio_pts)
                    audio_pts = wanted
                self.metrics["encoded"] += 1
                self.metrics["latency_ms"] = (time.perf_counter() - begin) * 1000
                window_encoded += 1
                last_written_pts = pts
                if self._last_output_pts is not None:
                    self._performance_samples["output_interval_ms"].append(
                        (pts - self._last_output_pts) * 1000 / cfg.fps
                    )
                self._last_output_pts = pts
                if now - window_started >= 4:
                    observed = window_encoded / (now - window_started)
                    self.metrics["measured_output_fps"] = observed
                    window_started = now
                    window_encoded = 0
                if now - last_disk_check > 1:
                    last_disk_check = now
                    self.metrics["bytes"] = sum(w.path.stat().st_size for _, w in writers)
                    self.metrics["cpu_percent"] = self.process.cpu_percent()
                    self.metrics["memory_bytes"] = self.process.memory_info().rss
                    self.metrics["peak_memory_bytes"] = max(
                        self.metrics.get("peak_memory_bytes", 0), self.metrics["memory_bytes"]
                    )
                    if shutil.disk_usage(self.output).free < 128 * 1024 * 1024:
                        self.notify("warning", "Recording stopped: less than 128 MB of disk space remains.")
                        self.stop()
            if self.state in {State.RECORDING, State.PAUSED}:
                self.machine.transition(State.STOPPING)
            self.machine.transition(State.FINALIZING)
            self.notify("status", "Finalizing and validating recorded streams…")
            # Preview continues during validation, but these frames are no
            # longer recording input and must not count as queue overflow.
            self.sources.screen.release_consumer()
            self.sources.phone.release_consumer()
            for _, writer in writers:
                writer.close()
            self._update_performance_metrics(writers, cfg)
            expected = (last_written_pts + 1) / cfg.fps
            if last_written_pts < 0:
                raise RuntimeError(
                    "Recording cancelled before the first frame; no completed recording was created"
                )
            saved = []
            for _, writer in writers:
                info = validate_media(writer.path, expected, audio_enabled)
                final = writer.path.with_name(writer.path.name.replace(".partial.mp4", ".mp4"))
                atomic_promote(writer.path, final)
                ident = self.library.add(
                    final, info, session=session, thumbnail=thumbnail,
                    cleanup_eligible=self.cleanup_eligible,
                )
                saved.append(ident)
            manifest.unlink(missing_ok=True)
            self.machine.transition(State.COMPLETED)
            self._write_performance_report()
            self.notify("saved", "Recording saved and validated")
            for ident in saved:
                try:
                    self.on_saved(ident)
                except Exception as exc:
                    self.notify(
                        "warning", f"Recording saved, but automatic compression could not be queued: {exc}"
                    )
        except Exception as exc:
            self.error = str(exc)
            with self.machine.lock:
                if self.state != State.ERROR:
                    self.machine.transition(State.ERROR)
            self.notify("error", f"Recording failed: {exc}. Existing media has been retained.")
        finally:
            self.sources.screen.release_consumer()
            self.sources.phone.release_consumer()
            for _, writer in writers:
                try:
                    writer.close()
                except Exception:
                    pass
            self._write_performance_report()

    @staticmethod
    def _summarize(values):
        values = sorted(float(value) for value in values if value is not None)
        if not values:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
        return {
            "count": len(values),
            "p50_ms": values[(len(values) - 1) // 2],
            "p95_ms": values[min(len(values) - 1, round((len(values) - 1) * 0.95))],
            "p99_ms": values[round((len(values) - 1) * 0.99)],
            "max_ms": values[-1],
        }

    def _update_performance_metrics(self, writers, cfg):
        source = {name: getattr(self.sources, name).metrics_snapshot() for name in ("screen", "phone")}
        self.metrics["source"] = source
        self.metrics["source_frames_received"] = {
            name: max(0, report["received"] - self._source_baseline.get(name, 0))
            for name, report in source.items()
        }
        self.metrics["source_frames_dropped"] = {
            name: max(0, report["dropped"] - self._drop_baseline.get(name, 0))
            for name, report in source.items()
        }
        self.metrics["source_capture_fps"] = {name: report["capture_fps"] for name, report in source.items()}
        self.metrics["stage_timings"] = {
            name: self._summarize(values) for name, values in self._performance_samples.items()
        }
        self.metrics["requested_fps"] = cfg.fps
        self.metrics["encoded_fps"] = self.metrics.get("encoded", 0) / max(
            0.001, self.clock.elapsed() if self.clock else 0.001
        )
        self.metrics["encoder_telemetry"] = [writer.telemetry_snapshot() for _, writer in writers]

    def _write_performance_report(self):
        if not self.performance_path:
            return
        try:
            self.performance_path.write_text(json.dumps(self.metrics, indent=2), encoding="utf-8")
        except Exception:
            pass


def recover_recordings(output, library):
    """Remux decodable packets to a new file, leaving interrupted originals untouched."""
    import av

    recovered, failures = [], []
    known = {r["path"] for r in library.recordings()}
    partials = [*Path(output).glob("*.partial.mp4"), *Path(output).glob("*.partial.mkv")]
    for partial in partials:
        if "-export-" in partial.name:
            continue  # Interrupted exports belong to the compression queue.
        target = partial.with_name(partial.name.rsplit(".partial.", 1)[0] + ".recovered.mp4")
        if str(target.resolve()) in known:
            continue
        temp = target.with_name(target.stem + ".recovering.mp4")
        try:
            if target.exists():
                info = validate_media(target)
            else:
                with av.open(str(partial)) as source, av.open(
                    str(temp), "w", format="mp4", options={"movflags": "+faststart"}
                ) as dest:
                    mapping = {s.index: dest.add_stream_from_template(s) for s in source.streams}
                    for packet in source.demux():
                        if packet.dts is not None:
                            packet.stream = mapping[packet.stream.index]
                            dest.mux(packet)
                info = validate_media(temp)
                atomic_promote(temp, target)
            recovered.append(library.add(target, info))
        except Exception as exc:
            failures.append(f"{partial.name}: {exc}")
    # A crash between file promotion and database commit must not orphan valid recordings.
    known = {r["path"] for r in library.recordings()}
    for pattern in ("FrameLink-*.mp4", "LumaCapture-*.mp4", "FrameLink-*.mkv", "LumaCapture-*.mkv"):
        for path in Path(output).glob(pattern):
            if (
                ".partial." in path.name
                or ".recovering." in path.name
                or "-export-" in path.name
                or str(path.resolve()) in known
            ):
                continue
            try:
                recovered.append(library.add(path, validate_media(path)))
                known.add(str(path.resolve()))
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
    return recovered, failures
