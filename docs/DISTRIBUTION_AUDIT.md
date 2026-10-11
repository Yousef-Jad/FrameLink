# Distribution audit

Status: incomplete. Native packages are built and executed for testing, but public binary distribution is gated separately. No commercial codec licence or exception is assumed.

`tools/package_inventory.py` records the actual dependency versions, declared metadata, notice paths, FFmpeg library versions, and hashes of bundled native files for each OS. Packaging collects transitive distribution metadata, including supplied notices. This inventory does not replace a corresponding-source bundle or a review of native libraries copied by the packaging tools.

## Concrete findings

- PyAV 17.1.0's [vendor manifest](https://github.com/PyAV-Org/PyAV/blob/v17.1.0/scripts/ffmpeg-8.1.json) selects `pyav-ffmpeg` 8.1.1-2. The vendor tag resolves to `b84838a003b1626152ef530750d5c3e0022c3db9`.
- The local wheel's FFmpeg configuration enables x264 and x265, while `avcodec_license()` reports LGPL version 3 or later. The vendor's [FFmpeg patch](https://github.com/PyAV-Org/pyav-ffmpeg/blob/8.1.1-2/patches/ffmpeg.patch) moves x264 and x265 out of the configure script's GPL list. That runtime label alone is insufficient evidence for downstream redistribution terms. The application's GPL licence does not remove the need to supply applicable dependency source, notices, and build materials.
- The vendor release contains platform binary archives. Exact matching source archives, vendor patches, build scripts, and copied platform dependencies still need to be assembled and checked against each shipped wheel.
- PySide6 Essentials/Addons 6.12.0 and Shiboken supply native Qt libraries. Their wheel metadata alone does not contain a complete notice/source package for all bundled Qt modules and native dependencies. The exact Qt/PySide/Shiboken sources and third-party notices must accompany the distribution process. See [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html).
- PyInstaller can collect system libraries beyond Python distribution metadata. These need platform-specific provenance and notice checks. Python's licence, the PyInstaller bootloader exception, AppImage runtime materials, and Windows adapter dependencies also need inclusion in the final source/notice bundle.

The remaining work is to assemble corresponding sources and build materials, resolve the wheel's codec redistribution terms through a documented route, validate the complete inventory on both runners, and host the required materials alongside binary downloads. FFmpeg's [distribution checklist](https://ffmpeg.org/legal.html) explains why the matching build sources and configuration matter.

Until that work is complete, `dependency_license_audit.verified` remains false. The build workflow uploads verification reports, but its binary-upload step stays disabled. The release workflow also refuses publication. Existing development artifacts are not a substitute for completing this gate.
