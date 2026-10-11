import numpy as np
from PIL import Image, ImageDraw, ImageOps


def fit(rgb, size):
    image = Image.fromarray(rgb)
    if image.size == size:
        return image
    return ImageOps.pad(image, size, method=Image.Resampling.BILINEAR, color=(12, 15, 20))


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
