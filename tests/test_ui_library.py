import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PIL import Image

from framelink.media.encode import inspect_media
from framelink.storage.library import Library
from framelink.ui.library_view import LibraryView


def test_library_empty_state_has_next_action():
    QApplication.instance() or QApplication([])
    view = LibraryView()
    view.show()
    view.refresh([], [])
    assert view.empty_state.isVisible()
    assert view.empty_state_button.text() == "Go to Studio"


def test_library_renders_real_thumbnail_and_compression_status(media_file, tmp_path):
    QApplication.instance() or QApplication([])
    library = Library(tmp_path / "library")
    thumbnail = tmp_path / "thumbnail.jpg"
    Image.new("RGB", (320, 180), "#AFC5FF").save(thumbnail)
    ident = library.add(media_file, inspect_media(media_file), thumbnail=thumbnail)
    library.enqueue(ident, {"codec": "libx264"})
    view = LibraryView()
    view.show()
    view.refresh(library.recordings(), library.jobs())
    assert view.item_count() == 1
    assert not view.item_for(ident).thumbnail.pixmap().isNull()
    assert "queued" in view.item_for(ident).status.text().lower()
