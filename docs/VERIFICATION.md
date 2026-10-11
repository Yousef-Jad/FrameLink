# Verification record

This is a development verification record, not a claim of production readiness.

## Executed locally

- Host: Arch Linux x86_64, Hyprland Wayland; Python 3.14.7.
- Installed runtime: PySide6 6.12.0, PyAV 17.1.0, aiortc 1.15.0.
- 37 automated tests passed after fixing startup, strict certificate validation, and library persistence defects.
- Actual desktop UI instantiated and rendered; screenshot in `screenshots/dashboard.png`.
- Runtime smoke test wrote H.264 MKV, decoded it, and exited successfully.
- Unit/integration coverage: recording state and pause clock, backend selection, encoder probing, safe filenames, non-overwriting finalization, bounded buffers, audio placement, one-use token expiry and races, LAN restrictions, media integrity, four recording layouts, phone interruption policy, compression/cancellation, queue/library indexing, recovery, strict HTTPS, invalid requests, real encrypted WebRTC between software peers, and reconnect.

Synthetic media sources are confined to test fixtures and the explicit smoke-test CLI. A software WebRTC sender is not a physical Android or iPhone camera. Actual platform verification:

- X11 under Xvfb: native Qt capture saved 1280×720 H.264, 5.100 seconds, 148 decoded frames. FFprobe and full decode passed.
- Hyprland through the configured xdg-desktop-portal-wlr and PipeWire: native 1920×1080 H.264, 5.033 seconds, 78 decoded frames. FFprobe and full decode passed. This overloaded run did **not** sustain nominal 30 FPS. The portal's selection UI remained in control of sharing.
- Frozen Linux executable: built on Arch, executed the actual UI and media smoke check successfully. This local build requires compatible host libraries; it is not the Ubuntu portability baseline.
- [CI run 38164735635](https://github.com/Yousef-Jad/LumaCapture/actions/runs/38164735635), commit `a597277`: Windows Server 2022 and Ubuntu 24.04 both passed 34 tests, UI/media smoke, and native package construction plus frozen-runtime execution. Ubuntu also passed genuine X11 capture in Xvfb. This does not verify Windows 10/11 hardware or physical phones.

Later changes must pass CI again before being described as verified packages.

## Pending release gates

Windows native build/package and actual WGC/DXGI capture; Linux X11/Wayland capture and portable package; physical Android Chrome and iPhone Safari with manually trusted certificates; router and both hotspot directions without internet; long-duration A/V drift; dependency license/source obligations.

## Incomplete requested features

Supported compositor blur APIs, GPU utilization metrics, zero-copy capture/composition, AppImage distribution, and guaranteed target-size multipass compression are not implemented. Windows WASAPI loopback is implemented but hardware testing is pending. Default originals retention is implemented; automatic original cleanup is deliberately unavailable.

A versioned public release remains blocked while the required evidence manifest is incomplete.
