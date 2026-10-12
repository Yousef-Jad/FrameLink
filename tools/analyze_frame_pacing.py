"""Decode real media and measure presentation gaps and consecutive duplicate content."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import av
import numpy as np


def analyze(path):
    stamps, hashes = [], []
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            stamps.append(float(frame.pts * frame.time_base))
            # Hash decoded luma, not packet bytes; timestamps/codec metadata are not motion.
            gray = frame.to_ndarray(format="gray")
            hashes.append(hashlib.sha256(gray).digest())
    gaps = np.diff(stamps) * 1000
    repeats = sum(a == b for a, b in zip(hashes, hashes[1:]))
    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], text=True
        )
    )
    span = stamps[-1] - stamps[0] if len(stamps) > 1 else 0
    return {
        "path": str(path),
        "decoded_frames": len(stamps),
        "presentation_span_seconds": span,
        "timestamp_fps": (len(stamps) - 1) / span if span else 0,
        "unique_transition_fps": (len(stamps) - 1 - repeats) / span if span else 0,
        "consecutive_identical_luma_frames": repeats,
        "intervals_ms": {f"p{p}": float(np.percentile(gaps, p)) if len(gaps) else 0 for p in (50, 95, 99)},
        "max_gap_ms": float(max(gaps, default=0)),
        "nonincreasing_pts": int(sum(gaps <= 0)),
        "gaps_over_50ms": int(sum(gaps > 50.5)),
        "ffprobe": probe,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = [analyze(path) for path in args.paths]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps([{k: v for k, v in row.items() if k != "ffprobe"} for row in result], indent=2))


if __name__ == "__main__":
    main()
