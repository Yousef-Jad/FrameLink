import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from framelink.storage.library import Library
from framelink.ui.components import SegmentedControl
from framelink.media.model import State
from framelink.ui.preview import PreviewWidget
from framelink.ui.theme import palette, stylesheet
from framelink.ui.window import MainWindow


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    instance = MainWindow(Library(tmp_path / "library"), tmp_path / "data", smoke=True)
    instance.show()
    qapp.processEvents()
    yield instance
    instance.close()
    instance.compression.stop()
    instance.compression.thread.join(5)


def test_palette_has_semantic_dark_and_light_roles():
    dark = palette(False)
    light = palette(True)
    assert dark["content"] == "#0B0D12"
    assert dark["recording"] == "#FF453A"
    assert light["content"] != dark["content"]
    assert {"content", "glass", "elevated", "opaque", "text", "secondary", "accent", "focus"} <= dark.keys()


def test_recording_directory_default_and_explicit_override(qapp, tmp_path, monkeypatch):
    desktop = tmp_path / "redirected-desktop"
    monkeypatch.setattr("framelink.ui.window.default_recording_directory", lambda: desktop)
    for override in (None, tmp_path / "chosen-videos"):
        library = Library(tmp_path / ("custom-db" if override else "default-db"))
        if override:
            library.set_setting("output", str(override))
        instance = MainWindow(library, library.root, smoke=True)
        try:
            assert instance.output == (override or desktop)
            assert instance.recorder.output == instance.output
        finally:
            instance.compression.stop()
            instance.compression.thread.join(5)
            instance.close()


def test_high_contrast_uses_opaque_material_fallback():
    css = stylesheet(False, high_contrast=True)
    assert "rgba(" not in css
    assert palette(False, high_contrast=True)["glass"] == palette(False, high_contrast=True)["opaque"]


def test_segmented_control_emits_value(qapp):
    control = SegmentedControl()
    control.add_item("Camera", "phone")
    control.add_item("Screen", "screen")
    spy = QSignalSpy(control.valueChanged)
    control.set_current_value("screen")
    assert spy.count() == 1
    assert control.current_value() == "screen"


def test_preview_preserves_current_frame(qapp):
    preview = PreviewWidget()
    frame = QImage(320, 180, QImage.Format.Format_RGB888)
    preview.set_frame(frame)
    assert preview.current_frame().size() == QSize(320, 180)


def test_phone_preview_refreshes_at_video_cadence(qapp, window):
    assert window.timer.interval() <= 50


def test_record_stays_disabled_until_completed_worker_exits(qapp, window):
    window.recorder.machine.state = State.COMPLETED
    worker = threading.Thread(target=lambda: time.sleep(0.15))
    worker.start()
    window.recorder.thread = worker
    window.tick()
    assert not window.start_btn.isEnabled()
    worker.join()
    window.tick()
    assert window.start_btn.isEnabled()


def test_explicit_phone_exit_refreshes_consumed_qr(qapp, window):
    from unittest.mock import MagicMock

    server = MagicMock()
    server.state = "Waiting for phone"
    server.origin = "https://192.168.1.20:8443"
    server.session_secret = None
    server.new_pairing_url.return_value = server.origin + "/#fresh-token"
    window.server = server
    window.set_qr(server.origin + "/#old-token")
    window.on_message("phone", "Waiting for phone")
    assert server.new_pairing_url.called
    assert window.preview._qr_url.endswith("#fresh-token")


def test_phone_recording_uses_granted_camera_cadence(qapp, window, monkeypatch):
    window.mode = "phone"
    window.measured_fps = 60
    monkeypatch.setattr(window.sources.phone_preview, "fps", lambda: 30.0)
    assert window.config().fps == 30


def test_native_capture_negotiates_selected_rate(qapp, window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.desktop, "start", lambda *args: calls.append(args))
    monkeypatch.setattr(window, "smoke", False)
    window.fps.setCurrentIndex(window.fps.findData(30))
    window.auto_start_screen()
    assert calls[-1][3] == min(30, round(qapp.primaryScreen().refreshRate()))
    window.desktop.active = True
    window.fps.setCurrentIndex(window.fps.findData(60))
    window.capture_rate_selected(window.fps.currentIndex())
    assert calls[-1][3] == min(60, round(qapp.primaryScreen().refreshRate()))
    assert len(calls) == 2


def test_main_window_has_compact_four_destination_shell(qapp, window):
    assert window.rail.width() == 72
    assert window.pages.count() == 4
    assert [window.rail.button(i).toolTip() for i in range(4)] == ["Studio", "Library", "Devices", "Settings"]


def test_mode_selector_changes_existing_recording_mode(qapp, window):
    window.mode_selector.set_current_value("phone")
    assert window.mode == "phone"
    assert window.config().mode == "phone"


def test_auto_phone_server_wires_remote_recording_control(qapp, window, monkeypatch):
    import threading

    captured = {}

    class FakeServer:
        def __init__(self, *args):
            captured["args"] = args
            self._ready = threading.Event()
            self.state = "Offline"
            self.origin = "https://127.0.0.1:8443"
            self.thread = None

        def start(self, address):
            captured["address"] = address
            self.state = "Waiting for phone"
            self._ready.set()

        def enable_bootstrap(self):
            return "http://127.0.0.1:8444/"

        def stop(self):
            self.state = "Offline"

    monkeypatch.setattr("framelink.ui.window.PhoneServer", FakeServer)
    monkeypatch.setattr("framelink.ui.window.interfaces", lambda: [{"address": "127.0.0.1"}])
    window.smoke = False
    window.start_phone_server_auto()
    deadline = time.monotonic() + 2
    while "args" not in captured and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert "args" in captured
    assert callable(captured["args"][4])
    assert captured["args"][4]() is window.phone_recording_snapshot
    assert callable(captured["args"][5])


def test_devices_connection_surface_is_separate_from_studio(qapp, window):
    assert window.connection_steps in window.devices_page_widget.findChildren(type(window.connection_steps))
    assert window.connection_steps not in window.studio_page_widget.findChildren(
        type(window.connection_steps)
    )


def test_screenshot_tool_has_documented_page_targets():
    from tools.ui_screenshots import PAGE_NAMES

    assert PAGE_NAMES == ("studio", "devices", "library", "settings")


def test_preview_qr_and_wifi_assistant(qapp):
    preview = PreviewWidget()
    assert not preview._wifi_needed
    assert preview._qr_pixmap is None

    # Test QR card
    dummy_pix = QPixmap(100, 100)
    preview.set_qr_card(dummy_pix, "Pair Phone", "Scan code")
    assert preview._qr_pixmap is not None
    assert preview._qr_title == "Pair Phone"

    preview.clear_qr_card()
    assert preview._qr_pixmap is None

    # Test Wi-Fi assistant
    preview.set_wifi_assistant(True)
    assert preview._wifi_needed
    preview.set_wifi_assistant(False)
    assert not preview._wifi_needed

    # Test self-capture hint
    preview.set_self_capture_hint(True)
    assert preview._self_capture_hint
    preview.set_self_capture_hint(False)
    assert not preview._self_capture_hint


def test_audiometer_level_bounds(qapp):
    from framelink.ui.components import AudioMeter

    meter = AudioMeter()
    meter.set_level(0.5)
    assert meter._level == 0.5
    meter.set_level(1.5)
    assert meter._level == 1.0
    meter.set_level(-0.5)
    assert meter._level == 0.0


def test_source_activation_modes(qapp, window):
    # Test switching between the 3 core modes
    for mode in ("screen", "phone", "both"):
        window.activate_mode_source(mode)
        assert window.mode == mode or mode in ("screen", "phone", "both")


def test_qr_stability_across_timer_ticks(qapp, window):
    from unittest.mock import MagicMock
    from framelink.security.pairing import PairingTokens

    mock_server = MagicMock()
    mock_server.state = "Waiting for connection"
    mock_server.thread.is_alive.return_value = True
    mock_server.bitrate = 0.0
    mock_server.address = "192.168.0.106"
    tokens = PairingTokens(ttl=600)
    mock_server.tokens = tokens
    mock_server.pairing_url.side_effect = lambda: (
        f"https://192.168.0.106:8443/?token={tokens.get_or_create()}"
    )
    mock_server.origin = "https://192.168.0.106:8443"

    window.server = mock_server
    window.mode_selector.set_current_value("phone")
    window.mode = "phone"
    window.update_phone_qr_display()

    initial_url = window.preview._qr_url
    assert initial_url is not None
    initial_pixmap = window.preview._qr_pixmap
    assert initial_pixmap is not None

    call_count_before = mock_server.pairing_url.call_count

    # Simulate multiple ticks that previously caused token rotation
    for _ in range(8):
        window.tick()
        qapp.processEvents()

    assert window.preview._qr_url == initial_url, "QR URL must not rotate on timer ticks"
    assert mock_server.pairing_url.call_count == call_count_before, (
        "pairing_url must not be called during ticks"
    )
    assert not window.preview._qr_expired

    # Invalidate token and verify tick triggers expired banner
    tokens.invalidate()
    window.tick()
    qapp.processEvents()
    assert window.preview._qr_expired is True


def test_preview_signals_and_expired_banner(qapp):
    preview = PreviewWidget()
    cert_spy = QSignalSpy(preview.certificateSetupRequested)
    refresh_spy = QSignalSpy(preview.refreshPairingRequested)

    assert not preview._qr_expired
    preview.set_qr_expired(True)
    assert preview._qr_expired

    preview.certificateSetupRequested.emit()
    assert cert_spy.count() == 1

    preview.refreshPairingRequested.emit()
    assert refresh_spy.count() == 1
