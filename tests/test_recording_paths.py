from pathlib import Path

from PySide6.QtCore import QStandardPaths

from framelink.storage.paths import default_recording_directory


def test_windows_uses_redirected_desktop(monkeypatch, tmp_path):
    redirected = tmp_path / "OneDrive" / "Desktop"
    requested = []

    def location(kind):
        requested.append(kind)
        return str(redirected)

    monkeypatch.setattr(QStandardPaths, "writableLocation", location)
    assert default_recording_directory("win32") == redirected
    assert requested == [QStandardPaths.StandardLocation.DesktopLocation]


def test_linux_retains_videos_default():
    assert default_recording_directory("linux") == Path.home() / "Videos" / "FrameLink"
