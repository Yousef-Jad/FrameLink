# Third-party notices

LumaCapture application code is GPL-3.0-or-later. Its packages include unmodified third-party components whose notices and license terms continue to apply.

| Component | Upstream | License family / distribution concern |
|---|---|---|
| Python | https://www.python.org/ | PSF license |
| Qt / PySide6 / Shiboken | https://www.qt.io/ | LGPLv3 / GPL alternatives; dynamic libraries must remain replaceable |
| FFmpeg / PyAV | https://ffmpeg.org/ and https://github.com/PyAV-Org/PyAV | FFmpeg build configuration determines LGPL/GPL obligations; PyAV BSD |
| x264 / x265 / AV1 libraries | FFmpeg build dependencies | GPL or permissive depending on the actual codec library |
| aiortc / aioice | https://github.com/aiortc/aiortc | BSD-3-Clause |
| aiohttp | https://github.com/aio-libs/aiohttp | Apache-2.0 |
| cryptography / OpenSSL | https://cryptography.io/ and https://openssl.org/ | Apache-2.0 / BSD and upstream bundled notices |
| libsrtp / pylibsrtp | https://github.com/cisco/libsrtp | BSD-style |
| Pillow | https://python-pillow.org/ | HPND and bundled dependency notices |
| NumPy | https://numpy.org/ | BSD-3-Clause and bundled notices |
| psutil | https://github.com/giampaolo/psutil | BSD-3-Clause |
| qrcode | https://github.com/lincolnloop/python-qrcode | BSD |
| windows-capture | https://github.com/NiiightmareXD/windows-capture | MIT; Windows-only native adapter |
| PyAudioWPatch / PortAudio | https://github.com/s0d3s/PyAudioWPatch | MIT / PortAudio license; Windows WASAPI loopback |
| PyInstaller bootloader | https://pyinstaller.org/ | GPL with bootloader distribution exception |

Package metadata and included license files are collected into portable bundles. This table is not a substitute for inspecting the exact binaries, transitive dependencies, build configurations, and corresponding source obligations. The release license gate remains false until that audit is complete and any required source distribution is prepared. The development packages must not be described as license-audited releases.

The application icon is an original vector asset included under the project license.
