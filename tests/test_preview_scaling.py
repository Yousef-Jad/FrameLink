import pytest
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from framelink.ui.preview import PreviewWidget, preview_rects
from framelink.media.buffers import Sources


def test_preview_buffer_is_independent_from_recording_history():
    sources = Sources()
    sources.phone.put("recording-frame-1", 1.0)
    sources.phone_preview.put("preview-frame-2", 2.0)
    assert sources.phone.latest().rgb == "recording-frame-1"
    assert sources.phone_preview.latest().rgb == "preview-frame-2"


@pytest.mark.parametrize("source", [(1080, 1920), (1920, 1080), (640, 480)])
@pytest.mark.parametrize("target", [(960, 540), (540, 960), (1280, 720)])
def test_fill_crop_covers_target_without_distortion(source, target):
    src, dst = preview_rects(*source, QRectF(0, 0, *target))
    assert dst == QRectF(0, 0, *target)
    assert src.width() / src.height() == pytest.approx(target[0] / target[1])
    assert src.center() == QRectF(0, 0, *source).center()
    assert src.left() >= 0 and src.top() >= 0
    assert src.right() <= source[0] and src.bottom() <= source[1]


def test_fit_and_explicit_stretch():
    src, dst = preview_rects(1080, 1920, QRectF(0, 0, 960, 540), "fit")
    assert src == QRectF(0, 0, 1080, 1920)
    assert dst.height() == 540 and dst.width() < 960
    src, dst = preview_rects(1080, 1920, QRectF(0, 0, 960, 540), "stretch")
    assert src == QRectF(0, 0, 1080, 1920) and dst == QRectF(0, 0, 960, 540)


def test_rendered_fill_resize_rotation_and_hidpi(tmp_path):
    app = QApplication.instance() or QApplication([])
    preview = PreviewWidget()
    preview.show()
    for shape in [(1080, 1920), (1920, 1080)]:
        image = QImage(*shape, QImage.Format.Format_RGB888)
        image.fill(QColor("#d64a34"))
        image.setDevicePixelRatio(2)  # Frame dimensions stay source pixel dimensions.
        preview.set_frame(image)
        for dimensions in [(960, 540), (540, 960), (1280, 720)]:
            preview.resize(*dimensions)
            app.processEvents()
            rendered = preview.grab().toImage()
            ratio = rendered.devicePixelRatio()
            for x, y in [
                (20, 20),
                (dimensions[0] - 20, 20),
                (20, dimensions[1] - 20),
                (dimensions[0] - 20, dimensions[1] - 20),
            ]:
                assert rendered.pixelColor(round(x * ratio), round(y * ratio)).name() == "#d64a34"
            assert preview.current_frame().size() == image.size()
    preview.close()


def test_preview_emits_presentation_after_paint():
    app = QApplication.instance() or QApplication([])
    preview = PreviewWidget()
    preview.resize(320, 180)
    frame = QImage(640, 360, QImage.Format.Format_RGB888)
    spy = QSignalSpy(preview.framePresented)
    preview.set_frame(frame, sequence=42)
    preview.show()
    preview.grab()
    app.processEvents()
    assert spy.count() >= 1
    assert spy.at(0)[0] == 42
    preview.close()


def test_preview_can_retain_frame_owner_without_copying_before_paint():
    QApplication.instance() or QApplication([])
    preview = PreviewWidget()
    owner = np.zeros((180, 320, 3), np.uint8)
    image = QImage(owner.data, 320, 180, 960, QImage.Format.Format_RGB888)
    preview.set_frame(image, sequence=7, owner=owner)
    assert preview._frame_owner is owner
    preview.close()
