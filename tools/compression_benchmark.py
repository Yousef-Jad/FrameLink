"""Measure real recordings without deleting or modifying the supplied source."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import time

from framelink.compression.queue import compress, CompressionSettings
from framelink.media.encode import probe_encoder, validate_media


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clip-seconds", type=int, default=30)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"source": str(args.source.resolve()), "source_sha256": digest(args.source),
              "source_media": validate_media(args.source), "comparisons": []}

    def save():
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    clip = args.output / "comparison-source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-n", "-i", str(args.source),
                    "-t", str(args.clip_seconds), "-c", "copy", str(clip)], check=True)
    report["clip_media"] = validate_media(clip)
    for codec, quality in (("libx264", 22), ("libx265", 22), ("libsvtav1", 28)):
        probe = probe_encoder(codec, 1920, 1080)
        result = {"encoder_probe": probe}
        report["comparisons"].append(result)
        if probe["available"]:
            settings = CompressionSettings(codec=codec, quality=quality)
            output = args.output / f"comparison-{codec}.mp4"
            start = time.perf_counter()
            info = compress(clip, output, settings)
            result.update(settings=asdict(settings), media=info, file=str(output),
                          elapsed_seconds=time.perf_counter() - start,
                          saved_percent=(1 - info["size"] / clip.stat().st_size) * 100)
        save()
        print(json.dumps(result), flush=True)
    # A full-duration encode is mandatory; do not extrapolate the short comparison.
    start = time.perf_counter()
    full = args.output / "full-balanced.mp4"
    info = compress(args.source, full, CompressionSettings())
    report["full"] = {"file": str(full), "media": info,
                      "elapsed_seconds": time.perf_counter() - start,
                      "saved_percent": (1 - info["size"] / args.source.stat().st_size) * 100}
    report["source_unchanged"] = digest(args.source) == report["source_sha256"]
    save()
    print(json.dumps(report["full"]), flush=True)


if __name__ == "__main__":
    main()
