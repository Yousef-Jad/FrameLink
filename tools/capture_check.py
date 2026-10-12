"""Run real platform capture with a bounded timeout and preserve the result."""

import json
from pathlib import Path
import subprocess
import sys

output = Path("artifacts/native-capture")
output.mkdir(parents=True, exist_ok=True)
try:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "framelink.app",
            "--capture-test",
            str(output),
            "--data-dir",
            "artifacts/native-capture-data",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "platform": sys.platform,
        "verified": result.returncode == 0 and bool(list(output.glob("*.mp4"))),
    }
except subprocess.TimeoutExpired:
    report = {"verified": False, "platform": sys.platform, "error": "Desktop capture exceeded 120 seconds"}
Path("artifacts/native-capture-result.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["verified"] else 1)
