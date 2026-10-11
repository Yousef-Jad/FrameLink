"""Resolution-independent, consistently weighted studio symbols."""

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "studio": '<rect x="3" y="5" width="18" height="14" rx="3"/><circle cx="12" cy="12" r="3"/>',
    "library": '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M7 3h10M5 5h14m-4 6 4 3-4 3z"/>',
    "devices": '<rect x="7" y="2" width="10" height="20" rx="3"/><path d="M10 5h4m-3 14h2"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="9" cy="18" r="2"/>',
    "record": '<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
    "pause": '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
    "play": '<path d="m8 5 11 7-11 7z"/>',
    "audio": '<path d="m3 9 4 0 5-4v14l-5-4H3zm13-1a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    "microphone": '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M6 10v2a6 6 0 0 0 12 0v-2m-6 8v4m-4 0h8"/>',
    "fullscreen": '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "screen": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    "camera": '<rect x="2" y="6" width="15" height="13" rx="3"/><path d="m17 10 5-3v11l-5-3M6 6l2-3h5l2 3"/>',
    "chevron": '<path d="m8 5 7 7-7 7"/>',
}


def symbol(name, color="#C0C7D5"):
    name = "settings" if name == "settings-gear" else name
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" color="{color}" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    pixmap = QPixmap(32, 32)
    pixmap.fill(0)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(2, 2, 28, 28))
    painter.end()
    return QIcon(pixmap)
