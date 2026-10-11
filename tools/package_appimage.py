"""Wrap the already verified Linux portable directory and test the resulting AppImage."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
)
TOOL_SHA256 = "95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6"


def main():
    tool = ROOT / "build/appimagetool"
    tool.parent.mkdir(parents=True, exist_ok=True)
    if not tool.exists():
        urllib.request.urlretrieve(TOOL_URL, tool)
    with tool.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != TOOL_SHA256:
        raise SystemExit("AppImage tool checksum changed. Review and pin the upstream asset before building.")
    tool.chmod(0o755)
    appdir = ROOT / "build/LumaCapture.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr").mkdir(parents=True)
    shutil.copytree(ROOT / "dist/LumaCapture", appdir / "usr/bin")
    shutil.copy(ROOT / "packaging/linux/AppRun", appdir / "AppRun")
    shutil.copy(
        ROOT / "packaging/linux/org.lumacapture.LumaCapture.desktop",
        appdir / "org.lumacapture.LumaCapture.desktop",
    )
    shutil.copy(ROOT / "src/lumacapture/assets/lumacapture.png", appdir / "lumacapture.png")
    shutil.copy(ROOT / "src/lumacapture/assets/lumacapture.png", appdir / ".DirIcon")
    artifact = ROOT / "artifacts/LumaCapture-Linux-x86_64.AppImage"
    env = os.environ | {"ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"}
    subprocess.run(
        [str(tool), "--no-appstream", str(appdir), str(artifact)], env=env, check=True, timeout=300
    )
    with tempfile.TemporaryDirectory() as data:
        subprocess.run(
            [str(artifact), "--appimage-extract-and-run", "--smoke-test", "--data-dir", data],
            env=env | {"QT_QPA_PLATFORM": "offscreen"},
            check=True,
            timeout=90,
        )
    with artifact.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(digest + "  " + artifact.name + "\n")
    print("Verified AppImage:", artifact)


if __name__ == "__main__":
    main()
