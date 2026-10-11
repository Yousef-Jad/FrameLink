import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from framelink.storage.library import Library
from framelink.ui.components import SegmentedControl
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


def test_main_window_has_compact_four_destination_shell(qapp, window):
    assert window.rail.width() == 72
    assert window.pages.count() == 4
    assert [window.rail.button(i).toolTip() for i in range(4)] == [
        "Studio", "Library", "Devices", "Settings"
    ]


def test_mode_selector_changes_existing_recording_mode(qapp, window):
    window.mode_selector.set_current_value("phone")
    assert window.mode == "phone"
    assert window.config().mode == "phone"


def test_devices_connection_surface_is_separate_from_studio(qapp, window):
    assert window.connection_steps in window.devices_page_widget.findChildren(type(window.connection_steps))
    assert window.connection_steps not in window.studio_page_widget.findChildren(type(window.connection_steps))


def test_screenshot_tool_has_documented_page_targets():
    from tools.ui_screenshots import PAGE_NAMES

    assert PAGE_NAMES == ("studio", "devices", "library", "settings")
