"""Conservative replacement gate; perceptual samples are not a losslessness claim."""

from itertools import zip_longest
import math

import av
import numpy as np


def frame_quality(reference, candidate):
    """Full-resolution luma block SSIM and PSNR, including sharp-edge pixels."""
    a = reference.astype(np.float32)
    b = candidate.astype(np.float32)
    error = (a - b) ** 2

    def psnr(mse):
        return min(100.0, 10 * math.log10(255**2 / max(float(mse), 1e-8)))

    edge = np.zeros(a.shape, dtype=bool)
    edge[1:, :] |= np.abs(np.diff(a, axis=0)) > 12
    edge[:, 1:] |= np.abs(np.diff(a, axis=1)) > 12
    h, w = a.shape[0] // 8 * 8, a.shape[1] // 8 * 8
    blocks = []
    for image in (a, b):
        blocks.append(image[:h, :w].reshape(h // 8, 8, w // 8, 8).transpose(0, 2, 1, 3))
    x, y = blocks
    mx, my = x.mean(axis=(2, 3)), y.mean(axis=(2, 3))
    vx, vy = x.var(axis=(2, 3)), y.var(axis=(2, 3))
    cov = (x * y).mean(axis=(2, 3)) - mx * my
    ssim = ((2 * mx * my + 6.5025) * (2 * cov + 58.5225)) / (
        (mx**2 + my**2 + 6.5025) * (vx + vy + 58.5225)
    )
    return {
        "ssim": float(ssim.mean()),
        "psnr_db": psnr(error.mean()),
        "edge_psnr_db": psnr(error[edge].mean()) if edge.any() else psnr(error.mean()),
    }


def compare_quality(source, candidate, source_info, output_info, checkpoint=lambda: None):
    """Decode every video frame, compare timestamps, sample up to 32 image pairs.

    Refuse destructive replacement when any frame, dimension or timing differs.
    Resized / reduced-FPS exports remain available, with their original retained.
    """
    result = {"passed": False, "samples": 0, "reason": ""}
    for key in ("width", "height", "decoded_frames", "audio"):
        if source_info.get(key) != output_info.get(key):
            result["reason"] = f"Source preservation check failed: {key}"
            return result
    if abs(source_info["duration"] - output_info["duration"]) > 0.1:
        result["reason"] = "Duration differs by more than 100 ms"
        return result
    if source_info["audio"]:
        for edge in ("start", "end"):
            key = f"audio_{edge}"
            offset = source_info[key] - source_info[f"video_{edge}"]
            exported_offset = output_info[key] - output_info[f"video_{edge}"]
            if abs(offset - exported_offset) > 0.05:
                result["reason"] = "Audio/video alignment changed by more than 50 ms"
                return result
    count = source_info["decoded_frames"]
    selected = set(np.linspace(0, count - 1, min(32, count), dtype=int))
    scores = []
    with av.open(str(source)) as original, av.open(str(candidate)) as exported:
        first_a = first_b = None
        for index, (a, b) in enumerate(zip_longest(original.decode(video=0), exported.decode(video=0))):
            checkpoint()
            if a is None or b is None:
                result["reason"] = "Decoded frame counts differ"
                return result
            if a.time is None or b.time is None or a.is_corrupt or b.is_corrupt:
                result["reason"] = "Missing timestamps or damaged frames"
                return result
            if first_a is None:
                first_a, first_b = float(a.time), float(b.time)
            if abs((float(a.time) - first_a) - (float(b.time) - first_b)) > 0.04:
                result["reason"] = "Frame pacing differs by more than 40 ms"
                return result
            if index in selected:
                # Read native luma rather than introducing RGB conversion errors.
                def luma(frame):
                    frame = frame.reformat(format="yuv420p")
                    plane = frame.planes[0]
                    return np.frombuffer(plane, np.uint8).reshape(frame.height, plane.line_size)[:, :frame.width]

                scores.append(frame_quality(luma(a), luma(b)))
    if not scores:
        result["reason"] = "No quality samples"
        return result
    result.update(
        samples=len(scores),
        mean_ssim=float(np.mean([s["ssim"] for s in scores])),
        min_ssim=min(s["ssim"] for s in scores),
        mean_psnr_db=float(np.mean([s["psnr_db"] for s in scores])),
        min_edge_psnr_db=min(s["edge_psnr_db"] for s in scores),
    )
    result["passed"] = (
        result["mean_ssim"] >= 0.98 and result["min_ssim"] >= 0.95
        and result["mean_psnr_db"] >= 38 and result["min_edge_psnr_db"] >= 35
    )
    result["reason"] = "Quality gate passed" if result["passed"] else "Quality below conservative replacement thresholds"
    return result
