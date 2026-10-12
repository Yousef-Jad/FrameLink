"""Compare measured browser transport and FFprobe results from the same fixture."""

import argparse
import json
from pathlib import Path


def summarize(directory):
    result = json.loads((directory / "result.json").read_text())
    probe = json.loads((directory / "ffprobe.json").read_text())
    media = result["recordings"][0]["metadata"]
    received = result.get("received_during_recording", result["received"])
    return {
        "source": result["source_settings"],
        "received": {k: received[k] for k in ("width", "height", "fps")},
        "saved": {
            k: media[k] for k in ("width", "height", "fps", "measured_fps", "duration", "size", "audio")
        },
        "container_bitrate": int(probe["format"]["bit_rate"]),
        "sender_target_bitrate": result["sender_stats"][0].get("targetBitrate"),
        "sender_measurements": result.get("quality_report"),
        "recorder_metrics": result.get("recorder_metrics"),
        "recording_elapsed": result.get("recording_elapsed"),
        "tls_verified": result["tls"]["verified"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--minimum-fps", type=float, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, after = summarize(args.before), summarize(args.after)
    assert before["source"] == after["source"], "Use identical camera capture settings"
    for dimension in ("width", "height"):
        assert after["source"][dimension] == after["received"][dimension] == after["saved"][dimension], (
            "Unexpected transport or recording resize"
        )
    assert after["received"]["fps"] >= args.minimum_fps, "Received cadence below requested gate"
    assert after["saved"]["measured_fps"] >= args.minimum_fps, "Saved cadence below requested gate"
    assert after["tls_verified"]
    if after["recording_elapsed"] is not None:
        assert abs(after["recording_elapsed"] - after["saved"]["duration"]) < 0.35
    args.output.write_text(
        json.dumps(
            {
                "fixture": "Chromium synthetic moving camera; not a physical phone or Wi-Fi benchmark",
                "before": before,
                "after": after,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Camera comparison passed: {args.output}")


if __name__ == "__main__":
    main()
