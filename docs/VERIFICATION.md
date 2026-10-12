# Verification record

This is a development verification record, not a claim of production readiness.

The current storage-reclamation work is documented in
[COMPRESSION_VERIFICATION.md](COMPRESSION_VERIFICATION.md): protected old recordings,
future-recording replacement, quality/integrity gates, codec comparison and native
ten-minute input. Automatic deletion remains fail-closed when verification fails.

Mode-dependent microphone defaults and current audio checks are documented in
[MODE_AUDIO.md](MODE_AUDIO.md), including decoded MP4 source-routing measurements
and a native Linux microphone check. Physical phone and Windows audio checks
remain outstanding.

The current Stop, certificate trust, camera quality and preview fixes are documented in [CRITICAL_FIXES.md](CRITICAL_FIXES.md). The historical builds below do not verify those newer changes.

## Executed locally

- Host: Arch Linux x86_64, Hyprland Wayland; Python 3.14.7.
- Installed runtime: PySide6 6.12.0, PyAV 17.1.0, aiortc 1.15.0.
- 42 automated tests passed after fixing startup, strict certificate validation, and library persistence defects.
- Actual desktop UI instantiated and rendered; screenshot in `screenshots/dashboard.png`.
- Runtime smoke test wrote H.264 MKV, decoded it, and exited successfully.
- Unit/integration coverage: recording state and pause clock, backend selection, encoder probing, safe filenames, non-overwriting finalization, bounded buffers, audio placement, one-use token expiry and races, LAN restrictions, media integrity, four recording layouts, phone interruption policy, compression/cancellation, queue/library indexing, recovery, strict HTTPS, invalid requests, HEVC and SVT-AV1 exports, contiguous audio under encoder jitter, real encrypted WebRTC between software peers, and reconnect.

Synthetic media sources are confined to test fixtures and the explicit smoke-test CLI. A software WebRTC sender is not a physical Android or iPhone camera. Actual platform verification:

- X11 under Xvfb: native Qt capture saved 1280×720 H.264, 5.100 seconds, 148 decoded frames. FFprobe and full decode passed.
- Hyprland through the configured xdg-desktop-portal-wlr and PipeWire: native 1920×1080 H.264, 5.033 seconds, 78 decoded frames. FFprobe and full decode passed. This overloaded run did **not** sustain nominal 30 FPS. The portal's selection UI remained in control of sharing.
- Frozen Linux executable: built on Arch, executed the actual UI and media smoke check successfully. This local build requires compatible host libraries; it is not the Ubuntu portability baseline.
- [CI run 38164735635](https://github.com/Yousef-Jad/FrameLink/actions/runs/38164735635), commit `a597277`: Windows Server 2022 and Ubuntu 24.04 both passed 34 tests, UI/media smoke, and native package construction plus frozen-runtime execution. Ubuntu also passed genuine X11 capture in Xvfb. This does not verify Windows 10/11 hardware or physical phones.

- [CI run 38165084721](https://github.com/Yousef-Jad/FrameLink/actions/runs/38165084721), commit `ee333a2`: both native runners passed 37 tests, archive extraction and frozen application execution. Windows Server 2022 captured genuine desktop frames: H.264, 1024×768, 5.066 seconds, 152 frames, no audio. This verifies that runner's capture path, not Windows 10/11 client devices.
- Chromium 153.0.8010.12 browser integration: real HTTPS CA trust in an isolated NSS database, without certificate-error bypass. Browser test camera/microphone streamed over encrypted WebRTC; phone-only recording produced H.264/AAC at 640×480, around 30 encoded FPS. Pairing, recording indicator, pause/resume, camera reacquisition, reconnect, desktop disconnect, fresh QR reuse in an open tab, and cancellation of pending camera acquisition were exercised. The camera is synthetic and does not verify Android Chrome or iPhone Safari.
- A local AppImage was constructed from the frozen application and executed successfully through extraction mode. Normal FUSE launch also passed on this Arch host. Clean-machine portability remains unverified.
- Phone startup latency follow-up: isolated Chromium synthetic 1920×1080/30 WebRTC with an attached Qt preview measured T6 first RTP → T7 decode at 63.0 ms, T7 → T8 painted preview at 85.0 ms, and T6 → T8 at 148.0 ms. The report is `artifacts/delay-verification/result.json`; FFprobe confirmed the saved 1920×1080 H.264/AAC recording. This is not physical iPhone evidence.
- The MP4 output follow-up passed 134 tests at that revision; see [MP4_OUTPUT.md](MP4_OUTPUT.md). Node startup/quality policy checks, phone Record/Stop acknowledgement and timer checks, and JavaScript syntax checks pass. Receiver states now distinguish WebRTC connected, receiving video, and Qt preview ready; temporary WebRTC reconnect retains the existing session and address.
- Offline ICE follow-up: candidate filtering, mDNS rewriting, selected-interface diagnostics, firewall guidance, credential redaction, failed-peer session retention, and encrypted synthetic host ICE pass. The report format now includes `network_diagnostics`; the host used a private IPv4 Wi-Fi network, not an iPhone hotspot.
- Remote recording controls: the authenticated WebRTC data channel carries versioned, revision-checked `start`/`stop` commands. The desktop confirms execution before the phone changes Record/Stop state, broadcasts the selected Phone/Screen/Both mode and elapsed time, rejects duplicate/conflicting/stale commands, and leaves Screen recording running when the phone disconnects. Real encrypted software-peer tests cover Phone, Screen, Both/PiP and Both/separate output plus automatic compression.
- Performance follow-up: bounded source queues, source-driven timestamps, duplicate-frame telemetry, stage timing, disk-flush timing, and RGB compositor fast paths are covered by the local suite. Synthetic and actual native 1920×1080 results are documented in [PERFORMANCE.md](PERFORMANCE.md). Current Hyprland/portal capture produced validated 30-second files at 29.93 actual FPS for Screen/30, and 32.65 actual FPS when requesting 60. Preview, PiP, separate outputs and automatic compression were exercised; Both used a labelled synthetic phone fixture. Native 60 FPS and physical phones remain unverified performance targets.

- [CI run 38166403701](https://github.com/Yousef-Jad/FrameLink/actions/runs/38166403701), commit `1c45077`: 42 tests passed on both Windows Server 2022 and Ubuntu 24.04. Both extracted portable packages passed UI/media smoke tests. Ubuntu AppImage construction and extraction-mode execution passed. Windows screen capture produced 1024×768 H.264, 5.100 seconds, 306 decoded frames. Frozen local Linux X11 capture produced 1280×720 H.264, 5.066 seconds, 152 decoded frames.

Later changes must pass CI again before being described as verified packages. Local browser checks can be repeated using `pip install playwright==1.63.0`, `playwright install chromium`, and `python tools/browser_check.py`; Linux `bubblewrap` and NSS `certutil` are required. The test mounts a temporary CA trust database into the browser namespace and does not modify the user's certificate trust.

## Pending release gates

Windows 10/11 device capture and audio, clean-machine Linux portability; physical Android Chrome and iPhone Safari with manually trusted certificates; router and both hotspot directions without internet; long-duration A/V drift; dependency license/source obligations.

## Incomplete requested features

Zero-copy capture/composition, Linux compositor blur, IPv6-only pairing, and guaranteed target-size multipass compression are not implemented. Windows 11 DWM backdrop integration and optional NVIDIA/AMD utilization reporting are implemented but require applicable hardware verification. Windows WASAPI loopback is implemented but hardware testing is pending. Automatic original cleanup is now restricted to eligible future recordings after integrity, size and sampled quality verification; see [compression retention](COMPRESSION_RETENTION.md). Existing recordings remain protected.

A versioned public release remains blocked while the required evidence manifest is incomplete.

Binary CI uploads are gated on the dependency license/source audit as well as build success. Older v0.1.0 binaries remain publicly available, but do not contain the current local fixes and are not a verified production release; CI logs retain historical build evidence. See [distribution audit](DISTRIBUTION_AUDIT.md).
