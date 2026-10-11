from dataclasses import replace
from pathlib import Path
import json
import shutil
import threading
import time
import uuid
import numpy as np
from lumacapture.media.model import SessionClock, StateMachine, State, atomic_promote
from lumacapture.media.compositor import compose, fit, as_rgb
from lumacapture.media.encode import MediaWriter, validate_media


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
        self.metrics = {}
        self.config = None
        self.error = ""

    @property
    def state(self):
        return self.machine.state

    def start(self, config):
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
        self.clock = SessionClock()
        self.error = ""
        self.stop_event.clear()
        self.metrics = {"encoded": 0, "skipped": 0, "latency_ms": 0, "bytes": 0, "encoder": config.encoder}
        self.machine.transition(State.RECORDING)
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
            if self.state in {State.RECORDING, State.PAUSED}:
                self.machine.transition(State.STOPPING)
                self.stop_event.set()

    def reset(self):
        if self.state == State.ERROR and (self.thread is None or not self.thread.is_alive()):
            self.machine.transition(State.IDLE)

    def _run(self):
        cfg = self.config
        writers = []
        manifest = None
        try:
            session = uuid.uuid4().hex
            base = time.strftime("LumaCapture-%Y%m%d-%H%M%S") + "-" + session[:6]
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
                partial = self.output / f"{base}-{name}.partial.mkv"
                try:
                    writer = MediaWriter(
                        partial, *dimensions, cfg.fps, cfg.encoder, audio_enabled, cfg.quality
                    )
                except Exception:
                    if cfg.encoder == "libx264":
                        raise
                    # Opening failed before any input was written; preserve it for inspection.
                    self.notify("warning", f"{cfg.encoder} could not initialize. Using software H.264.")
                    cfg = replace(cfg, encoder="libx264")
                    partial = self.output / f"{base}-{name}-software.partial.mkv"
                    writer = MediaWriter(
                        partial, *dimensions, cfg.fps, cfg.encoder, audio_enabled, cfg.quality
                    )
                    self.metrics["encoder"] = cfg.encoder
                writers.append((name, writer))
            manifest = self.output / f"{base}.session.json"
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
                if self.state == State.PAUSED:
                    self.clock.pause()
            pts = 0
            audio_pts = 0
            thumbnail = self.output / f"{base}.jpg"
            last_phone = self.sources.phone.latest()
            last_disk_check = 0.0
            stride = 1
            window_started = time.monotonic()
            window_encoded = 0
            last_written_pts = -1
            # A common playout delay absorbs ordinary local Wi-Fi jitter for both video and audio.
            delay = 0.12
            while not self.stop_event.is_set():
                with self.machine.lock:
                    paused = self.state == State.PAUSED
                    elapsed = self.clock.elapsed()
                    timeline_origin = self.clock.started + self.clock.paused_total
                if paused:
                    self.stop_event.wait(0.03)
                    continue
                target = int(elapsed * cfg.fps)
                if target < pts:
                    self.stop_event.wait(min(0.02, (pts / cfg.fps) - elapsed))
                    continue
                if target > pts + 1:
                    self.metrics["skipped"] += target - pts
                    pts = target
                now = time.monotonic()
                sample_time = timeline_origin + pts / cfg.fps - delay
                screen = self.sources.screen.latest(before=sample_time) or self.sources.screen.latest()
                phone = self.sources.phone.latest(before=sample_time) or self.sources.phone.latest()
                connected = phone is not None and now - phone.timestamp < 3
                if connected:
                    last_phone = phone
                if cfg.mode in {"phone", "both"} and not connected:
                    if cfg.disconnect_policy == "stop":
                        self.stop()
                        break
                    if cfg.disconnect_policy == "pause":
                        self.pause()
                        self.notify("warning", "Phone disconnected. Recording paused; reconnect and resume.")
                        continue
                    phone = last_phone if cfg.disconnect_policy == "freeze" else None
                begin = time.perf_counter()
                rgb_phone = phone.rgb if phone else None
                rgb_screen = screen.rgb if screen else None
                for name, writer in writers:
                    if cfg.layout == "separate" and cfg.mode == "both":
                        source = rgb_phone if name == "phone" else rgb_screen
                        image = (
                            fit(source, (writer.width, writer.height))
                            if source is not None
                            else compose(
                                None, None, (writer.width, writer.height), replace(cfg, mode="phone")
                            )
                        )
                    else:
                        image = compose(rgb_screen, rgb_phone, size, cfg)
                    writer.write_video(as_rgb(image), pts)
                    if pts == 0 and name == writers[0][0]:
                        image.copy().resize((320, max(1, round(image.height * 320 / image.width)))).save(
                            thumbnail
                        )
                if audio_enabled:
                    wanted = round((pts + 1) * 48000 / cfg.fps)
                    # Keep audio continuous through dropped video frames. Pauses remove both timelines.
                    count = wanted - audio_pts
                    # Read adjacent intervals on the source clock. Sampling relative
                    # to each loop's wall time would duplicate or skip audio on jitter.
                    start = timeline_origin + audio_pts / 48000 - delay
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
                if now - window_started >= 4:
                    observed = window_encoded / (now - window_started)
                    requested = cfg.fps / stride
                    # Reduce cadence, preserving timestamps and native dimensions.
                    # Never report intentional adaptation as successful full-rate encoding.
                    if observed < requested * 0.8 and requested > 15:
                        stride = min(stride * 2, cfg.fps // 15)
                        self.metrics["adapted_fps"] = cfg.fps / stride
                        self.notify(
                            "warning",
                            f"Encoding cannot sustain the requested rate. Cadence reduced to {cfg.fps / stride:g} FPS; resolution and recorded time are preserved.",
                        )
                    window_started = now
                    window_encoded = 0
                pts += stride
                if now - last_disk_check > 1:
                    last_disk_check = now
                    self.metrics["bytes"] = sum(w.path.stat().st_size for _, w in writers)
                    if shutil.disk_usage(self.output).free < 128 * 1024 * 1024:
                        self.notify("warning", "Recording stopped: less than 128 MB of disk space remains.")
                        self.stop()
            if self.state in {State.RECORDING, State.PAUSED}:
                self.machine.transition(State.STOPPING)
            self.notify("status", "Finalizing and validating recorded streams…")
            for _, writer in writers:
                writer.close()
            expected = (last_written_pts + 1) / cfg.fps
            for _, writer in writers:
                info = validate_media(writer.path, expected, audio_enabled)
                final = writer.path.with_name(writer.path.name.replace(".partial.mkv", ".mkv"))
                atomic_promote(writer.path, final)
                ident = self.library.add(final, info, session=session, thumbnail=thumbnail)
                self.on_saved(ident)
            manifest.unlink(missing_ok=True)
            self.machine.transition(State.IDLE)
            self.notify("saved", "Recording saved and validated")
        except Exception as exc:
            self.error = str(exc)
            with self.machine.lock:
                if self.state != State.ERROR:
                    self.machine.transition(State.ERROR)
            self.notify("error", f"Recording failed: {exc}. Existing media has been retained.")
        finally:
            for _, writer in writers:
                try:
                    writer.close()
                except Exception:
                    pass


def recover_recordings(output, library):
    """Remux decodable packets to a new file, leaving interrupted originals untouched."""
    import av

    recovered, failures = [], []
    known = {r["path"] for r in library.recordings()}
    for partial in Path(output).glob("*.partial.mkv"):
        target = partial.with_name(partial.name.replace(".partial.mkv", ".recovered.mkv"))
        if str(target.resolve()) in known:
            continue
        temp = target.with_name(target.stem + ".recovering.mkv")
        try:
            if target.exists():
                info = validate_media(target)
            else:
                with av.open(str(partial)) as source, av.open(str(temp), "w", format="matroska") as dest:
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
    for path in Path(output).glob("LumaCapture-*.mkv"):
        if ".partial." in path.name or ".recovering." in path.name or str(path.resolve()) in known:
            continue
        try:
            recovered.append(library.add(path, validate_media(path)))
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")
    return recovered, failures
