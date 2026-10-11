# FrameLink v0.1.0

FrameLink is a private, local desktop screen recording and mobile camera studio. It links your phone camera wirelessly and securely over your local network using encrypted WebRTC, captures your desktop with native platform APIs, records synchronized streams to resilient MKV files, and exports validated MP4s.

## Highlights

- **Dual-Source Local Studio**: Record Desktop Screen, Phone Camera, or Both simultaneously with customizable Picture-in-Picture (PiP).
- **Zero Cloud & Absolute Privacy**: 100% offline and local. No accounts, no external tracking, no cloud uploads. Certificates and recordings stay entirely on your machines.
- **Encrypted Local WebRTC**: Pair your mobile device in seconds via temporary QR codes over local HTTPS. No phone app installation required.
- **Native Desktop Capture**:
  - Windows: Windows Graphics Capture (WGC) and DXGI with WASAPI audio loopback.
  - Linux: Wayland/PipeWire (including Hyprland) via xdg-desktop-portal and X11 native capture.
- **Fail-Safe Recording**: Real-time free-space monitoring, single recording clock synchronization, and automatic recovery of interrupted/partial MKV recordings.
- **Library & Transcoding**: Built-in SQLite media library with instant playback, thumbnail previews, and background export queue (H.264, HEVC, AV1).
- **Cross-Platform Portable Builds**: Ready-to-run portable binaries for Windows (x64) and Linux (AppImage & tarball).
