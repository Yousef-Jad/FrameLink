# Verification record

This is a development verification record, not a claim of production readiness.

## Executed locally

- Host: Arch Linux x86_64, Hyprland Wayland; Python 3.14.7.
- Installed runtime: PySide6 6.12.0, PyAV 17.1.0, aiortc 1.15.0.
- 34 automated tests passed after fixing startup, strict certificate validation, and library persistence defects.
- Actual desktop UI instantiated and rendered; screenshot in `screenshots/dashboard.png`.
- Runtime smoke test wrote H.264 MKV, decoded it, and exited successfully.
- Unit/integration coverage: recording state and pause clock, backend selection, encoder probing, safe filenames, non-overwriting finalization, bounded buffers, audio placement, one-use token expiry and races, LAN restrictions, media integrity, four recording layouts, phone interruption policy, compression/cancellation, queue/library indexing, recovery, strict HTTPS, invalid requests, real encrypted WebRTC between software peers, and reconnect.

Synthetic media sources are confined to test fixtures and the explicit smoke-test CLI. A software WebRTC sender is not a physical Android or iPhone camera. Actual capture, package, and CI results will be added only after they execute.

## Pending release gates

Windows native build/package and actual WGC/DXGI capture; Linux X11/Wayland capture and portable package; physical Android Chrome and iPhone Safari with manually trusted certificates; router and both hotspot directions without internet; long-duration A/V drift; dependency license/source obligations.

## Incomplete requested features

Dedicated Windows WASAPI system loopback, supported compositor blur APIs, GPU utilization metrics, sustained-load automatic adaptation, zero-copy capture/composition, AppImage distribution, and guaranteed target-size multipass compression are not implemented. Default originals retention is implemented; automatic original cleanup is deliberately unavailable.

A versioned public release remains blocked while the required evidence manifest is incomplete.
