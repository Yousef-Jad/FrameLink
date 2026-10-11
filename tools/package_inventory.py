"""Record the actual native package contents for the distribution audit."""

import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def inventory(target):
    dependencies = []
    pending, seen = ["lumacapture"], set()
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        dist = metadata.distribution(name)
        dependencies.append({
            "name": dist.metadata["Name"],
            "version": dist.version,
            "declared_license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
            "project_urls": dist.metadata.get_all("Project-URL", []),
            "notice_files": [str(f) for f in dist.files or [] if any(
                term in str(f).lower() for term in ("license", "copyright", "sbom", "notice")
            ) and str(f).endswith((".txt", ".md", ".json", "LICENSE", "COPYING", "NOTICE"))],
        })
        for value in dist.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    files = []
    for path in sorted(target.rglob("*")):
        if path.is_file() and (".so" in path.name or path.suffix.lower() in {".dll", ".exe", ".pyd"}):
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            files.append({"path": path.relative_to(target).as_posix(), "size": path.stat().st_size, "sha256": digest})
    import av

    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "dependencies": sorted(dependencies, key=lambda d: d["name"].lower()),
        "pyav_libraries": av.library_versions,
        "native_files": files,
        "license_audit_complete": False,
        "note": "Declared metadata is evidence for review, not a determination of binary redistribution rights.",
    }


if __name__ == "__main__":
    target = Path(sys.argv[1])
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory(target), indent=2), encoding="utf-8")
    print("Package inventory:", output)
