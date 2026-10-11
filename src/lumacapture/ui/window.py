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
from PySide6.QtGui import QPixmap, QImage, QDesktopServices, QIcon, QPainter, QColor
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
    QGridLayout,
    QFormLayout,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QStackedWidget,
    QButtonGroup,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFileDialog,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
)
from lumacapture.capture.desktop import DesktopCapture, backend_name
from lumacapture.capture.audio import AudioCapture
from lumacapture.capture.loopback import WasapiLoopback
from lumacapture.compression.queue import CompressionQueue, CompressionSettings
from lumacapture.media.buffers import Sources
from lumacapture.media.model import RecordingConfig, State
from lumacapture.media.compositor import compose
from lumacapture.media.encode import probe_encoder, RECORD_ENCODERS
from lumacapture.networking.interfaces import interfaces
from lumacapture.networking.server import PhoneServer
from lumacapture.recording.engine import Recorder, recover_recordings
from lumacapture.security.certificates import Certificates
from lumacapture.ui.theme import stylesheet


class Events(QObject):
    message = Signal(str, str)
    encoders = Signal(object)
    benchmark = Signal(object)


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
        self.setWindowTitle("LumaCapture")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "lumacapture.svg")))
        self.resize(1240, 870)
        self.setMinimumSize(1060, 740)
        self.events = Events()
        self.events.message.connect(self.on_message)
        self.events.encoders.connect(self.on_encoders)
        self.events.benchmark.connect(self.on_benchmark)
        self.sources = Sources()
        self.output = Path(self.library.setting("output", str(Path.home() / "Videos" / "LumaCapture")))
        self.output.mkdir(parents=True, exist_ok=True)
        self.compression = CompressionQueue(library, self.events.message.emit)
        self.recorder = Recorder(self.sources, library, self.output, self.events.message.emit, self.saved)
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
        self.certificates = None
        self.mode = "screen"
        self.closing = False
        self.shutdown_started = False
        self.last_table_signature = None
        self.last_network_check = 0
        self.available_encoders = []
        self.measured_fps = 30
        self.last_state = None
        self.process = psutil.Process()
        self.process.cpu_percent()
        self.build_ui()
        self.setStyleSheet(stylesheet(self.library.setting("theme", "Dark") == "Light"))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(125)
        if not smoke:
            threading.Thread(target=self.detect_encoders, name="capability-probe", daemon=True).start()
            threading.Thread(target=self.recover, name="recovery", daemon=True).start()

    def saved(self, ident):
        if self.library.setting("auto_compress", True):
            self.compression.enqueue(ident)

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
        central.setObjectName("page")
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(18, 30, 18, 24)
        nav.setSpacing(8)
        nav.addWidget(label("LumaCapture", "brand"))
        nav.addWidget(label("LOCAL RECORDING STUDIO", "eyebrow"))
        nav.addSpacing(34)
        self.pages = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, name in enumerate(["Dashboard", "Recordings", "Devices", "Compression Queue", "Settings"]):
            b = button(name, name="nav")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.nav_group.addButton(b, i)
            nav.addWidget(b)
            b.clicked.connect(lambda checked=False, index=i: self.pages.setCurrentIndex(index))
        nav.addStretch()
        nav.addWidget(label("PRIVATE BY DESIGN", "eyebrow"))
        foot = label("Local Wi-Fi. Local files.\nNo cloud account.", "muted")
        foot.setWordWrap(True)
        nav.addWidget(foot)
        nav.addSpacing(16)
        nav.addWidget(label("LumaCapture 0.1.0", "muted"))
        row.addWidget(sidebar)
        row.addWidget(self.pages, 1)
        self.dashboard()
        self.recordings_page()
        self.devices_page()
        self.queue_page()
        self.settings_page()
        self.statusBar().showMessage("Ready · Start a capture source to preview")

    def page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(18)
        layout.addWidget(label(title, "title"))
        sub = label(subtitle, "subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        self.pages.addWidget(page)
        return layout

    def dashboard(self):
        layout = self.page("Recording studio", "Set your scene. Capture locally. Keep the original.")
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for text, name in [("Phone Camera", "phone"), ("Desktop Screen", "screen"), ("Both", "both")]:
            b = button(text)
            b.setCheckable(True)
            b.setChecked(name == self.mode)
            self.mode_group.addButton(b)
            b.clicked.connect(lambda checked=False, m=name: self.change_mode(m))
            modes.addWidget(b)
        layout.addLayout(modes)
        self.preview = Preview()
        layout.addWidget(self.preview, 1)
        summary = QHBoxLayout()
        self.backend_label = label(backend_name(), "muted")
        self.phone_label = label("Phone offline", "muted")
        summary.addWidget(self.backend_label)
        summary.addStretch()
        summary.addWidget(self.phone_label)
        layout.addLayout(summary)
        controls, inner = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
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
        for col, (name, control) in enumerate(
            [
                ("DISPLAY", self.monitor),
                ("RESOLUTION", self.resolution),
                ("FRAME RATE", self.fps),
                ("ENCODER", self.encoder),
            ]
        ):
            grid.addWidget(label(name, "eyebrow"), 0, col)
            grid.addWidget(control, 1, col)
        inner.addLayout(grid)
        actions = QHBoxLayout()
        self.preview_btn = button("Start screen preview", self.toggle_preview)
        self.pair_btn = button("Connect phone", lambda: self.navigate(2))
        self.benchmark_btn = button("Measure performance", self.benchmark)
        actions.addWidget(self.preview_btn)
        actions.addWidget(self.pair_btn)
        actions.addWidget(self.benchmark_btn)
        actions.addStretch()
        inner.addLayout(actions)
        layout.addWidget(controls)
        bar = QHBoxLayout()
        self.timer_label = label("00:00:00", "timer")
        self.state_label = label("Ready", "muted")
        bar.addWidget(self.timer_label)
        bar.addWidget(self.state_label)
        bar.addStretch()
        self.start_btn = button("Start recording", self.start_recording, "primary")
        self.pause_btn = button("Pause", self.pause_recording)
        self.pause_btn.setEnabled(False)
        self.stop_btn = button("Stop", self.stop_recording)
        self.stop_btn.setEnabled(False)
        bar.addWidget(self.start_btn)
        bar.addWidget(self.pause_btn)
        bar.addWidget(self.stop_btn)
        layout.addLayout(bar)
        self.metrics_label = label("Performance measurements appear while recording.", "muted")
        self.metrics_label.setWordWrap(True)
        layout.addWidget(self.metrics_label)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)

    def recordings_page(self):
        layout = self.page("Recordings", "Your recordings and validated exports, stored on this computer.")
        self.recordings = QTableWidget(0, 8)
        self.recordings.setHorizontalHeaderLabels(
            ["Recording", "Duration", "Resolution", "FPS", "Original", "Export", "Status", "Created"]
        )
        self.recordings.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.recordings.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recordings.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.recordings.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recordings.setIconSize(QSize(72, 42))
        self.recordings.verticalHeader().hide()
        self.recordings.itemDoubleClicked.connect(lambda item: self.library_action("play"))
        layout.addWidget(self.recordings, 1)
        actions = QHBoxLayout()
        for text, action in [
            ("Play", "play"),
            ("Open folder", "folder"),
            ("Rename", "rename"),
            ("Compress again", "compress"),
            ("Export", "export"),
            ("Delete", "delete"),
        ]:
            actions.addWidget(button(text, lambda checked=False, a=action: self.library_action(a)))
        layout.addLayout(actions)
        self.library_note = label("No recordings yet. Start a recording from the dashboard.", "muted")
        layout.addWidget(self.library_note)

    def devices_page(self):
        layout = self.page(
            "Connect a phone",
            "Android Chrome and iPhone Safari · encrypted camera streaming over local Wi-Fi",
        )
        top = QHBoxLayout()
        self.networks = QComboBox()
        self.refresh_interfaces()
        top.addWidget(self.networks, 1)
        top.addWidget(button("Refresh networks", self.refresh_interfaces))
        self.server_btn = button("Start phone server", self.toggle_server, "primary")
        top.addWidget(self.server_btn)
        layout.addLayout(top)
        body = QHBoxLayout()
        qr_card, qr_layout = card()
        self.qr_label = label("Start the server to pair a phone")
        self.qr_label.setMinimumSize(260, 260)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label)
        self.pair_url = label("Pairing links expire after two minutes.", "muted")
        self.pair_url.setWordWrap(True)
        self.pair_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        qr_layout.addWidget(self.pair_url)
        qr_layout.addWidget(button("New pairing code", self.new_pairing))
        body.addWidget(qr_card, 1)
        text_card, text_layout = card()
        text_layout.addWidget(label("First-time trust setup", "brand"))
        steps = label(
            "1. Connect your phone and desktop to the same Wi-Fi or hotspot.\n\n2. Install this desktop’s local certificate on the phone and enable trust. Verify the certificate fingerprint.\n\n3. Scan a fresh HTTPS pairing code. Tap Connect camera and grant permission.\n\n4. Keep the phone page open and unlocked. Start recording on the desktop."
        )
        steps.setWordWrap(True)
        text_layout.addWidget(steps)
        text_layout.addWidget(button("Certificate setup instructions", self.certificate_setup))
        text_layout.addWidget(button("Open certificate folder", self.open_certificate_folder))
        text_layout.addStretch()
        body.addWidget(text_card, 1)
        layout.addLayout(body)
        self.connection_detail = label("Phone server is off. No camera connection is accepted.", "muted")
        self.connection_detail.setWordWrap(True)
        layout.addWidget(self.connection_detail)
        disconnect = button("Disconnect phone", self.disconnect_phone, "danger")
        layout.addWidget(disconnect)
        troubleshooting = label(
            "Connection trouble? Check the selected address, allow TCP 8443 and local WebRTC UDP through your firewall, and disable hotspot client isolation. A certificate warning means trust setup is incomplete. IPv4 LAN interfaces are supported.",
            "muted",
        )
        troubleshooting.setWordWrap(True)
        layout.addWidget(troubleshooting)
        layout.addStretch()

    def queue_page(self):
        layout = self.page(
            "Compression queue",
            "Exports are decoded and validated before they enter the library. Originals are retained.",
        )
        self.jobs_table = QTableWidget(0, 5)
        self.jobs_table.setHorizontalHeaderLabels(
            ["Recording", "Preset / codec", "Status", "Progress", "Details"]
        )
        self.jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.jobs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.jobs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.jobs_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs_table.verticalHeader().hide()
        layout.addWidget(self.jobs_table, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(button("Cancel selected", self.cancel_job))
        buttons.addWidget(button("Retry selected", self.retry_job))
        buttons.addStretch()
        layout.addLayout(buttons)

    def settings_page(self):
        layout = self.page("Settings", "Capture, composition, audio, and local storage.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("page")
        form = QFormLayout(content)
        form.setSpacing(14)
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
        self.compression_preset = combo(
            [(n, n) for n in ["Fast", "Balanced", "Maximum Compression", "Telegram"]]
        )
        self.compression_preset.setCurrentText(self.library.setting("compression_preset", "Balanced"))
        self.compression_preset.currentTextChanged.connect(
            lambda v: self.library.set_setting("compression_preset", v)
        )
        form.addRow("Default export preset", self.compression_preset)
        self.layout_mode = combo([("Picture-in-picture", "pip"), ("Separate synchronized files", "separate")])
        form.addRow("Combined recording", self.layout_mode)
        self.position = combo(
            [
                (n.replace("-", " ").title(), n)
                for n in ["bottom-right", "bottom-left", "top-right", "top-left"]
            ]
        )
        form.addRow("Camera position", self.position)
        self.overlay_size = spin(10, 60, 25)
        self.overlay_size.setSuffix(" %")
        form.addRow("Camera width", self.overlay_size)
        self.opacity = spin(0, 100, 100)
        self.opacity.setSuffix(" %")
        form.addRow("Camera opacity", self.opacity)
        self.radius = spin(0, 120, 24)
        form.addRow("Rounded corners (output pixels)", self.radius)
        self.border = spin(0, 12, 2)
        form.addRow("Camera border (output pixels)", self.border)
        self.mirror = QCheckBox("Mirror the phone camera in recordings")
        form.addRow("Camera mirroring", self.mirror)
        self.disconnect_policy = combo(
            [
                ("Hide camera / black phone track", "hide"),
                ("Freeze last camera frame", "freeze"),
                ("Pause recording", "pause"),
                ("Stop and save recording", "stop"),
            ]
        )
        form.addRow("If phone disconnects", self.disconnect_policy)
        custom = QHBoxLayout()
        self.custom_width = spin(64, 7680, 1920)
        self.custom_height = spin(64, 4320, 1080)
        self.custom_width.setSingleStep(2)
        self.custom_height.setSingleStep(2)
        custom.addWidget(self.custom_width)
        custom.addWidget(label("×"))
        custom.addWidget(self.custom_height)
        form.addRow("Custom resolution", custom)
        self.cursor = QCheckBox("Include cursor (WGC; other backends use system policy)")
        self.cursor.setChecked(True)
        form.addRow("Cursor", self.cursor)
        self.fallback = QCheckBox("Use DXGI fallback on Windows")
        self.fallback.setEnabled(sys.platform == "win32")
        form.addRow("Windows capture", self.fallback)
        self.phone_audio = QCheckBox("Record phone microphone when shared by the phone")
        form.addRow("Phone audio", self.phone_audio)
        self.mic_enabled = QCheckBox("Record selected microphone")
        form.addRow("Microphone", self.mic_enabled)
        self.mic_device = QComboBox()
        self.system_device = QComboBox()
        self.audio_devices = QMediaDevices.audioInputs()
        for device in self.audio_devices:
            self.mic_device.addItem(device.description(), device)
            if sys.platform != "win32":
                self.system_device.addItem(device.description(), device)
        if sys.platform == "win32":
            try:
                for device in WasapiLoopback.devices():
                    self.system_device.addItem(device["name"], device)
            except Exception as exc:
                self.system_device.setToolTip(f"WASAPI device discovery failed: {exc}")
        form.addRow("Microphone device", self.mic_device)
        self.system_enabled = QCheckBox("Record a loopback / monitor input exposed by the OS")
        form.addRow("System audio", self.system_enabled)
        form.addRow("Loopback input device", self.system_device)
        for name in ["phone", "mic", "system"]:
            control = spin(0, 200, 100)
            control.setSuffix(" %")
            setattr(self, name + "_gain", control)
            form.addRow(name.title() + " gain", control)
        note = label(
            "System audio uses WASAPI speaker loopback on Windows or a monitor input on Linux. GPU load is not measured. Opaque surfaces are used when no supported compositor blur integration is available.",
            "muted",
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.capability_note = label("Detecting encoders…", "muted")
        self.capability_note.setWordWrap(True)
        form.addRow("Encoder diagnostics", self.capability_note)
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
        return RecordingConfig(
            mode=self.mode,
            layout=self.layout_mode.currentData(),
            fps=self.fps.currentData() if self.fps.currentData() > 0 else self.measured_fps,
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
        self.mode = mode
        self.preview_btn.setEnabled(mode != "phone")
        if mode == "phone" and self.desktop.active:
            self.desktop.stop()
            self.preview_btn.setText("Start screen preview")

    def toggle_preview(self):
        try:
            if self.desktop.active:
                self.desktop.stop()
                self.preview_btn.setText("Start screen preview")
            else:
                screen = self.monitor.currentData()
                if screen is None:
                    raise ValueError("No display is available")
                self.desktop.start(
                    screen,
                    self.monitor.currentIndex(),
                    self.cursor.isChecked(),
                    120 if self.measured_fps == 120 else 60,
                    self.fallback.isChecked(),
                )
                self.preview_btn.setText("Stop screen preview")
                if "portal" in backend_name():
                    self.on_message("status", "Select a screen in the system sharing dialog.")
        except Exception as exc:
            self.on_message("error", str(exc))

    def start_recording(self):
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
        except Exception as exc:
            self.microphone.stop()
            self.system_audio.stop()
            self.loopback.stop()
            self.on_message("error", str(exc))

    def pause_recording(self):
        try:
            if self.recorder.state == State.PAUSED:
                self.recorder.resume()
            else:
                self.recorder.pause()
        except ValueError as exc:
            self.on_message("error", str(exc))

    def stop_recording(self):
        self.recorder.stop()

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
        if self.server and self.server.thread and self.server.thread.is_alive():
            self.server.stop()
            self.server_btn.setEnabled(False)
            return
        address = self.networks.currentData()
        if not address:
            self.on_message("error", "No active private IPv4 network. Join a Wi-Fi network or hotspot first.")
            return
        self.server_btn.setEnabled(False)

        def start():
            try:
                if self.certificates is None:
                    self.certificates = Certificates(self.data_root / "certificates")
                self.server = PhoneServer(
                    self.sources,
                    self.certificates,
                    self.events.message.emit,
                    lambda: self.recorder.state.value,
                )
                self.server.start(address)
            except Exception as exc:
                self.events.message.emit("error", str(exc))

        threading.Thread(target=start, name="certificate-setup", daemon=True).start()

    def set_qr(self, url):
        image = qrcode.make(url).convert("RGB")
        data = io.BytesIO()
        image.save(data, "PNG")
        pix = QPixmap()
        pix.loadFromData(data.getvalue())
        self.qr_label.setPixmap(
            pix.scaled(260, 260, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        )

    def new_pairing(self):
        try:
            if self.server is None:
                raise ValueError("Start the phone server first")
            url = self.server.pairing_url()
            self.set_qr(url)
            self.pair_url.setText(
                "Scan to connect · expires in 2 minutes\n"
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
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.certificates.root)))
        else:
            self.on_message("status", "Start the phone server to create this desktop’s certificate identity.")

    def certificate_setup(self):
        if not self.server or self.server.state == "Offline":
            self.on_message("error", "Start the phone server before certificate setup.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Trust this desktop")
        dialog.resize(700, 650)
        layout = QVBoxLayout(dialog)
        warning = label(
            "Installing a root CA grants it authority to authenticate websites on your phone. Trust only your own desktop’s certificate and remove it when you stop using LumaCapture. Keep the CA private key on the desktop. A self-signed certificate is not automatically trusted by Safari."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        fingerprint = label("SHA-256 fingerprint\n" + self.certificates.fingerprint)
        fingerprint.setWordWrap(True)
        fingerprint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(fingerprint)
        instructions = label(
            "Safest offline setup: transfer LumaCapture-Root-CA.crt by USB and inspect its fingerprint.\n\niPhone: install the certificate profile in Settings → General → VPN & Device Management, then enable full trust in General → About → Certificate Trust Settings.\n\nAndroid: install it as a CA certificate in Settings → Security / Encryption & credentials. Device menu names vary.\n\nThe optional HTTP setup page serves only the public certificate and instructions for ten minutes. It cannot authenticate itself: compare the downloaded certificate’s fingerprint with the value above before trusting it. Then scan a fresh HTTPS pairing code."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        qr = label("")
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(qr)

        def enable():
            url = self.server.enable_bootstrap()
            data = io.BytesIO()
            qrcode.make(url).save(data, format="PNG")
            pix = QPixmap()
            pix.loadFromData(data.getvalue())
            qr.setPixmap(pix.scaled(220, 220))
            setup_url.setText(url)

        layout.addWidget(button("Enable certificate download for 10 minutes", enable))
        setup_url = label("")
        setup_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(setup_url)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
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
        sample = getattr(self.sources, "phone" if self.mode == "phone" else "screen").latest()
        if sample is None:
            self.on_message("error", "Start a live preview before measuring performance.")
            return
        self.benchmark_btn.setEnabled(False)
        cfg = self.config()
        height, width = sample.rgb.shape[:2]
        width, height = (cfg.width, cfg.height) if cfg.width else (width // 2 * 2, height // 2 * 2)
        encoder = cfg.encoder
        source_fps = getattr(self.sources, "phone" if self.mode == "phone" else "screen").fps()
        refresh = self.monitor.currentData().refreshRate() if self.monitor.currentData() else 30

        def measure():
            # Measure the actual frame conversion and encoding path with captured content.
            import av
            from fractions import Fraction
            from lumacapture.media.encode import encoder_options

            result = {"available": False, "error": ""}
            try:
                ctx = av.CodecContext.create(encoder, "w")
                ctx.width = width
                ctx.height = height
                ctx.pix_fmt = "yuv420p"
                ctx.time_base = Fraction(1, 120)
                ctx.framerate = Fraction(120)
                ctx.thread_count = 2
                ctx.options = encoder_options(encoder, preset="veryfast")
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
                stable = min(measured * 0.7, refresh if cfg.mode != "phone" else source_fps)
                result = {
                    "available": True,
                    "fps": measured,
                    "source_fps": source_fps,
                    "stable": max([f for f in (30, 60, 120) if f <= stable] or [30]),
                }
            except Exception as exc:
                result["error"] = str(exc)
            self.events.benchmark.emit(result)

        threading.Thread(target=measure, name="throughput-probe", daemon=True).start()

    def on_benchmark(self, result):
        self.benchmark_btn.setEnabled(True)
        if not result["available"]:
            self.on_message("error", result["error"])
            return
        self.measured_fps = result["stable"]
        if result["stable"] >= 120 and self.fps.findData(120) < 0:
            self.fps.addItem("120 FPS (measured capacity)", 120)
        self.on_message(
            "status",
            f"Measured {result['fps']:.1f} encode FPS; capture events {result['source_fps']:.1f}/s. Auto selects {self.measured_fps} FPS with headroom. Content and thermal load can change performance.",
        )

    def library_action(self, action):
        row = self.recordings.currentRow()
        if row < 0:
            return
        ident = self.recordings.item(row, 0).data(Qt.ItemDataRole.UserRole)
        item = self.library.get(ident)
        if not item:
            return
        path = Path(item["path"])
        try:
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
            self.navigate(3)

    def selected_job(self):
        row = self.jobs_table.currentRow()
        return self.jobs_table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None

    def cancel_job(self):
        ident = self.selected_job()
        if ident:
            self.compression.cancel(ident)

    def retry_job(self):
        ident = self.selected_job()
        if ident:
            self.compression.retry(ident)

    def choose_output(self):
        if self.recorder.state != State.IDLE:
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

    def on_message(self, kind, text):
        self.statusBar().showMessage(text)
        if kind == "server":
            self.new_pairing()
        if kind in {"saved", "compressed"}:
            self.last_table_signature = None
        if kind == "error":
            # Non-modal reporting keeps Stop available when a source fails.
            self.connection_detail.setText(text) if hasattr(self, "connection_detail") else None
            self.statusBar().setToolTip(text)

    def refresh_tables(self):
        items = self.library.recordings()
        jobs = self.library.jobs()
        signature = json.dumps([items, jobs], sort_keys=True)
        if signature == self.last_table_signature:
            return
        self.last_table_signature = signature
        selected_recording = self.recordings.item(self.recordings.currentRow(), 0)
        selected_id = selected_recording.data(Qt.ItemDataRole.UserRole) if selected_recording else None
        job_id = self.selected_job()
        self.recordings.setRowCount(len(items))
        for row, item in enumerate(items):
            info = item["metadata"]
            original = next((i for i in items if i["id"] == item["original"]), None)
            exported = next((i for i in items if i["original"] == item["id"]), None)
            values = [
                Path(item["path"]).name,
                duration(info["duration"]),
                f"{info['width']} × {info['height']}",
                f"{info.get('measured_fps', info['fps']):.1f}",
                filesize((original or item)["metadata"]["size"]),
                filesize(info["size"])
                if original
                else filesize(exported["metadata"]["size"])
                if exported
                else "—",
                item["status"],
                time.strftime("%b %d · %H:%M", time.localtime(item["created"])),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item["id"])
                    if item["thumbnail"]:
                        cell.setIcon(QIcon(item["thumbnail"]))
                self.recordings.setItem(row, col, cell)
            self.recordings.setRowHeight(row, 62)
            if item["id"] == selected_id:
                self.recordings.selectRow(row)
        self.library_note.setText(
            f"{len(items)} files indexed · {self.output}"
            if items
            else "No recordings yet. Start a recording from the dashboard."
        )
        self.jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            recording = next((i for i in items if i["id"] == job["recording_id"]), None)
            settings = json.loads(job["settings"])
            values = [
                Path(recording["path"]).name if recording else "Missing original",
                settings["codec"],
                job["status"],
                f"{job['progress'] * 100:.0f}%",
                job["error"],
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, job["id"])
                self.jobs_table.setItem(row, col, cell)
            if job["id"] == job_id:
                self.jobs_table.selectRow(row)

    def tick(self):
        state = self.recorder.state
        active = state in {State.RECORDING, State.PAUSED, State.STOPPING}
        self.state_label.setText(state.value)
        self.timer_label.setText(
            duration(self.recorder.clock.elapsed() if active and self.recorder.clock else 0)
        )
        self.start_btn.setEnabled(not active and not self.closing)
        self.pause_btn.setEnabled(state in {State.RECORDING, State.PAUSED})
        self.pause_btn.setText("Resume" if state == State.PAUSED else "Pause")
        self.stop_btn.setEnabled(state in {State.RECORDING, State.PAUSED})
        for widget in [
            self.monitor,
            self.resolution,
            self.fps,
            self.encoder,
            self.preview_btn,
            *self.mode_group.buttons(),
        ]:
            widget.setEnabled(not active)
        if self.mode == "phone":
            self.preview_btn.setEnabled(False)
        if self.last_state != state and state in {State.IDLE, State.ERROR}:
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
        screen, phone = self.sources.screen.latest(), self.sources.phone.latest()
        if phone and time.monotonic() - phone.timestamp > 3:
            phone = None
        source = phone if self.mode == "phone" else screen
        if source:
            size = (640, max(2, round(640 * source.rgb.shape[0] / source.rgb.shape[1])))
            scaled_screen = np.asarray(Image.fromarray(screen.rgb).resize(size)) if screen else None
            image = compose(scaled_screen, phone.rgb if phone else None, size, cfg)
            data = image.tobytes()
            self.preview.image = QImage(
                data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888
            ).copy()
        else:
            self.preview.image = QImage()
        self.preview.update()
        server_live = self.server and self.server.thread and self.server.thread.is_alive()
        self.server_btn.setEnabled(True)
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
            self.metrics_label.setText(
                f"Capture {self.sources.screen.fps():.0f} · Phone {self.sources.phone.fps():.0f} FPS   |   Encoded {metrics.get('encoded', 0) / max(0.1, elapsed):.1f} FPS   |   Skipped {metrics.get('skipped', 0)}   |   Encode {metrics.get('latency_ms', 0):.1f} ms   |   CPU {self.process.cpu_percent():.0f}%   |   RAM {self.process.memory_info().rss / 1024**2:.0f} MB   |   File {filesize(metrics.get('bytes', 0))}   |   Free {filesize(free)}"
            )
            self.refresh_tables()
            if server_live and self.server.address not in {i["address"] for i in interfaces()}:
                self.server.stop()
                self.on_message(
                    "error",
                    "The selected network address disappeared. Reconnect Wi-Fi, refresh networks, and start a new phone session.",
                )
        if self.closing:
            if active:
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
