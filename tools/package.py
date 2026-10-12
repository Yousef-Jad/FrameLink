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
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/framelink.spec"],
            cwd=ROOT,
            check=True,
        )
    target = ROOT / "dist" / "FrameLink"
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    manifest = output / "package-inventory.json"
    subprocess.run(
        [sys.executable, str(ROOT / "tools/package_inventory.py"), str(target), str(manifest)], check=True
    )
    shutil.copy(manifest, target / "package-inventory.json")
    executable = target / ("FrameLink.exe" if sys.platform == "win32" else "FrameLink")
    env = os.environ | {"QT_QPA_PLATFORM": "offscreen"}
    import tempfile

    with tempfile.TemporaryDirectory() as data:
        subprocess.run([str(executable), "--smoke-test", "--data-dir", data], env=env, timeout=60, check=True)
    if sys.platform == "win32":
        artifact = Path(
            shutil.make_archive(str(output / "FrameLink-Windows-x64"), "zip", ROOT / "dist", "FrameLink")
        )
    else:
        shutil.copy(ROOT / "packaging/linux/org.framelink.FrameLink.desktop", target)
        artifact = output / "FrameLink-Linux-x86_64.tar.gz"
        with tarfile.open(artifact, "w:gz", compresslevel=3) as archive:
            archive.add(target, arcname="FrameLink")
    with tempfile.TemporaryDirectory() as unpacked:
        shutil.unpack_archive(artifact, unpacked)
        extracted = Path(unpacked) / "FrameLink" / executable.name
        subprocess.run(
            [str(extracted), "--smoke-test", "--data-dir", str(Path(unpacked) / "verify-data")],
            env=env,
            timeout=60,
            check=True,
        )
    with artifact.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(checksum + "  " + artifact.name + "\n")
    print(f"Verified package: {artifact}")


if __name__ == "__main__":
    main()
