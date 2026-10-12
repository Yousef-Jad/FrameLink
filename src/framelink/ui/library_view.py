"""Thumbnail-first recording library presentation."""

from pathlib import Path
import json
import time

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from framelink.ui.components import MaterialSurface
from framelink.ui.icons import symbol


def _duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def _size(value):
    if value >= 1000**3:
        return f"{value / 1000**3:.1f} GB"
    if value >= 1000**2:
        return f"{value / 1000**2:.1f} MB"
    if value >= 1000:
        return f"{value / 1000:.1f} KB"
    return f"{value:.0f} B"


class RecordingTile(MaterialSurface):
    actionRequested = Signal(str, str)
    activated = Signal(str)

    def __init__(self, item, jobs, parent=None):
        super().__init__("elevated", parent)
        self.item = item
        self.ident = item["id"]
        self.setObjectName("recording-tile")
        self.setMinimumWidth(260)
        self.setMaximumWidth(440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Double-click to play this recording")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.thumbnail = QLabel()
        self.thumbnail.setObjectName("recording-thumbnail")
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setMinimumHeight(130)
        self.thumbnail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        thumb = item.get("thumbnail")
        if thumb and Path(thumb).is_file():
            pixmap = QPixmap(thumb)
            self.thumbnail.setPixmap(
                pixmap.scaled(
                    360, 202, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
        else:
            self.thumbnail.setPixmap(symbol("studio", "#6F7787").pixmap(QSize(34, 34)))
        layout.addWidget(self.thumbnail)
        top = QHBoxLayout()
        self.title = QLabel(Path(item["path"]).stem)
        self.title.setObjectName("recording-title")
        self.title.setWordWrap(True)
        top.addWidget(self.title, 1)
        self.more = QToolButton()
        self.more.setObjectName("icon-button")
        self.more.setIcon(symbol("more"))
        self.more.setToolTip("Recording actions")
        self.more.setAccessibleName("Recording actions")
        self.more.clicked.connect(self._menu)
        top.addWidget(self.more)
        layout.addLayout(top)
        info = item["metadata"]
        self.metadata = QLabel(
            f"{_duration(info.get('duration', 0))}  ·  {info.get('width', 0)} × {info.get('height', 0)}  ·  {_size(info.get('size', 0))}"
        )
        self.metadata.setObjectName("muted")
        layout.addWidget(self.metadata)
        report = info.get("compression")
        if report:
            before, after = report["before_bytes"], report["after_bytes"]
            self.savings = QLabel(
                f"Before {_size(before)} → After {_size(after)}\n"
                + ("Export discarded · original retained · disk saved: 0 MB"
                   if report.get("export_discarded")
                   else f"Disk saved: {_size(report['saved_bytes'])} ({report['reduction_percent']:.1f}%)"
                   if not report["original_retained"]
                   else f"Original retained · disk saved: 0 MB\nExport reduction: {report['reduction_percent']:.1f}%")
            )
            self.savings.setWordWrap(True)
            self.savings.setObjectName("muted")
            self.savings.setToolTip(report["reason"])
            layout.addWidget(self.savings)
        created = time.strftime("%b %d, %Y", time.localtime(item["created"]))
        self.date = QLabel(created)
        self.date.setObjectName("muted")
        layout.addWidget(self.date)
        self.status = QLabel(self._status(jobs))
        self.status.setObjectName("status-pill")
        layout.addWidget(self.status)

    def _status(self, jobs):
        job = next((job for job in jobs if job["recording_id"] == self.ident), None)
        if job:
            settings = json.loads(job["settings"])
            if job["status"] == "running":
                return f"Compressing · {job['progress'] * 100:.0f}% · {settings.get('codec', 'H.264')}"
            return f"{job['status'].title()} · {settings.get('codec', 'H.264')}"
        return self.item.get("status", "Original")

    def _menu(self):
        menu = QMenu(self)
        for text, action in [
            ("Play", "play"),
            ("Open folder", "folder"),
            ("Rename", "rename"),
            ("Compress again", "compress"),
            ("Export", "export"),
            ("Delete", "delete"),
        ]:
            menu.addAction(
                text, lambda checked=False, selected=action: self.actionRequested.emit(self.ident, selected)
            )
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.ident)
        super().mouseDoubleClickEvent(event)


class LibraryView(QWidget):
    recordingActivated = Signal(str)
    actionRequested = Signal(str, str)
    jobActionRequested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._jobs = []
        self._tiles = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search recordings")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        controls.addWidget(self.search, 1)
        self.sort = QComboBox()
        for label, value in [
            ("Newest", "newest"),
            ("Oldest", "oldest"),
            ("Longest", "duration"),
            ("Largest", "size"),
        ]:
            self.sort.addItem(label, value)
        self.sort.currentIndexChanged.connect(self._render)
        controls.addWidget(self.sort)
        self.queue_status = QLabel()
        self.queue_status.setObjectName("muted")
        controls.addWidget(self.queue_status)
        root.addLayout(controls)
        self.jobs_strip = QFrame()
        self.jobs_strip.setObjectName("library-jobs")
        self.jobs_layout = QHBoxLayout(self.jobs_strip)
        self.jobs_layout.setContentsMargins(12, 8, 12, 8)
        root.addWidget(self.jobs_strip)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 4, 0, 12)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        self.empty_state = QWidget(self.content)
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel("Your recordings live here")
        empty_title.setObjectName("title")
        empty_layout.addWidget(empty_title, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_detail = QLabel("Finished recordings and validated exports stay on this computer.")
        empty_detail.setObjectName("muted")
        empty_detail.setWordWrap(True)
        empty_layout.addWidget(empty_detail, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_state_button = QPushButton("Go to Studio")
        empty_layout.addWidget(self.empty_state_button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_state.hide()

    def set_empty_action(self, callback):
        self.empty_state_button.clicked.connect(callback)

    def refresh(self, items, jobs):
        self._items, self._jobs = list(items), list(jobs)
        self._render()

    def _render(self):
        for tile in self._tiles.values():
            tile.deleteLater()
        self._tiles.clear()
        while self.grid.count():
            self.grid.takeAt(0)
        query = self.search.text().strip().lower()
        items = [item for item in self._items if not query or query in Path(item["path"]).name.lower()]
        sort_key = self.sort.currentData()
        if sort_key == "oldest":
            items.sort(key=lambda item: item["created"])
        elif sort_key == "duration":
            items.sort(key=lambda item: item["metadata"].get("duration", 0), reverse=True)
        elif sort_key == "size":
            items.sort(key=lambda item: item["metadata"].get("size", 0), reverse=True)
        else:
            items.sort(key=lambda item: item["created"], reverse=True)
        self.empty_state.setVisible(not items)
        columns = max(1, self.width() // 300)
        for index, item in enumerate(items):
            tile = RecordingTile(item, self._jobs, self.content)
            tile.activated.connect(self.recordingActivated)
            tile.actionRequested.connect(self.actionRequested)
            self._tiles[item["id"]] = tile
            self.grid.addWidget(tile, index // columns, index % columns)
        self.queue_status.setText(
            f"{len(self._jobs)} compression job{'s' if len(self._jobs) != 1 else ''}"
            if self._jobs
            else f"{len(self._items)} recording{'s' if len(self._items) != 1 else ''}"
        )
        self._render_jobs()

    def _render_jobs(self):
        while self.jobs_layout.count():
            item = self.jobs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._jobs:
            self.jobs_strip.hide()
            return
        self.jobs_strip.show()
        for job in self._jobs[:3]:
            status = QLabel(f"Compression {job['status']} · {job['progress'] * 100:.0f}%")
            status.setObjectName("muted")
            self.jobs_layout.addWidget(status)
            if job["status"] in {"queued", "running"}:
                action = QPushButton("Cancel")
                action.clicked.connect(
                    lambda checked=False, ident=job["id"]: self.jobActionRequested.emit(ident, "cancel")
                )
            else:
                action = QPushButton("Retry")
                action.clicked.connect(
                    lambda checked=False, ident=job["id"]: self.jobActionRequested.emit(ident, "retry")
                )
            self.jobs_layout.addWidget(action)
        self.jobs_layout.addStretch()

    def item_count(self):
        return len(self._tiles)

    def item_for(self, ident):
        return self._tiles[ident]

    def selected_id(self):
        return next(iter(self._tiles), None)

    def selected_job_id(self):
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._items:
            self._render()
