"""Fail closed until both platform artifacts and the required manual checks are attested."""

import json
from pathlib import Path
import sys

required = (
    "windows_package",
    "linux_package",
    "windows_capture",
    "linux_x11_capture",
    "linux_wayland_capture",
    "android_chrome",
    "ios_safari",
    "offline_hotspot",
    "audio_sync",
    "dependency_license_audit",
    "recording_stop",
    "recording_finalization",
    "compression_after_stop",
    "camera_quality",
    "preview_fill",
    "tls_endpoint",
    "critical_regressions",
)
report = json.loads(Path(sys.argv[1]).read_text())
missing = [
    key
    for key in required
    if report.get(key, {}).get("verified") is not True or not report.get(key, {}).get("evidence")
]
if missing:
    raise SystemExit("Release blocked: missing verification for " + ", ".join(missing))
print("Release verification evidence is present for every required gate")
