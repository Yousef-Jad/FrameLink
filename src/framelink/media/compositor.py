import numpy as np
from PIL import Image, ImageDraw, ImageOps


def fit(rgb, size):
    image = Image.fromarray(rgb)
    if image.size == size:
        return image
    image.thumbnail(size, Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", size, (12, 15, 20))
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def compose(screen, phone, size, config):
    if config.mode == "phone":
        image = fit(phone, size) if phone is not None else Image.new("RGB", size, (12, 15, 20))
        return ImageOps.mirror(image) if config.mirror else image
    image = fit(screen, size)
    if config.mode != "both" or phone is None:
        return image
    width = max(2, int(size[0] * config.overlay_size))
    height = min(int(size[1] * 0.8), max(2, round(width * phone.shape[0] / phone.shape[1])))
    overlay = fit(phone, (width, height))
    if config.mirror:
        overlay = ImageOps.mirror(overlay)
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1), config.radius, fill=round(255 * config.opacity)
    )
    margin = max(12, round(size[0] * 0.02))
    x = margin if "left" in config.overlay_position else size[0] - width - margin
    y = margin if "top" in config.overlay_position else size[1] - height - margin
    image.paste(overlay, (x, y), mask)
    if config.border:
        ImageDraw.Draw(image).rounded_rectangle(
            (x, y, x + width - 1, y + height - 1), config.radius, outline=(226, 232, 240), width=config.border
        )
    return image


def as_rgb(image):
    return np.asarray(image)


def resize_rgb(rgb, size):
    """Fast nearest-neighbour resize for the live composition path."""
    width, height = size
    if rgb.shape[1] == width and rgb.shape[0] == height:
        return np.ascontiguousarray(rgb)
    ys = np.minimum(rgb.shape[0] - 1, (np.arange(height) * rgb.shape[0] // height))
    xs = np.minimum(rgb.shape[1] - 1, (np.arange(width) * rgb.shape[1] // width))
    return np.ascontiguousarray(rgb[ys[:, None], xs[None, :]])


def rounded_alpha(width, height, radius, opacity):
    yy, xx = np.ogrid[:height, :width]
    radius = max(0, min(radius, min(width, height) // 2))
    inside = np.ones((height, width), dtype=bool)
    if radius:
        corners = (
            ((xx < radius) & (yy < radius) & ((xx - radius) ** 2 + (yy - radius) ** 2 > radius**2))
            | ((xx >= width - radius) & (yy < radius) & ((xx - (width - radius - 1)) ** 2 + (yy - radius) ** 2 > radius**2))
            | ((xx < radius) & (yy >= height - radius) & ((xx - radius) ** 2 + (yy - (height - radius - 1)) ** 2 > radius**2))
            | ((xx >= width - radius) & (yy >= height - radius) & ((xx - (width - radius - 1)) ** 2 + (yy - (height - radius - 1)) ** 2 > radius**2))
        )
        inside &= ~corners
    return inside.astype(np.float32)[:, :, None] * opacity


def compose_rgb(screen, phone, size, config):
    """Compose the common native combined path without round-tripping the full frame through Pillow."""
    if config.mode != "both" or screen is None or screen.shape[:2] != (size[1], size[0]):
        return as_rgb(compose(screen, phone, size, config))
    canvas = np.ascontiguousarray(screen).copy()
    if phone is None:
        return canvas
    width = max(2, int(size[0] * config.overlay_size))
    height = min(int(size[1] * 0.8), max(2, round(width * phone.shape[0] / phone.shape[1])))
    overlay = resize_rgb(phone, (width, height))
    if config.mirror:
        overlay = overlay[:, ::-1]
    margin = max(12, round(size[0] * 0.02))
    x = margin if "left" in config.overlay_position else size[0] - width - margin
    y = margin if "top" in config.overlay_position else size[1] - height - margin
    region = canvas[y : y + height, x : x + width]
    if config.radius or config.opacity < 1:
        alpha = rounded_alpha(width, height, config.radius, config.opacity)
        region[:] = (region * (1 - alpha) + overlay * alpha).astype(np.uint8)
    else:
        region[:] = overlay
    if config.border:
        border = max(1, config.border)
        region[:border] = (226, 232, 240)
        region[-border:] = (226, 232, 240)
        region[:, :border] = (226, 232, 240)
        region[:, -border:] = (226, 232, 240)
    return canvas
