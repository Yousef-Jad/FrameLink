# FrameLink v0.1.1

FrameLink is a private, local desktop screen recording and mobile camera studio. It links your phone camera wirelessly and securely over your local network using encrypted WebRTC, captures your desktop with native platform APIs, records synchronized streams to resilient MKV files, and exports validated MP4s.

## Highlights in v0.1.1

- **Studio Engine & Capture Clock Stability**:
  - Enhanced capture clock synchronization across native Wayland (PipeWire), X11 (Qt Multimedia), and Windows (WGC / DXGI).
  - Deterministic frame admission pacing preventing drift under callback jitter or variable grab rates.
- **Cross-Platform Portable Builds**:
  - **Windows 10/11 x64**: Standalone portable archive (`FrameLink-Windows-x64.zip`).
  - **Linux x86_64**: Standalone AppImage (`FrameLink-Linux-x86_64.AppImage`) and portable tarball (`FrameLink-Linux-x86_64.tar.gz`).
- **Camera Transport & Visual Quality**:
  - High-resolution mobile camera negotiation up to 1080p with active aspect-ratio preservation.
  - Pixel-magnifier compression benchmarks verifying visually lossless export quality.
- **Audio Routing & Resilient Finalization**:
  - Automated microphone discovery and loopback routing for Windows (WASAPI) and Linux (PulseAudio / PipeWire).
  - Atomic recording finalization, corruption recovery, and background compression queue.
