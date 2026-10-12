from pathlib import Path
import io
import json
import shutil
import sys
import threading
import time
import numpy as np
import psutil
import qrcode
from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QUrl, QSize
from PySide6.QtGui import QPixmap, QImage, QDesktopServices, QIcon, QPainter, QColor, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QStackedWidget,
    QScrollArea,
    QMessageBox,
    QFileDialog,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
)
from framelink.capture.desktop import DesktopCapture, backend_name
from framelink.capture.audio import AudioCapture
from framelink.capture.loopback import WasapiLoopback
from framelink.compression.queue import CompressionQueue, CompressionSettings
from framelink.media.buffers import Sources
from framelink.media.model import RecordingConfig, State
from framelink.media.compositor import compose
from framelink.media.encode import probe_encoder, RECORD_ENCODERS
from framelink.networking.interfaces import interfaces, has_usable_network, open_system_wifi_settings
from framelink.networking.server import PhoneServer
from framelink.recording.engine import Recorder, recover_recordings
from framelink.security.certificates import Certificates
from framelink.ui.theme import stylesheet
from framelink.ui.platform_effects import apply_window_effects
from framelink.media.performance import ResourceMonitor, stable_frame_rate
from framelink.storage.paths import default_recording_directory
from framelink.ui.components import (
    FloatingTransport,
    IconButton,
    IconRailButton,
    MaterialSurface,
    SegmentedControl,
)
from framelink.ui.preview import PreviewWidget
from framelink.ui.library_view import LibraryView


class Events(QObject):
    message = Signal(str, str)
    tls_audit = Signal(str)
    encoders = Signal(object)
    benchmark = Signal(object)
    recording_command = Signal(object, object, object)


class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 225)
        self.image = QImage()
        self.caption = "Your studio is ready"
        self.detail = "Start screen preview or connect your phone"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#0b0e14"))
        p.setPen(QColor("#2b3443"))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 15, 15)
        if not self.image.isNull():
            size = self.image.size().scaled(self.size() - QSize(12, 12), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width() - size.width()) // 2, (self.height() - size.height()) // 2
            p.drawImage(
                x,
                y,
                self.image.scaled(
                    size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                ),
            )
        else:
            p.setPen(QColor("#dce4f3"))
            font = p.font()
            font.setPointSize(18)
            p.setFont(font)
            p.drawText(self.rect().adjusted(10, -20, -10, -20), Qt.AlignmentFlag.AlignCenter, self.caption)
            p.setPen(QColor("#8c9ab0"))
            font.setPointSize(10)
            p.setFont(font)
            p.drawText(self.rect().adjusted(10, 40, -10, 40), Qt.AlignmentFlag.AlignCenter, self.detail)
        p.end()


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback=None, name=None):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    if callback:
        widget.clicked.connect(callback)
    return widget


def combo(items):
    widget = QComboBox()
    for text, data in items:
        widget.addItem(text, data)
    return widget


def spin(minimum, maximum, value):
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(14)
    return widget, layout


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def filesize(value):
    return f"{value / 1024**3:.2f} GB" if value >= 1024**3 else f"{value / 1024**2:.1f} MB"


class MainWindow(QMainWindow):
    def __init__(self, library, data_root, smoke=False):
        super().__init__()
        self.library, self.data_root = library, Path(data_root)
        self.setWindowTitle("FrameLink")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "framelink.svg")))
        self.resize(1240, 870)
        self.setMinimumSize(1060, 740)
        self.events = Events()
        self.events.message.connect(self.on_message)
        self.events.encoders.connect(self.on_encoders)
        self.events.benchmark.connect(self.on_benchmark)
        self.events.recording_command.connect(self.handle_remote_recording)
        self.sources = Sources()
        self.output = Path(self.library.setting("output", str(default_recording_directory())))
        self.output.mkdir(parents=True, exist_ok=True)
        self.compression = CompressionQueue(library, self.events.message.emit)
        self.recorder = Recorder(self.sources, library, self.output, self.events.message.emit, self.saved)
        self.compression.capture_active = lambda: self.recorder.state.active
        self.desktop = DesktopCapture(self.sources.screen)
        self.desktop.error.connect(lambda text: self.on_message("error", f"Screen capture: {text}"))
        self.desktop.changed.connect(lambda text: self.backend_label.setText(text))
        self.microphone = AudioCapture(self.sources.audio["mic"])
        self.system_audio = AudioCapture(self.sources.audio["system"])
        self.loopback = WasapiLoopback(
            self.sources.audio["system"], lambda text: self.events.message.emit("error", text)
        )
        self.microphone.error.connect(lambda text: self.on_message("error", text))
        self.system_audio.error.connect(lambda text: self.on_message("error", text))
        self.server = None
        self.server_starting = False
        self.certificates = None
        self.smoke = smoke
        self.mode = "screen"
        self.closing = False
        self.shutdown_started = False
        self.last_table_signature = None
        self.last_network_check = 0
        self.available_encoders = []
        # Auto mode starts conservatively; the benchmark can raise it after
        # measuring the actual source/encoder path.
        self.measured_fps = 30
        self.benchmark_running = False
        self.start_after_benchmark = False
        self.last_state = None
        self.recording_revision = 0
        self._snapshot_state = None
        self.phone_recording_snapshot = {
            "state": State.IDLE.value,
            "elapsed": 0,
            "mode": self.mode,
            "disconnect_policy": "hide",
            "can_start": True,
            "can_stop": False,
            "error": "",
            "revision": self.recording_revision,
        }
        self.process = psutil.Process()
        self.process.cpu_percent()
        self.resources = ResourceMonitor()
        self.build_ui()
        self.apply_mode_audio_defaults()
        self.setStyleSheet(stylesheet(self.library.setting("theme", "Dark") == "Light"))
        apply_window_effects(self, self.library.setting("theme", "Dark") == "Dark")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        # Keep preview presentation below one video frame at 30 FPS; recording
        # remains independent of this UI cadence.
        self.timer.start(33)
        if not smoke:
            threading.Thread(target=self.detect_encoders, name="capability-probe", daemon=True).start()
            threading.Thread(target=self.recover, name="recovery", daemon=True).start()
            self.activate_mode_source(self.mode)

    def saved(self, ident):
        if self.library.setting("auto_compress", True):
            self.compression.enqueue(ident, automatic=True)

    def update_recording_snapshot(self):
        state = self.recorder.state
        active = state.active
        config = self.recorder.config if active and self.recorder.config else None
        mode = config.mode if config else self.mode
        disconnect_policy = config.disconnect_policy if config else self.disconnect_policy.currentData()
        snapshot_key = (state, mode, disconnect_policy)
        state_changed = self._snapshot_state != snapshot_key
        if state_changed:
            self.recording_revision += 1
            self._snapshot_state = snapshot_key
        elapsed = self.recorder.clock.elapsed() if active and self.recorder.clock else 0
        self.phone_recording_snapshot = {
            "state": state.value,
            "elapsed": max(0, round(elapsed, 3)),
            "mode": mode,
            "disconnect_policy": disconnect_policy,
            "can_start": self.recording_can_start(),
            "can_stop": state in {State.PREPARING, State.RECORDING, State.PAUSED},
            "error": str(self.recorder.error or "")[:200],
            "revision": self.recording_revision,
        }
        if state_changed and self.server:
            self.server.push_recording_snapshot()
        return self.phone_recording_snapshot

    def recording_can_start(self):
        state = self.recorder.state
        worker_finishing = (
            state in {State.COMPLETED, State.ERROR}
            and self.recorder.thread is not None
            and self.recorder.thread.is_alive()
        )
        return (
            state in {State.IDLE, State.COMPLETED, State.ERROR}
            and not self.closing
            and not self.benchmark_running
            and not worker_finishing
        )

    def handle_remote_recording(self, request, future, is_current):
        """Run a phone command on Qt's thread and return only confirmed state."""
        if future.cancelled() or future.done() or not is_current():
            if not future.done():
                future.set_result({"ok": False, "error": "Phone connection is no longer active"})
            return
        snapshot = self.update_recording_snapshot()
        if request.get("revision") != snapshot["revision"]:
            future.set_result({
                "ok": False,
                "error": "Desktop recording state changed; refresh the phone controls",
                "recorder": snapshot,
            })
            return
        action = request.get("action")
        if action == "start":
            if not snapshot["can_start"]:
                result = {"ok": False, "error": "A recording is already active", "recorder": snapshot}
            else:
                result = {"ok": self.start_recording(), "recorder": self.update_recording_snapshot()}
                if not result["ok"]:
                    result["error"] = self.recorder.error or "Recording could not start"
        elif action == "stop":
            if not snapshot["can_stop"]:
                result = {"ok": False, "error": "No active recording to stop", "recorder": snapshot}
            else:
                stopped = self.recorder.stop()
                if stopped:
                    self.transport.set_state(State.FINALIZING.value, self.recorder.clock.elapsed())
                    self.statusBar().showMessage("Finalizing recording")
                result = {"ok": stopped, "recorder": self.update_recording_snapshot()}
                if not stopped:
                    result["error"] = "Recording is already stopping or completed"
        else:
            result = {"ok": False, "error": "Unknown recording command", "recorder": snapshot}
        future.set_result(result)

    def recover(self):
        recovered, failures = recover_recordings(self.output, self.library)
        if recovered:
            self.events.message.emit(
                "saved", f"Recovered {len(recovered)} interrupted recording(s); original fragments retained."
            )
        if failures:
            self.events.message.emit(
                "warning", "Some interrupted files need manual recovery. " + "; ".join(failures[:2])
            )

    def build_ui(self):
        central = QWidget()
        central.setObjectName("content")
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(16)
        self.rail = MaterialSurface("glass")
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(72)
        nav = QVBoxLayout(self.rail)
        nav.setContentsMargins(8, 12, 8, 12)
        nav.setSpacing(8)
        mark = label("FL", "brand-mark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setToolTip("FrameLink")
        nav.addWidget(mark)
        nav.addSpacing(16)
        self.pages = QStackedWidget()
        self.rail_buttons = []
        for i, (name, icon) in enumerate(
            [("Studio", "studio"), ("Library", "library"), ("Devices", "devices"), ("Settings", "settings")]
        ):
            b = IconRailButton(icon, name, self.rail)
            b.setChecked(i == 0)
            self.rail_buttons.append(b)
            nav.addWidget(b)
            b.clicked.connect(lambda checked=False, index=i: self.navigate(index))
        nav.addStretch()
        self.rail.button = lambda index: self.rail_buttons[index]
        row.addWidget(self.rail)
        row.addWidget(self.pages, 1)
        self.dashboard()
        self.recordings_page()
        self.devices_page()
        self.settings_page()
        self.statusBar().showMessage("Ready · Start a capture source to preview")
        for index, sequence in enumerate(("Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4")):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(lambda index=index: self.navigate(index))
        self.space_shortcut = QShortcut(QKeySequence("Space"), self)
        self.space_shortcut.activated.connect(self.toggle_recording_from_keyboard)
        self.inspector_shortcut = QShortcut(QKeySequence("I"), self)
        self.inspector_shortcut.activated.connect(self.toggle_inspector)
        self.escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.escape_shortcut.activated.connect(lambda: self.set_inspector_visible(False))

    def page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(label(title, "title"))
        sub = label(subtitle, "subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        self.pages.addWidget(page)
        return layout

    def dashboard(self):
        self.studio_page_widget = QWidget()
        self.studio_page_widget.setObjectName("page")
        self.pages.addWidget(self.studio_page_widget)
        layout = QVBoxLayout(self.studio_page_widget)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(label("Studio", "title"))
        header.addStretch()
        self.mode_selector = SegmentedControl()
        for text, name in [("Screen", "screen"), ("Phone", "phone"), ("Both", "both")]:
            self.mode_selector.add_item(text, name)
        self.mode_selector.set_current_value(self.mode, emit=False)
        self.mode_selector.valueChanged.connect(self.change_mode)
        header.addWidget(self.mode_selector)
        self.preview_fullscreen = IconButton("fullscreen", "Enter fullscreen preview")
        self.preview_fullscreen.clicked.connect(self.toggle_fullscreen_preview)
        header.addWidget(self.preview_fullscreen)
        layout.addLayout(header)

        workspace = QHBoxLayout()
        workspace.setSpacing(12)
        canvas = QVBoxLayout()
        canvas.setSpacing(10)
        self.preview = PreviewWidget()
        self.preview.fullscreenRequested.connect(self.toggle_fullscreen_preview)
        self.preview.framePresented.connect(self.on_preview_presented)
        self.preview.openWifiRequested.connect(self.open_wifi_settings)
        self.preview.certificateSetupRequested.connect(self.certificate_setup)
        self.preview.refreshPairingRequested.connect(self.new_pairing)
        canvas.addWidget(self.preview, 1)
        summary = QHBoxLayout()
        self.backend_label = label(backend_name(), "muted")
        self.phone_label = label("Phone offline", "muted")
        summary.addWidget(self.backend_label)
        summary.addStretch()
        summary.addWidget(self.phone_label)
        canvas.addLayout(summary)
        # Aliases maintained for programmatic and test access
        self.preview_btn = button("Start screen preview", self.toggle_preview)
        self.pair_btn = button("Connect phone", lambda: self.navigate(2))
        self.benchmark_btn = button("Measure performance", self.benchmark)
        self.transport = FloatingTransport()
        self.transport.recordRequested.connect(self.start_recording)
        self.transport.pauseRequested.connect(self.pause_recording)
        self.transport.stopRequested.connect(self.stop_recording)
        self.transport.inspectorRequested.connect(self.toggle_inspector)
        self.transport.microphoneToggled.connect(self.set_microphone_toggle)
        self.transport.audioToggled.connect(self.set_audio_toggle)
        canvas.addWidget(self.transport, 0, Qt.AlignmentFlag.AlignHCenter)
        self.metrics_label = label("Capture health appears while recording.", "muted")
        self.metrics_label.setWordWrap(True)
        self.metrics_label.setToolTip("Detailed capture and encode measurements appear here while recording.")
        canvas.addWidget(self.metrics_label)
        workspace.addLayout(canvas, 1)

        self.inspector = MaterialSurface("elevated")
        self.inspector.setObjectName("inspector")
        self.inspector.setMinimumWidth(300)
        self.inspector.setMaximumWidth(320)
        self.inspector.setVisible(False)
        inspector_layout = QVBoxLayout(self.inspector)
        inspector_layout.setContentsMargins(16, 16, 16, 16)
        inspector_layout.setSpacing(12)
        inspector_header = QHBoxLayout()
        inspector_header.addWidget(label("Recording settings", "title"))
        inspector_header.addStretch()
        inspector_close = IconButton("close", "Close recording settings")
        inspector_close.clicked.connect(lambda: self.set_inspector_visible(False))
        inspector_header.addWidget(inspector_close)
        inspector_layout.addLayout(inspector_header)
        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspector_content = QWidget()
        inspector_content.setObjectName("content")
        self.inspector_form = QFormLayout(inspector_content)
        self.inspector_form.setSpacing(10)
        inspector_layout.addWidget(self.inspector_scroll, 1)
        self.inspector_scroll.setWidget(inspector_content)
        self._build_capture_controls()
        self.fps.activated.connect(self.capture_rate_selected)
        for control in [
            self.monitor,
            self.resolution,
            self.fps,
            self.encoder,
            self.layout_mode,
            self.position,
            self.mic_device,
            self.system_device,
            self.disconnect_policy,
        ]:
            control.setMinimumWidth(110)
            control.setMaximumWidth(175)
        workspace.addWidget(self.inspector)
        layout.addLayout(workspace, 1)
        self._build_transport_aliases()
        self.refresh_inspector()

    def _build_capture_controls(self):
        self.monitor = QComboBox()
        for screen in QApplication.screens():
            self.monitor.addItem(
                f"{screen.name()} · {screen.size().width()} × {screen.size().height()} · {screen.refreshRate():.0f} Hz",
                screen,
            )
        self.resolution = combo(
            [
                ("Native resolution", "native"),
                ("1920 × 1080", "1080"),
                ("1280 × 720", "720"),
                ("Custom", "custom"),
            ]
        )
        self.fps = combo(
            [("Auto / Best Stable", 0), ("30 FPS", 30), ("60 FPS", 60), ("Highest Stable FPS", -1)]
        )
        self.encoder = combo([("Software H.264", "libx264")])
        for name, control in [
            ("Display", self.monitor),
            ("Resolution", self.resolution),
            ("Frame rate", self.fps),
            ("Encoder", self.encoder),
        ]:
            self.inspector_form.addRow(name, control)
        self.layout_mode = combo([("Picture-in-picture", "pip"), ("Separate synchronized files", "separate")])
        self.position = combo(
            [
                (n.replace("-", " ").title(), n)
                for n in ["bottom-right", "bottom-left", "top-right", "top-left"]
            ]
        )
        self.overlay_size = spin(10, 60, 25)
        self.overlay_size.setSuffix(" %")
        self.opacity = spin(0, 100, 100)
        self.opacity.setSuffix(" %")
        self.radius = spin(0, 120, 24)
        self.border = spin(0, 12, 2)
        self.mirror = QCheckBox("Mirror phone camera")
        self.disconnect_policy = combo(
            [
                ("Hide camera / black phone track", "hide"),
                ("Freeze last camera frame", "freeze"),
                ("Pause recording", "pause"),
                ("Stop and save recording", "stop"),
            ]
        )
        self.inspector_form.addRow(label("Composition", "eyebrow"))
        for name, control in [
            ("Combined recording", self.layout_mode),
            ("Camera position", self.position),
            ("Camera width", self.overlay_size),
            ("Camera opacity", self.opacity),
            ("Rounded corners", self.radius),
            ("Camera border", self.border),
            ("Mirror", self.mirror),
            ("If phone disconnects", self.disconnect_policy),
        ]:
            self.inspector_form.addRow(name, control)
        self.preview_policy = combo(
            [
                ("Fill · center crop", "fill"),
                ("Fit · whole image", "fit"),
                ("Stretch · distort (advanced)", "stretch"),
            ]
        )
        self.inspector_form.addRow("Camera preview only", self.preview_policy)
        self.preview_policy.setToolTip(
            "Changes only the phone preview; recording retains its source aspect ratio."
        )
        self.phone_audio = QCheckBox("Record phone microphone")
        self.mic_enabled = QCheckBox("Record selected microphone")
        self.system_enabled = QCheckBox("Record system / loopback audio")
        self.mic_device = QComboBox()
        self.system_device = QComboBox()
        self.audio_devices = QMediaDevices.audioInputs()
        for device in self.audio_devices:
            self.mic_device.addItem(device.description(), device)
            if sys.platform != "win32":
                self.system_device.addItem(device.description(), device)
        default_input = QMediaDevices.defaultAudioInput()
        for index, device in enumerate(self.audio_devices):
            if device.id() == default_input.id():
                self.mic_device.setCurrentIndex(index)
                break
        for control in (self.phone_audio, self.mic_enabled, self.system_enabled):
            control.toggled.connect(self.sync_audio_controls)
        if sys.platform == "win32":
            try:
                for device in WasapiLoopback.devices():
                    self.system_device.addItem(device["name"], device)
            except Exception as exc:
                self.system_device.setToolTip(f"WASAPI device discovery failed: {exc}")
        self.inspector_form.addRow(label("Audio", "eyebrow"))
        for name, control in [
            ("Phone audio", self.phone_audio),
            ("Microphone", self.mic_enabled),
            ("Microphone device", self.mic_device),
            ("System audio", self.system_enabled),
            ("Loopback device", self.system_device),
        ]:
            self.inspector_form.addRow(name, control)
        for name in ["phone", "mic", "system"]:
            control = spin(0, 200, 100)
            control.setSuffix(" %")
            setattr(self, name + "_gain", control)
            self.inspector_form.addRow(name.title() + " gain", control)
        self.custom_width = spin(64, 7680, 1920)
        self.custom_height = spin(64, 4320, 1080)
        self.custom_width.setSingleStep(2)
        self.custom_height.setSingleStep(2)
        custom = QHBoxLayout()
        custom.addWidget(self.custom_width)
        custom.addWidget(label("×"))
        custom.addWidget(self.custom_height)
        self.inspector_form.addRow("Custom resolution", custom)
        self.cursor = QCheckBox("Include cursor")
        self.cursor.setChecked(True)
        self.fallback = QCheckBox("Use DXGI fallback on Windows")
        self.fallback.setEnabled(sys.platform == "win32")
        self.inspector_form.addRow("Cursor", self.cursor)
        self.inspector_form.addRow("Windows capture", self.fallback)
        self.capability_note = label("Detecting encoders…", "muted")
        self.capability_note.setWordWrap(True)
        self.inspector_form.addRow("Encoder diagnostics", self.capability_note)
        adv_actions = QHBoxLayout()
        adv_actions.addWidget(self.preview_btn)
        adv_actions.addWidget(self.pair_btn)
        adv_actions.addWidget(self.benchmark_btn)
        self.inspector_form.addRow("Manual controls", adv_actions)

    def _build_transport_aliases(self):
        self.start_btn = self.transport.record
        self.pause_btn = self.transport.pause
        self.stop_btn = self.transport.stop
        self.timer_label = self.transport.timer
        self.state_label = self.transport.state

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for position, nav_button in enumerate(self.rail_buttons):
            nav_button.setChecked(position == index)

    def toggle_recording_from_keyboard(self):
        if self.recorder.state in {State.RECORDING, State.PAUSED}:
            self.pause_recording()
        elif self.recorder.state in {State.IDLE, State.COMPLETED}:
            self.start_recording()

    def toggle_inspector(self):
        if self.recorder.state.active:
            return
        self.set_inspector_visible(not self.inspector.isVisible())

    def set_inspector_visible(self, visible):
        self.inspector.setVisible(visible)
        self.metrics_label.setVisible(not visible)

    def toggle_fullscreen_preview(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def set_microphone_toggle(self, enabled):
        control = self.phone_audio if self.mode == "phone" else self.mic_enabled
        control.setChecked(enabled)

    def set_audio_toggle(self, enabled):
        self.system_enabled.setChecked(enabled)

    def apply_mode_audio_defaults(self):
        self.phone_audio.setChecked(self.mode == "phone")
        self.mic_enabled.setChecked(self.mode != "phone")
        self.system_enabled.setChecked(False)
        self.sync_audio_controls()

    def sync_audio_controls(self):
        control = self.phone_audio if self.mode == "phone" else self.mic_enabled
        for button, checked in (
            (self.transport.microphone, control.isChecked()),
            (self.transport.audio, self.system_enabled.isChecked()),
        ):
            blocked = button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(blocked)
        text = (
            "Phone microphone for next recording"
            if self.mode == "phone"
            else "Computer microphone for next recording"
        )
        self.transport.microphone.setToolTip(text)
        self.transport.microphone.setAccessibleName(text)

    def refresh_inspector(self):
        def set_row_visible(control, visible):
            field_label = self.inspector_form.labelForField(control)
            if field_label:
                field_label.setVisible(visible)
            control.setVisible(visible)

        screen_visible = self.mode != "phone"
        phone_visible = self.mode in {"phone", "both"}
        both_visible = self.mode == "both"
        for control in [self.monitor, self.resolution, self.fps, self.encoder, self.cursor, self.fallback]:
            set_row_visible(control, screen_visible)
        for control in [self.phone_audio, self.preview_policy]:
            set_row_visible(control, phone_visible)
        for control in [
            self.layout_mode,
            self.position,
            self.overlay_size,
            self.opacity,
            self.radius,
            self.border,
            self.mirror,
            self.disconnect_policy,
        ]:
            set_row_visible(control, both_visible)
        if hasattr(self, "inspector_scroll"):
            self.inspector_scroll.setToolTip(
                "These settings are unavailable while recording is active."
                if self.recorder.state.active
                else "Settings for the selected recording sources."
            )
        for control in [
            self.monitor,
            self.resolution,
            self.fps,
            self.encoder,
            self.layout_mode,
            self.position,
            self.overlay_size,
            self.opacity,
            self.radius,
            self.border,
            self.mirror,
            self.disconnect_policy,
            self.phone_audio,
            self.mic_enabled,
            self.mic_device,
            self.system_enabled,
            self.system_device,
            self.phone_gain,
            self.mic_gain,
            self.system_gain,
        ]:
            control.setEnabled(not self.recorder.state.active)

    def recordings_page(self):
        layout = self.page("Library", "Your recordings and validated exports, kept on this computer.")
        self.library_view = LibraryView()
        self.library_view.set_empty_action(lambda: self.navigate(0))
        self.library_view.recordingActivated.connect(lambda ident: self.library_action("play", ident))
        self.library_view.actionRequested.connect(lambda ident, action: self.library_action(action, ident))
        self.library_view.jobActionRequested.connect(self.library_job_action)
        layout.addWidget(self.library_view, 1)

    def devices_page(self):
        self.devices_page_widget = QWidget()
        self.devices_page_widget.setObjectName("page")
        self.pages.addWidget(self.devices_page_widget)
        layout = QVBoxLayout(self.devices_page_widget)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(label("Devices", "title"))
        header.addWidget(label("Encrypted camera streaming over local Wi-Fi", "subtitle"))
        header.addStretch()
        layout.addLayout(header)
        top = QHBoxLayout()
        self.networks = QComboBox()
        self.refresh_interfaces()
        top.addWidget(label("Network", "eyebrow"))
        top.addWidget(self.networks, 1)
        top.addWidget(button("Refresh", self.refresh_interfaces))
        self.server_btn = button("Start phone server", self.toggle_server, "primary")
        top.addWidget(self.server_btn)
        layout.addLayout(top)
        body = QHBoxLayout()
        qr_card = MaterialSurface("elevated")
        qr_layout = QVBoxLayout(qr_card)
        qr_layout.setContentsMargins(20, 20, 20, 20)
        qr_layout.setSpacing(10)
        qr_layout.addWidget(label("Pair a camera", "eyebrow"))
        self.qr_label = label("Start the server to pair a phone")
        self.qr_label.setMinimumSize(280, 280)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label)
        self.pair_url = label("Pairing links expire after two minutes.", "muted")
        self.pair_url.setWordWrap(True)
        self.pair_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        qr_layout.addWidget(self.pair_url)
        qr_layout.addWidget(button("New pairing code", self.new_pairing))
        body.addWidget(qr_card, 1)
        text_card = MaterialSurface("elevated")
        text_layout = QVBoxLayout(text_card)
        text_layout.setContentsMargins(20, 20, 20, 20)
        text_layout.setSpacing(12)
        text_layout.addWidget(label("Three steps to connect", "eyebrow"))
        self.connection_steps = label(
            "1  Join the same Wi-Fi or hotspot\n\n2  Trust this desktop certificate when Safari or Chrome requires it\n\n3  Scan a fresh QR code, approve camera access, and keep the page open"
        )
        self.connection_steps.setWordWrap(True)
        self.connection_steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_layout.addWidget(self.connection_steps)
        text_layout.addWidget(button("Certificate setup instructions", self.certificate_setup))
        text_layout.addWidget(button("Open certificate folder", self.open_certificate_folder))
        text_layout.addSpacing(8)
        text_layout.addWidget(label("Connection status", "eyebrow"))
        self.connection_detail = label("Phone server is off. No camera connection is accepted.", "muted")
        self.connection_detail.setWordWrap(True)
        text_layout.addWidget(self.connection_detail)
        text_layout.addStretch()
        body.addWidget(text_card, 1)
        layout.addLayout(body)
        footer = QHBoxLayout()
        footer.addWidget(
            label(
                "Use IPv4 on a network that permits local device traffic. The QR token expires after two minutes.",
                "muted",
            ),
            1,
        )
        footer.addWidget(button("Disconnect phone", self.disconnect_phone, "danger"))
        layout.addLayout(footer)
        layout.addStretch()

    def settings_page(self):
        layout = self.page("Settings", "Preferences that shape every new recording.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("content")
        form = QFormLayout(content)
        form.setContentsMargins(0, 8, 0, 8)
        form.setSpacing(16)
        form.addRow(label("Appearance", "eyebrow"))
        self.theme = combo([("Dark", "Dark"), ("Light", "Light")])
        self.theme.setCurrentText(self.library.setting("theme", "Dark"))
        self.theme.currentTextChanged.connect(self.set_theme)
        form.addRow("Appearance", self.theme)
        output_row = QHBoxLayout()
        self.output_label = label(str(self.output))
        self.output_label.setWordWrap(True)
        output_row.addWidget(self.output_label, 1)
        output_row.addWidget(button("Choose folder", self.choose_output))
        form.addRow("Recording folder", output_row)
        self.auto_compress = QCheckBox("Compress after recording")
        self.auto_compress.setChecked(self.library.setting("auto_compress", True))
        self.auto_compress.toggled.connect(lambda v: self.library.set_setting("auto_compress", v))
        form.addRow("Automatic export", self.auto_compress)
        self.keep_original = QCheckBox("Keep Original")
        self.keep_original.setChecked(self.library.setting("keep_original", False))
        self.keep_original.toggled.connect(lambda value: self.library.set_setting("keep_original", value))
        self.keep_original.setToolTip(
            "For future recordings only: keep the original after compression. When off, "
            "a smaller MP4 must pass integrity and quality checks before replacing it. "
            "Existing recordings are always protected."
        )
        form.addRow("Storage", self.keep_original)
        self.compression_preset = combo(
            [(n, n) for n in ["Fast", "Balanced", "Maximum Compression", "Telegram"]]
        )
        self.compression_preset.setCurrentText(self.library.setting("compression_preset", "Balanced"))
        self.compression_preset.currentTextChanged.connect(
            lambda v: self.library.set_setting("compression_preset", v)
        )
        form.addRow("Default export preset", self.compression_preset)
        form.addRow(label("Capture and composition", "eyebrow"))
        capture_note = label(
            "Capture, audio, and combined-scene controls appear contextually in Studio so the live preview stays clear.",
            "muted",
        )
        capture_note.setWordWrap(True)
        form.addRow(capture_note)
        form.addRow(button("Open Studio settings", lambda: (self.navigate(0), self.toggle_inspector())))
        form.addRow(label("Privacy", "eyebrow"))
        privacy = label(
            "Recordings, phone transport, and certificates stay on this computer and local network. FrameLink does not use analytics or cloud uploads.",
            "muted",
        )
        privacy.setWordWrap(True)
        form.addRow(privacy)
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def config(self):
        width, height = 0, 0
        if self.resolution.currentData() == "1080":
            width, height = 1920, 1080
        elif self.resolution.currentData() == "720":
            width, height = 1280, 720
        elif self.resolution.currentData() == "custom":
            width, height = self.custom_width.value(), self.custom_height.value()
        fps = self.fps.currentData() if self.fps.currentData() > 0 else self.measured_fps
        if self.mode == "phone":
            # A phone track that was granted 30 FPS must not be written into a
            # 60 FPS timeline. That duplicates frames and spends the encoder
            # budget without adding motion detail.
            actual_phone_fps = self.sources.phone_preview.fps()
            if actual_phone_fps >= 15:
                fps = 15 if actual_phone_fps < 22.5 else 30
            else:
                fps = min(fps, 30)
        return RecordingConfig(
            mode=self.mode,
            layout=self.layout_mode.currentData(),
            fps=fps,
            width=width,
            height=height,
            encoder=self.encoder.currentData(),
            overlay_size=self.overlay_size.value() / 100,
            overlay_position=self.position.currentData(),
            opacity=self.opacity.value() / 100,
            radius=self.radius.value(),
            border=self.border.value(),
            mirror=self.mirror.isChecked(),
            disconnect_policy=self.disconnect_policy.currentData(),
            phone_audio=self.phone_audio.isChecked(),
            microphone=self.mic_enabled.isChecked(),
            system_audio=self.system_enabled.isChecked(),
            phone_gain=self.phone_gain.value() / 100,
            mic_gain=self.mic_gain.value() / 100,
            system_gain=self.system_gain.value() / 100,
        )

    def change_mode(self, mode):
        if self.recorder.state.active:
            self.mode_selector.set_current_value(self.mode, emit=False)
            return
        changed = self.mode != mode
        self.mode = mode
        if changed:
            self.apply_mode_audio_defaults()
        self.preview_btn.setEnabled(mode != "phone")
        if hasattr(self, "mode_selector") and self.mode_selector.current_value() != mode:
            self.mode_selector.set_current_value(mode, emit=False)
        if hasattr(self, "inspector"):
            self.refresh_inspector()
        if not getattr(self, "smoke", False):
            self.activate_mode_source(mode)

    def activate_mode_source(self, mode):
        if mode == "screen":
            self.preview.clear_qr_card()
            self.preview.set_wifi_assistant(False)
            if not self.desktop.active:
                self.auto_start_screen()
        elif mode == "phone":
            if self.desktop.active:
                self.desktop.stop()
            self.sources.screen.clear()
            self.preview.set_frame(QImage())
            self.preview_btn.setText("Start screen preview")
            if not has_usable_network():
                self.preview.set_wifi_assistant(True)
            else:
                self.preview.set_wifi_assistant(False)
                server_live = self.server and self.server.thread and self.server.thread.is_alive()
                if not server_live and not self.server_starting:
                    self.preview.set_empty_state("Starting pairing server…", "Preparing secure local QR code")
                    self.start_phone_server_auto()
                else:
                    self.update_phone_qr_display()
        elif mode == "both":
            if not self.desktop.active:
                self.auto_start_screen()
            if not has_usable_network():
                self.preview.set_wifi_assistant(True)
            else:
                self.preview.set_wifi_assistant(False)
                server_live = self.server and self.server.thread and self.server.thread.is_alive()
                if not server_live and not self.server_starting:
                    self.start_phone_server_auto()
                else:
                    self.update_phone_qr_display()

    def auto_start_screen(self):
        if getattr(self, "smoke", False):
            return
        try:
            screen = self.monitor.currentData() if hasattr(self, "monitor") else None
            if screen is None:
                screen = QApplication.primaryScreen()
            if screen is None:
                return
            idx = (
                self.monitor.currentIndex()
                if hasattr(self, "monitor") and self.monitor.currentIndex() >= 0
                else 0
            )
            cur = self.cursor.isChecked() if hasattr(self, "cursor") else True
            fb = self.fallback.isChecked() if hasattr(self, "fallback") else False
            selected = self.fps.currentData()
            fps = selected if selected > 0 else self.measured_fps
            refresh = int(round(screen.refreshRate()))
            if refresh > 0:
                fps = min(refresh, fps)
            self.desktop.start(screen, idx, cur, fps, fb)
            self.preview_btn.setText("Stop screen preview")
            if "portal" in backend_name():
                self.on_message("status", "Screen capture active.")
        except Exception as exc:
            self.on_message("error", f"Screen capture: {exc}")

    def capture_rate_selected(self, _index):
        # Qt negotiates maximumFrameRate only when a stream starts. A software
        # limiter alone cannot raise an already negotiated portal stream rate.
        if self.desktop.active and not self.recorder.state.active:
            self.auto_start_screen()

    def start_phone_server_auto(self):
        if getattr(self, "smoke", False) or self.server_starting:
            return
        net_list = interfaces()
        if not net_list:
            self.preview.set_wifi_assistant(True)
            return
        address = net_list[0]["address"]
        self.server_starting = True

        def start():
            try:
                if self.certificates is None:
                    self.certificates = Certificates(self.data_root / "certificates")
                if self.server is None:
                    self.server = PhoneServer(
                        self.sources,
                        self.certificates,
                        self.events.message.emit,
                        lambda: self.recorder.state.value,
                        lambda: self.phone_recording_snapshot,
                        self.events.recording_command.emit,
                    )
                self.server.start(address)
                self.server._ready.wait(20)
                self.server.enable_bootstrap()
                try:
                    self.events.message.emit("server_ready", self.server.origin)
                except RuntimeError:
                    pass
            except Exception as exc:
                try:
                    self.events.message.emit("error", str(exc))
                except RuntimeError:
                    pass
            finally:
                self.server_starting = False

        threading.Thread(target=start, name="auto-phone-server", daemon=True).start()

    def update_phone_qr_display(self):
        if not self.server or self.server.state == "Offline":
            return
        try:
            url = self.server.pairing_url()
            if hasattr(self, "preview") and self.preview._qr_url == url and self.preview._qr_pixmap:
                self.preview._qr_detail = self.server.state
                self.preview.update()
                return
            image = qrcode.make(url).convert("RGB")
            data = io.BytesIO()
            image.save(data, "PNG")
            pix = QPixmap()
            pix.loadFromData(data.getvalue())
            self.preview.set_qr_card(pix, "Pair Phone Camera", self.server.state, url=url)
            if hasattr(self, "qr_label"):
                self.qr_label.setPixmap(
                    pix.scaled(
                        260, 260, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
                    )
                )
            if hasattr(self, "pair_url"):
                self.pair_url.setText(
                    "Scan to connect · expires in 10 minutes\n"
                    + self.server.origin
                    + "\nThe QR contains a one-use secret; do not share it."
                )
        except Exception:
            pass

    def open_wifi_settings(self):
        if not open_system_wifi_settings():
            self.on_message(
                "status", "Could not open system Wi-Fi dialog. Connect via your desktop network menu."
            )

    def toggle_preview(self):
        try:
            if self.desktop.active:
                self.desktop.stop()
                self.preview_btn.setText("Start screen preview")
            else:
                self.auto_start_screen()
        except Exception as exc:
            self.on_message("error", str(exc))

    def start_recording(self, checked=False, measured=False):
        try:
            self.recorder.reset()
            cfg = self.config()
            self.desktop.set_rate(cfg.fps)
            if cfg.microphone:
                if self.mic_device.currentData() is None:
                    raise ValueError("No microphone input is available")
                self.microphone.start(self.mic_device.currentData())
            if cfg.system_audio:
                if self.system_device.currentData() is None:
                    raise ValueError("No system audio input is available")
                if (
                    sys.platform != "win32"
                    and cfg.microphone
                    and self.system_device.currentIndex() == self.mic_device.currentIndex()
                ):
                    raise ValueError(
                        "Choose different microphone and loopback devices to avoid doubling audio"
                    )
                if sys.platform == "win32":
                    self.loopback.start(self.system_device.currentData())
                else:
                    self.system_audio.start(self.system_device.currentData())
            self.recorder.start(cfg)
            self.update_recording_snapshot()
            return True
        except Exception as exc:
            self.microphone.stop()
            self.system_audio.stop()
            self.loopback.stop()
            self.on_message("error", str(exc))
            self.recorder.error = str(exc)
            self.update_recording_snapshot()
            return False

    def pause_recording(self):
        try:
            if self.recorder.state == State.PAUSED:
                self.recorder.resume()
            else:
                self.recorder.pause()
        except ValueError as exc:
            self.on_message("error", str(exc))

    def stop_recording(self):
        self.start_after_benchmark = False
        if self.recorder.stop():
            self.transport.set_state(State.FINALIZING.value, self.recorder.clock.elapsed())
            self.statusBar().showMessage("Finalizing recording")

    def refresh_interfaces(self):
        current = self.networks.currentData() if hasattr(self, "networks") else None
        self.networks.clear()
        for interface in interfaces():
            self.networks.addItem(f"{interface['name']} · {interface['address']}", interface["address"])
        if current:
            index = self.networks.findData(current)
            if index >= 0:
                self.networks.setCurrentIndex(index)

    def toggle_server(self):
        if self.server_starting:
            return
        if self.server and self.server.thread and self.server.thread.is_alive():
            self.server.stop()
            self.server_btn.setEnabled(False)
            return
        address = self.networks.currentData()
        if not address:
            self.on_message("error", "No active private IPv4 network. Join a Wi-Fi network or hotspot first.")
            return
        self.server_btn.setEnabled(False)
        self.server_starting = True

        def start():
            try:
                if self.certificates is None:
                    self.certificates = Certificates(self.data_root / "certificates")
                self.server = PhoneServer(
                    self.sources,
                    self.certificates,
                    self.events.message.emit,
                    lambda: self.recorder.state.value,
                    lambda: self.phone_recording_snapshot,
                    self.events.recording_command.emit,
                )
                self.server.start(address)
                self.server._ready.wait(20)
            except Exception as exc:
                self.events.message.emit("error", str(exc))
            finally:
                self.server_starting = False

        threading.Thread(target=start, name="certificate-setup", daemon=True).start()

    def set_qr(self, url):
        image = qrcode.make(url).convert("RGB")
        data = io.BytesIO()
        image.save(data, "PNG")
        pix = QPixmap()
        pix.loadFromData(data.getvalue())
        if hasattr(self, "qr_label"):
            self.qr_label.setPixmap(
                pix.scaled(
                    260, 260, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
                )
            )
        self.preview.set_qr_card(
            pix, "Pair Phone Camera", self.server.state if self.server else "Scan to pair phone", url=url
        )

    def new_pairing(self):
        try:
            if self.server is None or self.server.state == "Offline":
                raise ValueError("Start the phone server first")
            url = self.server.new_pairing_url()
            self.set_qr(url)
            if hasattr(self, "pair_url"):
                self.pair_url.setText(
                    "Scan to connect · expires in 10 minutes\n"
                    + self.server.origin
                    + "\nThe QR contains a one-use secret; do not share it."
                )
        except ValueError as exc:
            self.on_message("error", str(exc))

    def disconnect_phone(self):
        if self.server:
            self.server.disconnect()

    def open_certificate_folder(self):
        if self.certificates:
            directory = QFileDialog.getExistingDirectory(
                self, "Export public certificate files for USB transfer"
            )
            if directory:
                self.certificates.export_public(directory)
                QDesktopServices.openUrl(QUrl.fromLocalFile(directory))
        else:
            self.on_message("status", "Start the phone server to create this desktop’s certificate identity.")

    def certificate_setup(self):
        if not self.server or self.server.state == "Offline":
            self.on_message("error", "Start the phone server before certificate setup.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Trust FrameLink Desktop on iPhone / iPad")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(label("iPhone & iPad Certificate Trust Setup", "title"))
        sub = label(
            "Apple requires trusted root certificates for in-browser camera streaming over local Wi-Fi.",
            "subtitle",
        )
        sub.setWordWrap(True)
        layout.addWidget(sub)

        warning = label(
            "Prefer exporting the public certificate/profile to removable media for a trusted USB transfer. "
            "The HTTP download below is not authenticated: compare the downloaded certificate fingerprint "
            "with this desktop before installation. A trusted root can authenticate websites; remove it when no longer needed. "
            "Never continue through Safari’s certificate warning."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        layout.addWidget(button("Export public certificate for USB transfer", self.open_certificate_folder))
        diagnostic = label("Server certificate check has not run.", "muted")
        diagnostic.setWordWrap(True)
        layout.addWidget(diagnostic)
        audit_events = Events(dialog)
        audit_events.tls_audit.connect(diagnostic.setText)

        def audit():
            diagnostic.setText("Checking the certificate served at the pairing address…")
            audit_button.setEnabled(False)
            certificates, origin = self.certificates, self.server.origin

            def work():
                try:
                    result = certificates.audit_endpoint(origin)
                    text = f"Served certificate validated: {result['url']} · SAN {', '.join(result['san'])} · {result['tls']}. iPhone full trust still requires manual verification."
                except Exception as exc:
                    text = f"Served certificate validation failed: {exc}"
                try:
                    audit_events.tls_audit.emit(text)
                except RuntimeError:
                    pass  # The window may close while the socket check finishes.

            threading.Thread(target=work, name="tls-audit", daemon=True).start()

        audit_button = button("Validate served certificate at QR address", audit)
        audit_events.tls_audit.connect(lambda _: audit_button.setEnabled(True))
        layout.addWidget(audit_button)
        url = self.server.enable_bootstrap()
        qr_card = MaterialSurface("elevated")
        qr_layout = QHBoxLayout(qr_card)
        qr_layout.setContentsMargins(16, 16, 16, 16)
        qr_layout.setSpacing(20)

        qr_img = label("")
        qr_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_data = io.BytesIO()
        qrcode.make(url).save(img_data, format="PNG")
        qpix = QPixmap()
        qpix.loadFromData(img_data.getvalue())
        qr_img.setPixmap(
            qpix.scaled(
                180, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        qr_layout.addWidget(qr_img)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)
        info_layout.addWidget(label("1. Scan with iPhone Camera", "eyebrow"))
        step1 = label("Opens the local certificate setup page in Safari:")
        step1.setWordWrap(True)
        info_layout.addWidget(step1)
        url_link = label(f"<a style='color:#38bdf8;' href='{url}'>{url}</a>")
        url_link.setOpenExternalLinks(True)
        info_layout.addWidget(url_link)

        fp_box = label(f"SHA-256 Fingerprint:\n{self.certificates.fingerprint}", "muted")
        fp_box.setWordWrap(True)
        fp_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(fp_box)
        qr_layout.addLayout(info_layout, 1)
        layout.addWidget(qr_card)

        steps_card = MaterialSurface("elevated")
        steps_layout = QVBoxLayout(steps_card)
        steps_layout.setContentsMargins(16, 16, 16, 16)
        steps_layout.setSpacing(10)
        steps_layout.addWidget(label("2. Install and enable full trust on your iPhone:", "eyebrow"))

        steps_text = label(
            "• Tap 'Download iOS Trust Profile' → Tap 'Allow'\n"
            "• Open iPhone Settings → Tap 'Profile Downloaded' at the top → Tap 'Install'\n"
            "• In Settings: General → About → Certificate Trust Settings\n"
            "• Under 'Enable full trust for root certificates', turn ON 'FrameLink Local CA'\n"
            "• Open 'Test Secure Connection' in a fresh Safari tab. It must open without a warning; "
            "loading the page after a bypass does not prove trust. Then scan the Studio pairing code."
        )
        steps_text.setWordWrap(True)
        steps_layout.addWidget(steps_text)
        layout.addWidget(steps_card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = button("Done", dialog.accept, "primary")
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dialog.exec()

    def detect_encoders(self):
        self.events.encoders.emit([probe_encoder(name) for name in RECORD_ENCODERS])

    def on_encoders(self, results):
        self.available_encoders = results
        self.encoder.clear()
        for result in results:
            if result["available"]:
                self.encoder.addItem(result["name"], result["name"])
        if not self.encoder.count():
            self.encoder.addItem("Software H.264 (unverified)", "libx264")
        self.capability_note.setText(
            "\n".join(f"{r['name']}: {'available' if r['available'] else r['error']}" for r in results)
        )

    def benchmark(self):
        if self.benchmark_running or self.recorder.state not in {State.IDLE, State.COMPLETED}:
            return
        sample = getattr(self.sources, "phone" if self.mode == "phone" else "screen").latest()
        if sample is None:
            self.start_after_benchmark = False
            self.on_message("error", "Start a live preview before measuring performance.")
            return
        try:
            cfg = self.config()
        except ValueError as exc:
            self.start_after_benchmark = False
            self.on_message("error", str(exc))
            return
        self.benchmark_btn.setEnabled(False)
        self.benchmark_running = True
        self.statusBar().showMessage("Measuring capture and encoding throughput for three seconds…")
        height, width = sample.rgb.shape[:2]
        width, height = (cfg.width, cfg.height) if cfg.width else (width // 2 * 2, height // 2 * 2)
        encoder = cfg.encoder
        refresh = self.monitor.currentData().refreshRate() if self.monitor.currentData() else 30
        self.desktop.set_rate(min(120, max(30, round(refresh))))

        def measure():
            # Measure the actual frame conversion and encoding path with captured content.
            import av
            from fractions import Fraction
            from framelink.media.encode import encoder_options

            result = {"available": False, "error": ""}
            try:
                ctx = av.CodecContext.create(encoder, "w")
                ctx.width = width
                ctx.height = height
                ctx.pix_fmt = "yuv420p"
                ctx.time_base = Fraction(1, 120)
                ctx.framerate = Fraction(120)
                ctx.thread_count = 2
                ctx.options = encoder_options(
                    encoder, preset="ultrafast" if encoder == "libx264" else "veryfast"
                )
                ctx.open()
                start, count = time.perf_counter(), 0
                while time.perf_counter() - start < 3:
                    current = getattr(self.sources, "phone" if cfg.mode == "phone" else "screen").latest()
                    phone = self.sources.phone.latest()
                    image = compose(
                        current.rgb if cfg.mode != "phone" else None,
                        phone.rgb if phone else None,
                        (width, height),
                        cfg,
                    )
                    frame = av.VideoFrame.from_ndarray(np.asarray(image), format="rgb24")
                    frame.pts = count
                    ctx.encode(frame)
                    count += 1
                ctx.encode()
                measured = count / (time.perf_counter() - start)
                source_fps = getattr(self.sources, "phone" if cfg.mode == "phone" else "screen").fps()
                stable, limited = stable_frame_rate(
                    measured, source_fps, refresh if cfg.mode != "phone" else source_fps
                )
                result = {
                    "available": True,
                    "fps": measured,
                    "source_fps": source_fps,
                    "stable": stable,
                    "limited": limited,
                }
            except Exception as exc:
                result["error"] = str(exc)
            self.events.benchmark.emit(result)

        threading.Thread(target=measure, name="throughput-probe", daemon=True).start()

    def on_benchmark(self, result):
        self.benchmark_running = False
        self.benchmark_btn.setEnabled(True)
        if not result["available"]:
            self.start_after_benchmark = False
            self.on_message("error", result["error"])
            return
        self.measured_fps = result["stable"]
        if result["stable"] >= 120 and self.fps.findData(120) < 0:
            self.fps.addItem("120 FPS (measured capacity)", 120)
        self.on_message(
            "status",
            f"Measured {result['fps']:.1f} encode FPS; capture events {result['source_fps']:.1f}/s. Auto selects {self.measured_fps} FPS. "
            + (
                "Measured capacity is low; reduce output resolution. "
                if result["limited"]
                    else "The selected rate is based on measured capture and encoding capacity. "
            )
            + "Content and thermal load can change performance.",
        )
        if self.start_after_benchmark:
            self.start_after_benchmark = False
            if not self.closing:
                self.start_recording(measured=True)

    def library_action(self, action, ident=None):
        ident = ident or self.library_view.selected_id()
        if ident is None:
            return
        item = self.library.get(ident)
        if not item:
            return
        path = Path(item["path"])
        try:
            if action in {"rename", "delete"} and any(
                j["recording_id"] == ident and j["status"] in {"queued", "running"}
                for j in self.library.jobs()
            ):
                raise ValueError("Cancel compression for this recording before changing its file")
            if action == "play":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            elif action == "folder":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            elif action == "rename":
                value, ok = QInputDialog.getText(
                    self, "Rename recording", "Filename without extension", text=path.stem
                )
                if ok:
                    self.library.rename(ident, value)
            elif action == "delete":
                if any(
                    j["recording_id"] == ident and j["status"] in {"queued", "running"}
                    for j in self.library.jobs()
                ):
                    raise ValueError("Cancel compression for this recording before deleting it")
                if (
                    QMessageBox.question(
                        self,
                        "Delete recording",
                        f"Permanently delete {path.name}?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    == QMessageBox.StandardButton.Yes
                ):
                    self.library.delete(ident)
            elif action == "compress":
                self.compression_dialog(ident)
            elif action == "export":
                target, _ = QFileDialog.getSaveFileName(
                    self, "Export a copy", path.name, "Video files (*.mkv *.mp4)"
                )
                if target:
                    destination = Path(target)
                    if destination.suffix.lower() != path.suffix.lower():
                        raise ValueError(
                            "A copied export must keep the original extension. Use Compress again to create MP4."
                        )

                    def copy():
                        try:
                            # Exclusive creation prevents a race from replacing another file.
                            with path.open("rb") as source, destination.open("xb") as out:
                                shutil.copyfileobj(source, out)
                            self.events.message.emit("status", f"Exported copy to {destination}")
                        except Exception as exc:
                            self.events.message.emit("error", str(exc))

                    threading.Thread(target=copy, name="export", daemon=False).start()
            self.last_table_signature = None
        except Exception as exc:
            self.on_message("error", str(exc))

    def compression_dialog(self, ident):
        dialog = QDialog(self)
        dialog.setWindowTitle("Compress recording")
        form = QFormLayout(dialog)
        presets = combo([(n, n) for n in ["Fast", "Balanced", "Maximum Compression", "Telegram", "Custom"]])
        presets.setCurrentText("Balanced")
        codecs = combo(
            [
                ("H.264 · compatible MP4", "libx264"),
                ("H.265 / HEVC", "libx265"),
                ("AV1 · SVT", "libsvtav1"),
                ("AV1 · AOM", "libaom-av1"),
            ]
        )
        quality = spin(0, 51, 24)
        speed = combo([(s, s) for s in ["ultrafast", "veryfast", "fast", "medium", "slow"]])
        speed.setCurrentText("medium")
        width = combo([("Original", 0), ("640", 640), ("1280", 1280), ("1920", 1920)])
        fps = combo([("Original", 0), ("24", 24), ("30", 30), ("60", 60)])
        audio = spin(32, 320, 128)
        target = spin(0, 100000, 0)
        target.setSuffix(" MB (0 = quality mode)")
        for text, widget in [
            ("Preset", presets),
            ("Codec", codecs),
            ("CRF quality (lower = larger)", quality),
            ("Encoder speed", speed),
            ("Maximum width", width),
            ("Maximum FPS", fps),
            ("Audio kbit/s", audio),
            ("Target size", target),
        ]:
            form.addRow(text, widget)
        estimate = label(
            "Quality-based size is content dependent. Target size uses a bitrate estimate with 4% container headroom; an oversized result fails validation.",
            "muted",
        )
        estimate.setWordWrap(True)
        form.addRow(estimate)

        def preset_changed(name):
            if name == "Custom":
                return
            settings = CompressionSettings.preset_named(name)
            codecs.setCurrentIndex(codecs.findData(settings.codec))
            quality.setValue(settings.quality)
            speed.setCurrentText(settings.preset)
            width.setCurrentIndex(width.findData(settings.width))
            fps.setCurrentIndex(fps.findData(settings.fps))
            audio.setValue(settings.audio_bitrate // 1000)

        presets.currentTextChanged.connect(preset_changed)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            settings = CompressionSettings(
                codecs.currentData(),
                quality.value(),
                speed.currentText(),
                width.currentData(),
                fps.currentData(),
                audio.value() * 1000,
                target.value(),
            )
            self.compression.enqueue(ident, settings)
            self.navigate(1)

    def selected_job(self):
        return getattr(self.library_view, "selected_job_id", lambda: None)()

    def library_job_action(self, ident, action):
        if action == "cancel":
            self.compression.cancel(ident)
        elif action == "retry":
            self.compression.retry(ident)

    def cancel_job(self):
        ident = self.selected_job()
        if ident:
            self.compression.cancel(ident)

    def retry_job(self):
        ident = self.selected_job()
        if ident:
            self.compression.retry(ident)

    def choose_output(self):
        if self.recorder.state not in {State.IDLE, State.COMPLETED}:
            self.on_message("error", "Stop recording before changing the output directory.")
            return
        selected = QFileDialog.getExistingDirectory(self, "Recording folder", str(self.output))
        if selected:
            self.output = Path(selected)
            self.recorder.output = self.output
            self.library.set_setting("output", selected)
            self.output_label.setText(selected)

    def set_theme(self, theme):
        self.library.set_setting("theme", theme)
        self.setStyleSheet(stylesheet(theme == "Light"))
        apply_window_effects(self, theme == "Dark")

    def on_message(self, kind, text):
        self.statusBar().showMessage(text)
        if kind in {"server", "server_ready"}:
            self.update_phone_qr_display()
        if kind == "phone":
            self.phone_label.setText(text)
            if hasattr(self, "preview") and self.preview._qr_pixmap:
                self.preview._qr_detail = text
                self.preview.update()
            if (
                text.lower().startswith("waiting for phone")
                and self.server
                and self.server.session_secret is None
            ):
                # An explicit phone Exit consumes the one-use QR token. Issue
                # a fresh code immediately; temporary WebRTC reconnects do
                # not clear session_secret and therefore do not rotate QR.
                self.new_pairing()
        if kind in {"saved", "compressed"}:
            self.last_table_signature = None
        if kind == "error":
            # Non-modal reporting keeps Stop available when a source fails.
            self.connection_detail.setText(text) if hasattr(self, "connection_detail") else None
            self.statusBar().setToolTip(text)

    def on_preview_presented(self, sequence):
        if self.server:
            self.server.mark_preview_displayed(sequence)

    def refresh_tables(self):
        items = self.library.recordings()
        jobs = self.library.jobs()
        signature = json.dumps([items, jobs], sort_keys=True)
        if signature == self.last_table_signature:
            return
        self.last_table_signature = signature
        self.library_view.refresh(items, jobs)

    def tick(self):
        state = self.recorder.state
        self.update_recording_snapshot()
        active = state.active
        elapsed = self.recorder.clock.elapsed() if active and self.recorder.clock else 0
        self.transport.set_state(state.value, elapsed)
        worker_finishing = (
            state in {State.COMPLETED, State.ERROR}
            and self.recorder.thread is not None
            and self.recorder.thread.is_alive()
        )
        self.start_btn.setEnabled(
            (
                self.recording_can_start()
                or state in {State.PREPARING, State.RECORDING, State.PAUSED}
            )
            and state not in {State.STOPPING, State.FINALIZING}
            and not worker_finishing
        )
        for widget in [
            self.monitor,
            self.resolution,
            self.fps,
            self.encoder,
            self.preview_btn,
        ]:
            widget.setEnabled(not active and not self.benchmark_running)
        self.mode_selector.setEnabled(not active and not self.benchmark_running)
        if self.mode == "phone":
            self.preview_btn.setEnabled(False)
        if self.last_state != state:
            self.refresh_inspector()
            if state in {State.IDLE, State.COMPLETED, State.ERROR}:
                self.microphone.stop()
                self.system_audio.stop()
                self.loopback.stop()
        self.last_state = state
        try:
            cfg = self.recorder.config if active else self.config()
        except ValueError:
            # Editing a custom dimension may temporarily produce an odd number.
            # Validation still rejects it when Start is pressed.
            cfg = RecordingConfig(mode=self.mode)
        screen, phone = self.sources.screen.latest(), self.sources.phone_preview.latest()
        phone_live = phone is not None and (time.monotonic() - phone.timestamp <= 3)

        # Detect self-capture recursion
        if self.mode in {"screen", "both"} and self.desktop.active:
            cur_scr = self.screen()
            cap_scr = self.monitor.currentData() if hasattr(self, "monitor") else None
            if cap_scr is None:
                cap_scr = QApplication.primaryScreen()
            self.preview.set_self_capture_hint(
                cur_scr == cap_scr and self.isVisible() and not self.isMinimized()
            )
        else:
            self.preview.set_self_capture_hint(False)

        # Update Audio level meter on transport
        if self.recorder.state == State.RECORDING:
            try:
                audio_config = self.recorder.config
                start = time.monotonic() - 0.05
                mixed = np.zeros((2, 2400), dtype=np.float32)
                for name, enabled, gain in (
                    ("phone", audio_config.phone_audio, audio_config.phone_gain),
                    ("mic", audio_config.microphone, audio_config.mic_gain),
                    ("system", audio_config.system_audio, audio_config.system_gain),
                ):
                    if enabled:
                        mixed += self.sources.audio[name].read(start, 2400) * gain
                rms = float(np.sqrt(np.mean(np.clip(mixed, -1, 1) ** 2)))
                self.transport.set_audio_level(min(1.0, rms * 5))
            except Exception:
                pass
        else:
            self.transport.set_audio_level(0.0)

        self.preview.set_aspect_policy(self.preview_policy.currentData() if self.mode == "phone" else "fit")
        if self.mode == "phone":
            if self.desktop.active:
                self.desktop.stop()
                self.sources.screen.clear()
            if phone_live:
                self.preview.clear_qr_card()
                self.preview.set_wifi_assistant(False)
                rgb = np.ascontiguousarray(phone.rgb[:, ::-1] if cfg.mirror else phone.rgb)
                height, width = rgb.shape[:2]
                self.preview.set_frame(
                    QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888),
                    sequence=phone.sequence,
                    owner=rgb,
                )
                self.preview.set_frame_info(
                    f"Phone: {self.sources.phone.fps():.0f} FPS · {phone.rgb.shape[1]}×{phone.rgb.shape[0]}"
                )
                self.transport.set_background_frame(self.preview.current_frame())
            else:
                self.preview.set_frame(QImage())
                if not has_usable_network():
                    self.preview.set_wifi_assistant(True)
                else:
                    self.preview.set_wifi_assistant(False)
                    server_live = self.server and self.server.thread and self.server.thread.is_alive()
                    if server_live:
                        if not self.preview._qr_pixmap:
                            self.update_phone_qr_display()
                        elif (
                            self.server and hasattr(self.server, "tokens") and self.server.tokens.is_expired()
                        ):
                            self.preview.set_qr_expired(True)
                    elif not self.server_starting:
                        self.start_phone_server_auto()
                self.transport.set_background_frame(QImage())

        elif self.mode == "both":
            if screen:
                size = (640, max(2, round(640 * screen.rgb.shape[0] / screen.rgb.shape[1])))
                scaled_screen = np.asarray(Image.fromarray(screen.rgb).resize(size))
                if phone_live:
                    self.preview.clear_qr_card()
                    self.preview.set_wifi_assistant(False)
                    image = compose(scaled_screen, phone.rgb, size, cfg)
                    data = image.tobytes()
                    self.preview.set_frame(
                        QImage(
                            data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888
                        ).copy()
                    )
                    self.preview.set_frame_info(
                        f"Screen {self.sources.screen.fps():.0f} · Phone {self.sources.phone.fps():.0f} FPS"
                    )
                else:
                    image = compose(scaled_screen, None, size, cfg)
                    data = image.tobytes()
                    self.preview.set_frame(
                        QImage(
                            data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888
                        ).copy()
                    )
                    self.preview.set_frame_info(f"Screen {self.sources.screen.fps():.0f} FPS")
                    if not has_usable_network():
                        self.preview.set_wifi_assistant(True)
                    else:
                        self.preview.set_wifi_assistant(False)
                        server_live = self.server and self.server.thread and self.server.thread.is_alive()
                        if server_live:
                            if not self.preview._qr_pixmap:
                                self.update_phone_qr_display()
                            elif (
                                self.server
                                and hasattr(self.server, "tokens")
                                and self.server.tokens.is_expired()
                            ):
                                self.preview.set_qr_expired(True)
                        elif not self.server_starting:
                            self.start_phone_server_auto()
                self.transport.set_background_frame(self.preview.current_frame())
            else:
                self.preview.set_empty_state(
                    "Starting screen preview…", "Initializing desktop screen capture"
                )
                self.transport.set_background_frame(QImage())

        else:  # mode == "screen"
            self.preview.clear_qr_card()
            self.preview.set_wifi_assistant(False)
            if screen:
                size = (640, max(2, round(640 * screen.rgb.shape[0] / screen.rgb.shape[1])))
                scaled_screen = np.asarray(Image.fromarray(screen.rgb).resize(size))
                image = compose(scaled_screen, None, size, cfg)
                data = image.tobytes()
                self.preview.set_frame(
                    QImage(
                        data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888
                    ).copy()
                )
                self.preview.set_frame_info(
                    f"Screen: {self.sources.screen.fps():.0f} FPS · {screen.rgb.shape[1]}×{screen.rgb.shape[0]}"
                )
                self.transport.set_background_frame(self.preview.current_frame())
            else:
                self.preview.set_empty_state(
                    "Starting screen preview…", "Initializing desktop screen capture"
                )
                self.transport.set_background_frame(QImage())
        server_live = self.server and self.server.thread and self.server.thread.is_alive()
        self.server_btn.setEnabled(not self.server_starting)
        self.server_btn.setText("Stop phone server" if server_live else "Start phone server")
        self.networks.setEnabled(not server_live)
        self.phone_label.setText(self.server.state if server_live else "Phone offline")
        if server_live:
            self.connection_detail.setText(
                f"{self.server.state} · {self.server.origin} · Receive {self.server.bitrate / 1000:.0f} kbit/s"
            )
        now = time.monotonic()
        if now - self.last_network_check >= 1:
            self.last_network_check = now
            metrics = self.recorder.metrics
            elapsed = self.recorder.clock.elapsed() if self.recorder.clock else 0
            free = shutil.disk_usage(self.output).free
            gpu = f"{self.resources.gpu}%" if self.resources.gpu is not None else "unavailable"
            source_name = "phone" if self.mode == "phone" else "screen"
            source_metrics = getattr(self.sources, source_name).metrics_snapshot()
            unique_source = metrics.get("source_frames_received", {}).get(source_name, source_metrics["received"])
            output_fps = metrics.get("encoded_fps", metrics.get("encoded", 0) / max(0.1, elapsed))
            self.metrics_label.setText(
                f"Capture {source_metrics['capture_fps']:.1f} · Unique {unique_source} · Output {output_fps:.1f} FPS   |   Queue {source_metrics['queue_depth']}/{source_metrics['max_queue_depth']}   |   Dropped {source_metrics['dropped']}   |   Duplicated {metrics.get('duplicated_frames', 0)}   |   CPU {self.process.cpu_percent():.0f}%   |   RAM {self.process.memory_info().rss / 1024**2:.0f} MB   |   File {filesize(metrics.get('bytes', 0))}   |   Free {filesize(free)}"
            )
            self.refresh_tables()
            self.metrics_label.setToolTip(
                f"Phone diagnostics: {self.server.phone_quality if self.server else {}} · Received: {self.server.metrics if self.server else {}}\n"
                f"ICE/UDP diagnostics: {self.server.network_diagnostics() if self.server else {}}\n"
                f"Capture metrics: {source_metrics}\n"
                f"Recorder performance: {metrics}\n"
                f"System GPU load: {gpu} · System memory use: {self.resources.memory_pressure:.0f}% · CPU percentage is per process and may exceed 100% across cores. Frame rate in the library is measured from decoded frames, not the stream's nominal rate."
            )
            if server_live and self.server.address not in {i["address"] for i in interfaces()}:
                self.server.stop()
                self.on_message(
                    "error",
                    "The selected network address disappeared. Reconnect Wi-Fi, refresh networks, and start a new phone session.",
                )
        if self.closing:
            if active or self.server_starting:
                return
            if not self.shutdown_started:
                self.shutdown_started = True
                self.desktop.stop()
                self.microphone.stop()
                self.system_audio.stop()
                self.loopback.stop()
                if self.server:
                    self.server.stop()
                self.compression.stop()
                self.resources.stop()
            if self.compression.thread.is_alive() or (server_live):
                return
            self.timer.stop()
            QApplication.instance().quit()

    def closeEvent(self, event):
        if (
            self.shutdown_started
            and not self.compression.thread.is_alive()
            and not (self.server and self.server.thread and self.server.thread.is_alive())
        ):
            event.accept()
            return
        event.ignore()
        self.closing = True
        self.recorder.stop()
        self.statusBar().showMessage("Saving recording and stopping local connections…")
