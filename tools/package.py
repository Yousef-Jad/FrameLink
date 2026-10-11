"""Build a native portable package. Invoke independently on each target operating system."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if not args.skip_build:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/lumacapture.spec"],
            cwd=ROOT,
            check=True,
        )
    target = ROOT / "dist" / "LumaCapture"
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    executable = target / ("LumaCapture.exe" if sys.platform == "win32" else "LumaCapture")
    env = os.environ | {"QT_QPA_PLATFORM": "offscreen"}
    import tempfile

    with tempfile.TemporaryDirectory() as data:
        subprocess.run([str(executable), "--smoke-test", "--data-dir", data], env=env, timeout=60, check=True)
    if sys.platform == "win32":
        artifact = Path(
            shutil.make_archive(str(output / "LumaCapture-Windows-x64"), "zip", ROOT / "dist", "LumaCapture")
        )
    else:
        shutil.copy(ROOT / "packaging/linux/org.lumacapture.LumaCapture.desktop", target)
        artifact = output / "LumaCapture-Linux-x86_64.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            archive.add(target, arcname="LumaCapture")
    checksum = hashlib.file_digest(artifact.open("rb"), "sha256").hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(checksum + "  " + artifact.name + "\n")
    print(f"Verified package: {artifact}")


if __name__ == "__main__":
    main()
