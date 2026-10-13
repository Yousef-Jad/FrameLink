# FrameLink

Desktop and phone recording, on your local network.

[English](README.md) | [العربية](docs/i18n/README.ar.md) | [Русский](docs/i18n/README.ru.md) | [简体中文](docs/i18n/README.zh-CN.md)

FrameLink records your screen, a phone camera, or both. The desktop receives camera video through the phone's browser over encrypted WebRTC, stores recordings locally, and automatically queues validated MP4 exports. No cloud account is required.

![FrameLink running in Phone mode with its pairing interface](docs/assets/framelink-phone-pairing.png)

*Actual application window. The demonstration QR is deliberately nonfunctional; it contains no pairing credential or private network address.*

## Features

- Native screen capture on Windows and Linux, including portal-based Wayland/Hyprland capture.
- Browser camera streaming for Android Chrome and iPhone Safari; physical-device verification is pending.
- Screen + camera recording with picture-in-picture or separate synchronized files.
- Local Wi-Fi pairing, phone-side Record/Stop controls, and microphone audio.
- Automatic compression with validated exports and a recording library showing file sizes and savings.

## Compression example

![Original recording on the left; compressed recording on the right](docs/assets/compression-comparison.png)

**1.47 MB → 0.81 MB | 45.2% space saved | H.264**

Real 30-second 1920×1080 desktop recording compared at the exact same frame (10.000s). The 8× nearest-neighbor crops highlight fine-detail retention and subtle DCT compression characteristics. Lossy results vary by scene complexity and settings. [Files, settings and methodology](docs/COMPRESSION_EVIDENCE.md).

For future recordings, **Keep Original** defaults to off: the original is removed only after a smaller export passes integrity and sampled quality checks. Existing recordings remain protected. [Retention policy](docs/COMPRESSION_RETENTION.md).

## Installation

**Windows 10/11 x64:** download [FrameLink-Windows-x64-Setup.exe](https://github.com/Yousef-Jad/FrameLink/releases/download/v0.1.2/FrameLink-Windows-x64-Setup.exe) and choose **Install and launch**. This experimental online installer needs .NET Framework 4.8 and internet for the first installation; it downloads its runtime and dependencies, then creates a desktop shortcut. Recording afterward stays local. [Setup and verification](docs/WINDOWS_ONLINE_INSTALLER.md).

**Linux x86_64:** public portable bundles remain withdrawn. Use Python 3.12+ and the [source installation instructions](docs/USER_GUIDE.md#build-from-source). Wayland requires PipeWire and a working ScreenCast portal. Bundled-package publication remains gated on device verification and distribution licensing.

## Quick Start

1. Launch FrameLink and choose **Screen**, **Phone**, or **Both**.
2. For Phone, connect both devices to the same Wi-Fi or supported hotspot, complete HTTPS certificate trust setup, then scan the pairing QR and grant camera access.
3. Check the preview, record, stop, and use the validated compressed export in the recording library.

## Offline pairing

Internet access is unnecessary for local recording. Devices still need reachable local addresses, permitted firewall traffic, and a trusted desktop certificate. Isolated hotspot clients cannot connect.

On iPhone, install the verified public root certificate, then enable full trust under **Settings → General → About → Certificate Trust Settings**. Never bypass Safari certificate warnings. [Trust and network setup](docs/USER_GUIDE.md#first-time-certificate-trust-and-pairing).

## Compatibility and verification

Windows 10/11 x64 and Linux x86_64 are the desktop targets. Native Linux X11 and Hyprland/Wayland recording have local test evidence. Physical Android/iPhone streaming and trust, Windows client hardware, hotspot operation, and long-running A/V synchronization still require verification. [Evidence and limitations](docs/VERIFICATION.md).

## Documentation

- [Installation, setup and troubleshooting](docs/USER_GUIDE.md)
- [Architecture](docs/ARCHITECTURE.md) · [Performance](docs/PERFORMANCE.md)
- [Security](SECURITY.md) · [HTTPS verification](docs/CRITICAL_FIXES.md)
- [Development and contributions](CONTRIBUTING.md)

## License

[GPL-3.0-or-later](LICENSE). Dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
