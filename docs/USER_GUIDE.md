# FrameLink user guide

Detailed setup and operating reference, including local application fixes not yet pushed to the public repository. For a concise overview, see the [project README](../README.md).

FrameLink is a local desktop and phone camera recording studio. It receives browser camera video over encrypted WebRTC, captures desktop video through native platform APIs, records to recoverable MP4 files, and creates validated MP4 exports.

**Development status:** working implementation under verification. See the [critical recording, TLS, quality and preview verification](CRITICAL_FIXES.md) for the current changes and [verification history](VERIFICATION.md) for earlier builds.

![Actual FrameLink Studio interface](screenshots/ui-redesign/studio.png)

The current interface is a preview-first Studio with a compact navigation rail, a floating recording transport, and contextual capture settings. Additional rendered states are in [UI redesign evidence](screenshots/ui-redesign/README.md).

The screenshot is from the running application. The empty preview is intentional: no recording or device data is fabricated.

## Features

- Phone Camera, Desktop Screen, and Both modes.
- Picture-in-picture with corner placement, size, opacity, mirroring, rounded corners, and borders.
- Separate screen and camera MP4 files sharing one recording clock.
- Start, pause, resume, stop, free-space monitoring, and interrupted-file recovery.
- Local HTTPS pairing with one-use, ten-minute QR codes and encrypted WebRTC media.
- Front/rear mobile camera selection, requested resolutions and frame rates, reconnect attempts, and visible camera/recording indicators.
- SQLite recording library with actual file metadata, thumbnails, playback, rename, export, and deletion.
- Persistent compression queue with H.264, HEVC, and AV1 options, cancellation, quality controls, and output validation.
- Dark and light desktop themes with an opaque fallback surface.

## Platform targets

| Platform | Capture implementation | Verification |
|---|---|---|
| Windows 10/11 x64 | Windows Graphics Capture via `windows-capture`, Qt DXGI fallback | See verification report |
| Linux x86_64 / X11 | Qt Multimedia X11 capture | See verification report |
| Linux / Wayland, including Hyprland | Qt PipeWire capture through xdg-desktop-portal | Requires a working ScreenCast portal and permission |
| Android Chrome | HTTPS `getUserMedia` → WebRTC | Physical device testing pending |
| iPhone Safari | HTTPS `getUserMedia` → WebRTC | Physical device testing pending; manual certificate trust required |

Qt documents its Wayland screen capture as experimental. The target display is selected in the system portal; the desktop monitor dropdown does not override that selection. [Qt documentation](https://doc.qt.io/qt-6/qscreencapture.html)

## Installation

The public [v0.1.0 release](https://github.com/Yousef-Jad/FrameLink/releases/tag/v0.1.0) contains older portable builds. These are not the current working-tree implementation or a verified production release. New binary publication is blocked until the [release gates](release-verification.json), including dependency licensing, pass. Build from source to use the current checkout. A successful CI build alone is not release approval.

### Windows

Extract `FrameLink-Windows-x64.zip` and launch `FrameLink/FrameLink.exe`. Keep the `_internal` directory beside the executable. The portable bundle includes Python, Qt, media libraries, and runtime dependencies. It is unsigned; there is no installer or automatic firewall modification. Remove the extracted directory to uninstall. Application data and recordings remain in your user directories until you remove them explicitly.

### Linux

Extract `FrameLink-Linux-x86_64.tar.gz`, then run `./FrameLink/FrameLink`. The package targets Linux systems (tested on Ubuntu 24.04 and Arch Linux). The host must provide its graphics drivers, X11 or Wayland libraries, audio service, PipeWire, and a suitable desktop portal. For the AppImage, make `FrameLink-Linux-x86_64.AppImage` executable and run it. If FUSE is unavailable, use `./FrameLink-Linux-x86_64.AppImage --appimage-extract-and-run`. The portable package includes a desktop entry; adjust its `Exec` and `Icon` paths before installing it in `~/.local/share/applications/`.

For Hyprland, use a working `xdg-desktop-portal-hyprland` or another ScreenCast portal appropriate for your compositor. FrameLink does not change portal configuration or bypass permission dialogs.

## First-time certificate trust and pairing

1. Launch FrameLink and select **Phone** or **Both**. The local server displays a pairing QR code.
2. Open **iPhone setup: Trust certificate**. Use **Export public certificate for USB transfer** to export the public root, iOS profile, and SHA-256 fingerprint into a separate folder. Never transfer the private certificate directory. Use a trusted physical transfer or Apple Configurator where available.
3. Alternatively, the setup QR opens a temporary HTTP certificate page on port 8444. HTTP is unauthenticated: compare the fingerprint of the **downloaded certificate itself** with the desktop before trusting it. Text on the HTTP page alone is not proof of authenticity. If your device cannot display the fingerprint, use the trusted transfer method.
4. On iPhone, install the profile through **Settings → Profile Downloaded**, or **Settings → General → VPN & Device Management**. Then open **Settings → General → About → Certificate Trust Settings** and explicitly enable full trust for this desktop's root. Installing a profile alone does not enable TLS trust. See [Apple's instructions](https://support.apple.com/en-us/102390).
5. On Android, install the verified public certificate using Settings' **Install a certificate → CA certificate** flow; names vary by device. Remove this root when you stop using the desktop.
6. In FrameLink, run **Validate served certificate at QR address**. Open the secure test link in a fresh browser tab. It must open without a certificate warning. A test page cannot detect a previously bypassed warning. Never select **Visit This Website** to bypass an error.
7. Scan a fresh Studio pairing QR and explicitly grant camera access. Keep Safari or Chrome foregrounded. The desktop preview appears when video arrives.

There is no guaranteed first-scan offline experience without prior certificate trust. Full iPhone Safari and Android device validation remains pending; do not interpret desktop certificate validation as proof that a phone trusts the root.

### Certificate identity

FrameLink keeps a persistent 3072-bit RSA root CA. Unix keys use mode `0600` inside a `0700` directory; Windows uses the current user's application-data ACL. Only public trust material is exported. A trusted root grants certificate authority, so protect the desktop account and remove phone trust when decommissioning it.

The served leaf uses a 2048-bit RSA key, SHA-256 signature, exact IP SAN, server-authentication EKU, and up to 90 days' validity. The server sends leaf and root. Signature, key, chain, purpose, validity and address are checked before reuse. An IP change renews the leaf without replacing the root. The diagnostic checks the actual listener, including whether its certificate differs from the file on disk.

The phone sends compressed video and audio through encrypted WebRTC. It does not persist a recording file or upload to a cloud service. QR tokens remain stable for ten minutes unless consumed or explicitly replaced.

## Offline Wi-Fi and hotspots

Recording does not contact GitHub or any external server. Both a router without internet and a phone/desktop hotspot can work if they permit client-to-client traffic. FrameLink discovers active private IPv4 interfaces instead of assuming a gateway address. Select the address reachable from your phone.

Allow TCP 8443 for HTTPS and local UDP for WebRTC. TCP 8444 serves public certificate setup only when enabled. Firewall rules are not changed automatically. No STUN or TURN service is configured. IPv6-only networks and restrictive client-isolated hotspots are not supported by this build.

If the selected address disappears, FrameLink stops the server and asks you to select the new address. Start it again and scan a fresh QR. The existing CA remains, while a new IP certificate is issued. The phone attempts bounded reconnections while its page stays open. Locking the screen or putting Safari/Chrome in the background may suspend streaming. Wake lock is requested where supported, but uninterrupted background capture is not promised.

## Recording

### Desktop Screen

Select a monitor on X11 or Windows, or use the Wayland portal chooser. Native output dimensions come from received capture frames, not the display's logical UI geometry. Custom output may reduce resolution; upscaling is rejected. Cursor inclusion is selectable for WGC; other backends use platform or portal policy.

30 and 60 FPS can be requested. **Measure performance** exercises conversion, composition, and encoding on captured content. The measurement updates Auto to select 15, 30, 60, or 120 FPS with measured encoding headroom; 120 FPS is offered only after sufficient measured capacity. This is a short estimate, not a sustained-performance guarantee, and a high-refresh display alone is not proof of achievable recording FPS. Actual capture-event FPS, encoded FPS, skipped output frames, encode latency, process CPU, memory, file size, and free space are displayed. Unchanged desktop content may produce no new capture events.

### Phone Camera

Connect the camera and wait for live preview before starting. The desktop writes the received video. Camera preview defaults to **Fill**, which center-crops without distortion. **Fit** preserves the full image and **Stretch** explicitly permits distortion; these display choices do not change the recorded source. Actual capture, sender, and receiver dimensions/FPS are available in the metrics tooltip.

The browser requests 1080p/30 initially, checks granted settings, sets a resolution-aware bitrate ceiling, and adapts using transport statistics. Higher modes are probed only when capability and measured headroom allow, outside an active recording. Browser and hardware limits still apply. Software recording uses H.264 CRF 20 with an ultrafast preset to reduce capture cost; this makes larger originals, which can be compressed after Stop.

Phone recording is recording-first rather than preview-latency-first: the saved phone path follows the granted camera cadence, keeps a separate timestamped recording buffer, and drains the short buffered interval on Stop before finalization. Preview may lag slightly during encoder jitter; it must not force the recording path to skip frames.

Stop and Disconnect are separate: stopping a recording leaves camera preview active until you disconnect.

### Both

Choose Picture-in-picture or Separate synchronized files in Settings before starting. Separate outputs share start time, pause intervals, frame timestamps, and a session ID. They contain the same selected mixed audio. Camera placement, opacity, border, mirroring, and rounded corners are configured in Settings. Settings are snapshotted when recording starts.

Choose a phone-disconnection policy: hide/black, freeze the last frame, pause, or stop and save. Paused recordings require an explicit Resume. Disconnection does not delete screen data.

### Audio

Phone mode records the phone microphone by default; allow microphone access in the browser and leave its microphone unmuted. Screen and Both modes default to the computer microphone, with phone audio disabled to avoid recording both microphones. The computer input initially follows the operating system's default microphone; choose the laptop's built-in microphone in Settings if another device is the default. The studio microphone button toggles the selected mode's microphone. Changing modes restores these defaults; Settings allows explicit overrides before recording. If no computer microphone is available, select an input or disable it before starting. Independent gain controls mix stereo audio with clipping protection. System audio is off by default and uses WASAPI loopback on Windows and an exposed monitor/loopback input on Linux. Select that input explicitly. Windows speaker capture uses WASAPI loopback through PyAudioWPatch; hardware verification is pending.

Sources are normalized onto a monotonic desktop timeline with a 120 ms playout delay. This is receive-time alignment with bounded jitter buffering, not calibrated cross-device clock synchronization. Physical phone lip-sync and long-duration drift require real-device validation; see known limitations.

### Reliability and recovery

On Windows, recordings are saved directly to the current user's Desktop by default, including a Desktop redirected to OneDrive or another location. Linux retains `~/Videos/FrameLink`. Change **Settings → Recording folder** to choose another location; an existing custom folder is preserved. The recording folder contains MP4 videos; library thumbnails and diagnostic files are stored in the application's data folder, without JPG sidecars.

Recordings are written directly to fragmented `.partial.mp4` files with short fragments and finalized as `.mp4`. Stop flushes encoders, decodes and validates the streams, syncs the file, and promotes it without replacing an existing recording. Stop immediately displays **Finalizing recording** and disables duplicate requests. The controller reaches **Completed** only after validation and library indexing; automatic compression is then queued. Final validation may take time on long recordings; the UI stays available. A low-space check stops recording before disk exhaustion when possible.

At startup, decodable interrupted MP4 files (and legacy MKV recordings) are remuxed into new `.recovered.mp4` files and indexed. Original fragments are retained even if recovery fails. Do not delete the only valid copy. Sudden power loss can still lose buffered data or damage the last fragment.

## Compression and Telegram exports

Automatic compression is enabled by default and configurable in Settings. **Fast** uses H.264 with a fast encoder preset; **Balanced** uses moderate quality and effort; **Maximum Compression** uses slower H.264 and higher CRF. These names describe tradeoffs, not guaranteed file sizes.

**Compress again** opens advanced settings: H.264, HEVC, AV1 (SVT or AOM), CRF, speed, downscaling, FPS reduction, audio bitrate, and an optional size target. H.264 MP4 is the compatibility preset. HEVC and AV1 require available encoders and compatible playback software; AV1 is not universally smaller or faster. Target size uses a single-pass bitrate estimate, so it can fail to meet a tight limit. Oversized output is not marked complete. Quality-mode size cannot be accurately predicted before encoding.

The **Telegram** preset exports H.264 MP4 at up to 1280 pixels wide and 30 FPS with 96 kbit/s audio. It does not send anything to Telegram or guarantee compliance with changing service limits. Select an appropriate target size yourself if needed.

The queue persists across restarts. In-progress jobs become Interrupted and can be retried. Cancellation retains the original and any partial output. **Keep Original** defaults to off for future recordings. Existing, imported and recovered recordings are always protected. A new recording can be replaced only by a smaller, durably saved MP4 after full decoding, frame/timestamp/audio checks and conservative sampled quality checks. Originals remain available throughout compression. Enabling Keep Original retains both files. The Library shows before/after sizes, export reduction and actual disk savings; retained originals do not count as reclaimed space.

Automatic exports that are not smaller or fail the quality gate are discarded, leaving the source intact. Manual downscaled or reduced-FPS exports retain their source; they cannot qualify for automatic replacement. Balanced uses H.264 CRF 22 with offline encoding rather than the low-latency recording settings. Quality checks sample up to 32 native-resolution frames, including edge detail; they are conservative safeguards, not a guarantee of lossless or identical visual quality. See [compression and retention](COMPRESSION_RETENTION.md). **Export** copies an existing file; it does not transcode it.

## Hardware acceleration and performance

Encoder discovery actually opens and encodes frames with NVENC, Quick Sync, AMF, and software H.264. A codec listed by FFmpeg alone is insufficient. Failed hardware initialization falls back to software before recording begins. Driver or runtime failures during recording preserve partial media and report an error.

Capture, WebRTC, encoding, and compression run in separate execution paths with bounded receive buffers. The current compositor uses CPU RGB frames and copies; this is not a zero-copy pipeline. High-resolution/high-FPS combined capture can exceed ordinary laptop capacity. A four-second overload window reduces encoding cadence, reports the change, and preserves resolution and timing. Reduce output size or FPS when skipped frames continue to rise. The metrics tooltip reports system memory pressure and GPU utilization when NVIDIA NVML or Linux AMD sysfs provides it; unsupported drivers show unavailable. These GPU interfaces require hardware verification. Compression pauses while recording or finalizing to reduce resource contention.

## Troubleshooting

| Symptom | Checks |
|---|---|
| Camera permission unavailable | Trusted HTTPS is required; verify IP certificate, phone clock, and root CA trust. |
| iPhone still warns after profile installation | Enable full trust, compare the installed root fingerprint, and run the served-certificate diagnostic. Check the current QR IP, SAN and device clocks; never bypass the warning. See [manual verification](CRITICAL_FIXES.md#manual-device-verification). |
| Phone cannot open the page | Confirm selected IPv4 address, same network, TCP firewall rule, and hotspot isolation. |
| Page opens but video never connects | Allow local WebRTC UDP; no TURN relay is available to bypass blocked LAN traffic. |
| Wayland preview never appears | Verify PipeWire and the correct ScreenCast portal; check the system permission dialog and portal logs. |
| Windows capture fails | Select DXGI fallback in Settings; protected content and secure desktops cannot be recorded. |
| Audio is silent | Check both phone and desktop audio toggles, selected input device, and OS microphone permission. |
| Recording stops or drops frames | Inspect the error, disk space, encode latency, thermal load, and output resolution/FPS. |
| Interrupted recording | Keep `.partial.mp4`; relaunch to attempt recovery. |
| Compression fails | Read queue Details; original media is retained. Try H.264 or a less restrictive target. |

## Build from source

Use Python 3.12 or newer, 64-bit, and a supported OS. Native Qt and FFmpeg functionality comes from pinned wheel dependencies. A C++ compiler is not needed for the normal wheel-based build.

```sh
python -m venv .venv
# Linux
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m framelink.app
```

Build and verify a portable bundle **on each target OS**:

```sh
python -m pytest -q
python -m ruff check src tests tools
python tools/package.py
# Linux only, after the portable build:
python tools/package_appimage.py
```

`--smoke-test` opens the actual UI, writes and validates a one-second video, and exits. `--capture-test DIRECTORY` requests genuine desktop capture, records five seconds, validates the saved file, and exits. The latter needs a usable desktop and Wayland permission. FFprobe is used by the test suite when installed. Synthetic source tests exercise media mechanics and WebRTC transport; they do not certify physical cameras or operating systems.

## Architecture

The repository uses a Python application layer with Qt 6 native capture and UI, PyAV/FFmpeg media libraries, aiortc transport, and SQLite. This avoids a separate signaling daemon and keeps one media clock and storage model. It deliberately differs from the suggested C++/QML layout; see [architecture](ARCHITECTURE.md) for tradeoffs.

```text
src/framelink/
  capture/       Native desktop and audio adapters
  media/         Buffers, clocks, compositor, codec probing, media validation
  recording/     State machine integration, muxing, finalization, recovery
  compression/   Persistent export queue and transcoding
  networking/    Local interfaces, HTTPS signaling, WebRTC receiver
  security/      Pairing tokens, rate limits, local certificate identity
  storage/       SQLite library and settings
  ui/            Native Qt desktop interface
  web/           Offline mobile camera and certificate setup pages
packaging/       Native portable bundle definitions
.github/workflows/  Separate Windows/Linux builds and gated publication
```

## Privacy and security

No analytics, cloud uploads, external signaling, or embedded credentials are included. The camera server starts only when requested and binds one selected private IPv4 address. Pairing secrets are not included in server access logs. Session credentials remain in browser memory. The server validates request origins, host headers, content type, size, session authorization, and local ICE candidates. It limits pairing/signaling requests and accepts one paired phone. QR codes are bearer credentials: do not share them.

The public certificate setup endpoint is separate from camera/signaling endpoints. CA private keys stay in the user application-data directory with restrictive Unix permissions; Windows relies on the user profile's ACL. Local users with access to your account or a compromised desktop can access your media and keys. Root trust must be removed manually on the phone when decommissioning the desktop. See [security](../SECURITY.md).

## Known limitations

See [verification](VERIFICATION.md) for the current blockers. In particular: physical Android/iPhone trust and streaming, Windows 10/11 display and audio capture, offline hotspot operation, and sustained A/V synchronization require real hardware verification. Windows Server CI capture does not certify Windows client hardware. Zero-copy composition and IPv6-only pairing are not implemented. Windows 11 system backdrop integration uses the supported DWM API where available; Linux and unsupported Windows versions use opaque surfaces. GPU metrics depend on driver interfaces. AppImage extraction and execution are tested in Ubuntu CI; normal FUSE launch also passed locally on Arch. Clean-machine portability still requires validation. No production compatibility or performance guarantee is made for these gaps.

## License and contributions

FrameLink is licensed under GPL-3.0-or-later. Qt, FFmpeg, codecs, and other dependencies retain their own licenses. See [third-party notices](../THIRD_PARTY_NOTICES.md). Binary publication is gated on a dependency license/source-distribution audit; bundling a library does not remove its obligations.

Contributions should include focused tests, platform-specific evidence where relevant, and accurate documentation. Follow [CONTRIBUTING.md](../CONTRIBUTING.md). Do not upload private recordings, pairing tokens, certificate keys, or device identifiers to issues.
