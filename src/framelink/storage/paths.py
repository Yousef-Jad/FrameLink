from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths


def default_recording_directory(platform=None):
    """Use Windows' actual Desktop, including redirected or OneDrive folders."""
    if (platform or sys.platform) == "win32":
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        return Path(desktop) if desktop else Path.home() / "Desktop"
    return Path.home() / "Videos" / "FrameLink"
