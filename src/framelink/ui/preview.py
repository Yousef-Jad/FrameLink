"""Preview canvas used by Studio and the floating recording controls."""

from PySide6.QtCore import Qt, QSize, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget


def preview_rects(source_width, source_height, target, policy="fill"):
    """Map source pixels to Qt logical coordinates; QPainter applies device scale."""
    target = QRectF(target)
    if min(source_width, source_height, target.width(), target.height()) <= 0:
        return QRectF(), QRectF()
    source = QRectF(0, 0, source_width, source_height)
    if policy == "fill":
        ratio = max(target.width() / source_width, target.height() / source_height)
        width, height = target.width() / ratio, target.height() / ratio
        source = QRectF((source_width - width) / 2, (source_height - height) / 2, width, height)
    elif policy == "fit":
        ratio = min(target.width() / source_width, target.height() / source_height)
        width, height = source_width * ratio, source_height * ratio
        target = QRectF(
            target.x() + (target.width() - width) / 2,
            target.y() + (target.height() - height) / 2,
            width,
            height,
        )
    elif policy != "stretch":
        raise ValueError("Unknown preview policy")
    return source, target


class PreviewWidget(QWidget):
    fullscreenRequested = Signal()
    openWifiRequested = Signal()
    certificateSetupRequested = Signal()
    refreshPairingRequested = Signal()
    framePresented = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = QImage()
        self._frame_owner = None
        self._frame_sequence = None
        self.aspect_policy = "fill"
        self._title = "Your studio is ready"
        self._detail = "Choose a source to begin"
        self._frame_info = ""
        self._qr_pixmap = None
        self._qr_title = ""
        self._qr_detail = ""
        self._qr_url = ""
        self._qr_expired = False
        self._wifi_needed = False
        self._wifi_btn_rect = None
        self._cert_btn_rect = None
        self._refresh_btn_rect = None
        self._self_capture_hint = False

        self.setObjectName("preview-canvas")
        self.setMinimumSize(400, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Live recording preview. Double-click to enter fullscreen.")

    def set_frame(self, image: QImage, sequence=None, owner=None):
        self._frame_owner = owner
        self._frame = image if owner is not None and not image.isNull() else image.copy() if not image.isNull() else QImage()
        self._frame_sequence = sequence
        self.update()

    def current_frame(self):
        return self._frame.copy()

    def set_aspect_policy(self, policy):
        if policy not in {"fill", "fit", "stretch"}:
            raise ValueError("Unknown preview policy")
        self.aspect_policy = policy
        self.update()

    def set_empty_state(self, title: str, detail: str):
        self._title, self._detail = title, detail
        self._frame = QImage()
        self.update()

    def set_frame_info(self, text: str):
        self._frame_info = text
        self.update()

    def set_self_capture_hint(self, enabled: bool):
        if self._self_capture_hint != enabled:
            self._self_capture_hint = enabled
            self.update()

    def set_qr_card(
        self,
        pixmap: QPixmap = None,
        title: str = "",
        detail: str = "",
        url: str = "",
        expired: bool = False,
    ):
        self._qr_pixmap = pixmap
        self._qr_title = title
        self._qr_detail = detail
        self._qr_url = url
        self._qr_expired = expired
        self._wifi_needed = False
        self.update()

    def set_qr_expired(self, expired: bool = True):
        if self._qr_expired != expired:
            self._qr_expired = expired
            self.update()

    def clear_qr_card(self):
        self._qr_pixmap = None
        self._qr_title = ""
        self._qr_detail = ""
        self._qr_url = ""
        self._qr_expired = False
        self._cert_btn_rect = None
        self._refresh_btn_rect = None
        self.update()

    def set_wifi_assistant(self, active: bool = True):
        if self._wifi_needed == active:
            return
        self._wifi_needed = active
        if active:
            self._qr_pixmap = None
        self.update()

    def sizeHint(self):
        return QSize(960, 540)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.fullscreenRequested.emit()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if self._wifi_needed and self._wifi_btn_rect and self._wifi_btn_rect.contains(pos):
            self.openWifiRequested.emit()
            return
        if self._cert_btn_rect and self._cert_btn_rect.contains(pos):
            self.certificateSetupRequested.emit()
            return
        if self._qr_expired and self._refresh_btn_rect and self._refresh_btn_rect.contains(pos):
            self.refreshPairingRequested.emit()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background canvas
        painter.setBrush(QColor("#080A0E"))
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)

        has_frame = not self._frame.isNull()

        # 1. Draw live frame if available
        if has_frame:
            source, destination = preview_rects(
                self._frame.width(), self._frame.height(), self.rect(), self.aspect_policy
            )
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), 14, 14)
            painter.setClipPath(clip)
            painter.drawImage(destination, self._frame, source)
            painter.restore()
            if self._frame_sequence is not None:
                self.framePresented.emit(self._frame_sequence)

            # Top-right frame telemetry
            if self._frame_info:
                painter.setPen(QColor(242, 244, 248, 220))
                font = painter.font()
                font.setPointSize(9)
                font.setWeight(QFont.Weight.Medium)
                painter.setFont(font)
                painter.drawText(
                    self.rect().adjusted(16, 12, -16, -12),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                    self._frame_info,
                )

            # Self-capture hint banner
            if self._self_capture_hint:
                painter.setBrush(QColor(30, 41, 59, 210))
                painter.setPen(QPen(QColor(148, 163, 184, 180), 1))
                hint_rect = QRectF(16, 12, 340, 28)
                painter.drawRoundedRect(hint_rect, 6, 6)
                painter.setPen(QColor(226, 232, 240))
                hfont = painter.font()
                hfont.setPointSize(8)
                painter.setFont(hfont)
                painter.drawText(
                    hint_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    "Self-capture · Minimize or move window to prevent recursion",
                )

        # 2. Render Wi-Fi Setup Assistant if needed
        if self._wifi_needed:
            self._draw_wifi_assistant(painter)
            painter.end()
            return

        # 3. Render Inline / Overlay QR Card if present
        if self._qr_pixmap and not self._qr_pixmap.isNull():
            self._draw_qr_card(painter, overlay=has_frame)
            painter.end()
            return

        # 4. Fallback empty state
        if not has_frame:
            painter.setPen(QColor("#F2F4F8"))
            title_font = painter.font()
            title_font.setPointSize(16)
            title_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(title_font)
            painter.drawText(
                self.rect().adjusted(24, -14, -24, -14),
                Qt.AlignmentFlag.AlignCenter,
                self._title,
            )
            painter.setPen(QColor("#9299A8"))
            detail_font = painter.font()
            detail_font.setPointSize(10)
            detail_font.setWeight(QFont.Weight.Normal)
            painter.setFont(detail_font)
            painter.drawText(
                self.rect().adjusted(24, 40, -24, 40),
                Qt.AlignmentFlag.AlignCenter,
                self._detail,
            )

        painter.end()

    def _draw_qr_card(self, painter: QPainter, overlay: bool = False):
        canvas = self.rect()
        card_w, card_h = 320, 415
        if overlay:
            # Render as floating elevated card in top-right
            x = canvas.right() - card_w - 24
            y = canvas.top() + 24
            if x < canvas.left() + 20:
                x = canvas.left() + (canvas.width() - card_w) // 2
                y = canvas.top() + (canvas.height() - card_h) // 2
        else:
            # Centered prominently
            x = canvas.left() + (canvas.width() - card_w) // 2
            y = canvas.top() + (canvas.height() - card_h) // 2

        card_rect = QRectF(x, y, card_w, card_h)

        # Card shadow / backdrop
        painter.setBrush(QColor(15, 23, 42, 245 if overlay else 235))
        painter.setPen(QPen(QColor(56, 189, 248, 80 if overlay else 50), 1.5))
        painter.drawRoundedRect(card_rect, 16, 16)

        # Title
        painter.setPen(QColor("#F8FAFC"))
        tfont = painter.font()
        tfont.setPointSize(12)
        tfont.setWeight(QFont.Weight.Bold)
        painter.setFont(tfont)
        painter.drawText(
            QRectF(x + 16, y + 16, card_w - 32, 24),
            Qt.AlignmentFlag.AlignCenter,
            self._qr_title or "Pair Phone Camera",
        )

        # Subtitle
        painter.setPen(QColor("#94A3B8"))
        sfont = painter.font()
        sfont.setPointSize(9)
        sfont.setWeight(QFont.Weight.Normal)
        painter.setFont(sfont)
        painter.drawText(
            QRectF(x + 16, y + 40, card_w - 32, 20),
            Qt.AlignmentFlag.AlignCenter,
            "Scan with your phone's camera app",
        )

        # QR Code Container (White rounded card for maximum contrast)
        qr_size = 200
        qr_x = x + (card_w - qr_size) // 2
        qr_y = y + 64
        qr_bg_rect = QRectF(qr_x, qr_y, qr_size, qr_size)
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(qr_bg_rect, 12, 12)

        # QR Pixmap
        painter.drawPixmap(
            int(qr_x + 8),
            int(qr_y + 8),
            qr_size - 16,
            qr_size - 16,
            self._qr_pixmap,
        )

        if self._qr_expired:
            painter.setBrush(QColor(15, 23, 42, 225))
            painter.drawRoundedRect(qr_bg_rect, 12, 12)
            self._refresh_btn_rect = qr_bg_rect.toRect()
            exp_badge = QRectF(qr_x + 14, qr_y + 80, qr_size - 28, 40)
            painter.setBrush(QColor(239, 68, 68, 220))
            painter.setPen(QPen(QColor(248, 113, 113), 1))
            painter.drawRoundedRect(exp_badge, 8, 8)
            painter.setPen(QColor("#FFFFFF"))
            efont = painter.font()
            efont.setPointSize(8)
            efont.setWeight(QFont.Weight.Bold)
            painter.setFont(efont)
            painter.drawText(exp_badge, Qt.AlignmentFlag.AlignCenter, "QR Expired\nTap to Refresh")
        else:
            self._refresh_btn_rect = None

        # Bottom detail / status badge
        detail_text = self._qr_detail or "Awaiting connection over local Wi-Fi…"
        badge_rect = QRectF(x + 16, y + 276, card_w - 32, 28)
        painter.setBrush(QColor(30, 41, 59, 180))
        painter.setPen(QPen(QColor(51, 65, 85), 1))
        painter.drawRoundedRect(badge_rect, 8, 8)

        painter.setPen(QColor("#38BDF8"))
        bfont = painter.font()
        bfont.setPointSize(8)
        bfont.setWeight(QFont.Weight.Medium)
        painter.setFont(bfont)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, detail_text)

        # Certificate Setup button
        cert_rect = QRectF(x + 16, y + 312, card_w - 32, 32)
        self._cert_btn_rect = cert_rect.toRect()
        painter.setBrush(QColor(14, 165, 233, 25))
        painter.setPen(QPen(QColor(14, 165, 233, 120), 1))
        painter.drawRoundedRect(cert_rect, 8, 8)
        painter.setPen(QColor("#7DD3FC"))
        cfont = painter.font()
        cfont.setPointSize(8)
        cfont.setWeight(QFont.Weight.DemiBold)
        painter.setFont(cfont)
        painter.drawText(cert_rect, Qt.AlignmentFlag.AlignCenter, "🔒 iPhone setup: Trust certificate")

        # Hint
        painter.setPen(QColor("#64748B"))
        hfont = painter.font()
        hfont.setPointSize(8)
        painter.setFont(hfont)
        painter.drawText(
            QRectF(x + 16, y + 352, card_w - 32, 54),
            Qt.AlignmentFlag.AlignCenter,
            "Works with iPhone & Android camera app.\nLocal network only · No internet needed.",
        )

    def _draw_wifi_assistant(self, painter: QPainter):
        canvas = self.rect()
        card_w, card_h = 420, 330
        x = canvas.left() + (canvas.width() - card_w) // 2
        y = canvas.top() + (canvas.height() - card_h) // 2
        card_rect = QRectF(x, y, card_w, card_h)

        # Card body
        painter.setBrush(QColor(15, 23, 42, 240))
        painter.setPen(QPen(QColor(245, 158, 11, 100), 1.5))
        painter.drawRoundedRect(card_rect, 16, 16)

        # Title
        painter.setPen(QColor("#FEF3C7"))
        tfont = painter.font()
        tfont.setPointSize(14)
        tfont.setWeight(QFont.Weight.Bold)
        painter.setFont(tfont)
        painter.drawText(
            QRectF(x + 24, y + 20, card_w - 48, 28),
            Qt.AlignmentFlag.AlignCenter,
            "Wi-Fi Connection Required",
        )

        painter.setPen(QColor("#94A3B8"))
        sfont = painter.font()
        sfont.setPointSize(9)
        painter.setFont(sfont)
        painter.drawText(
            QRectF(x + 24, y + 48, card_w - 48, 20),
            Qt.AlignmentFlag.AlignCenter,
            "Connect computer and phone to the same Wi-Fi or Hotspot",
        )

        # Steps
        steps = [
            ("Step 1", "Turn on Personal Hotspot on your phone (iOS / Android)"),
            ("Step 2", "Connect this computer to your phone's Wi-Fi network"),
            ("Step 3", "The pairing QR code will appear here automatically"),
        ]
        sy = y + 80
        for num, desc in steps:
            # Number pill
            painter.setBrush(QColor(30, 41, 59))
            painter.setPen(QPen(QColor(245, 158, 11), 1))
            pill = QRectF(x + 32, sy, 54, 22)
            painter.drawRoundedRect(pill, 6, 6)
            painter.setPen(QColor("#FBBF24"))
            pfont = painter.font()
            pfont.setPointSize(8)
            pfont.setWeight(QFont.Weight.Bold)
            painter.setFont(pfont)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, num)

            # Description
            painter.setPen(QColor("#E2E8F0"))
            dfont = painter.font()
            dfont.setPointSize(9)
            dfont.setWeight(QFont.Weight.Normal)
            painter.setFont(dfont)
            painter.drawText(QRectF(x + 96, sy, card_w - 128, 22), Qt.AlignmentFlag.AlignVCenter, desc)
            sy += 36

        # Interactive "Open Wi-Fi" button
        btn_w, btn_h = 180, 38
        bx = x + (card_w - btn_w) // 2
        by = y + 230
        self._wifi_btn_rect = QRectF(bx, by, btn_w, btn_h).toRect()

        painter.setBrush(QColor(56, 189, 248))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(bx, by, btn_w, btn_h), 10, 10)

        painter.setPen(QColor("#082F49"))
        bfont = painter.font()
        bfont.setPointSize(10)
        bfont.setWeight(QFont.Weight.Bold)
        painter.setFont(bfont)
        painter.drawText(QRectF(bx, by, btn_w, btn_h), Qt.AlignmentFlag.AlignCenter, "Open Wi-Fi Settings")

        # Auto-detecting notice
        painter.setPen(QColor("#64748B"))
        nfont = painter.font()
        nfont.setPointSize(8)
        painter.setFont(nfont)
        painter.drawText(
            QRectF(x + 24, y + 280, card_w - 48, 20),
            Qt.AlignmentFlag.AlignCenter,
            "Scanning for local network connections…",
        )
