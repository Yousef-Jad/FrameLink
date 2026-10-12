# Recording performance verification

This document records the local performance work for the bounded capture,
source-driven recording, and telemetry changes. Synthetic runs isolate the
pipeline and are useful for regression comparison; they do not replace native
PipeWire/portal or physical-device verification.

## What is measured

The recorder now keeps separate values for source frames, unique source frames,
encoded frames, output interval percentiles, dropped frames, skipped source
frames, duplicated frames, queue depth, stage timings, CPU, memory, and output
size. `MediaWriter` additionally records conversion, encode, mux/write, and
final disk-flush timings. The final report is written under the application data folder
as `recording-cache/<recording>.performance.json`. Older benchmark artifacts
retain their original sidecar locations.

The UI diagnostics show the active source's measured capture FPS, unique source
count, queue depth, drops, duplicates, output FPS, CPU, memory, file size, and
free disk space. The detailed report remains the authoritative evidence.

## Local synthetic results

All runs used the same moving-frame source and the same local software encoder.
The relevant JSON reports are retained under `artifacts/`.

| Scenario | Result | Evidence |
|---|---:|---|
| Screen, 1920×1080, 30 FPS | 30.0 encoded FPS | `artifacts/performance-final-1080-30/result.json` |
| Screen, 1920×1080, 60 FPS | ~47 encoded FPS | `artifacts/performance-final-1080-60/result.json` |
| Both/PiP, 1920×1080, 30 FPS | ~30 encoded FPS, 33.3 ms output interval | `artifacts/performance-after-both30-latest/result.json` |

The Both/PiP run improved from roughly 22–24 FPS in the baseline report to
approximately 30 FPS after the fast RGB compositor and source-driven recorder
changes. The final run reported no duplicated output frames. Its measured
source-to-encoder latency was approximately 119 ms. The screen 60 FPS result
is intentionally reported as approximately 47 FPS: 60 FPS is not sustainable
on this host and is not treated as a pass.

A post-change telemetry smoke run also confirmed that finalization records an
actual file flush: the 1-second 1920×1080 screen report measured `3.83 ms` for
`disk_flush_ms` (`artifacts/performance-telemetry-final/result.json`). This is a
flush-call measurement, not a universal storage benchmark.

The encoder probe opens each candidate encoder, sends a frame, and receives an
encoded packet. On this Linux host, `libx264` passed; NVENC, QSV, and AMF did
not initialize successfully and therefore are not selected as working
hardware encoders.

## Native capture investigation, 12 October 2026

These retained measurements predate the MP4-only output change. Their MKV paths
are historical evidence; new recordings use MP4. See [MP4 output verification](MP4_OUTPUT.md).

The current native tests use the physical eDP-1 display at 1920×1080/60 Hz,
Hyprland, xdg-desktop-portal-hyprland, PipeWire and Qt 6.12. Portal approval is
performed through the normal screen picker. Permission waiting ends only after
a valid RGB frame arrives; recording measurement starts after encoder preparation.
The Qt event loop continues during measurement and asynchronous finalization.

The earlier portal failure (`Unavailable cursor mode 1`) was a backend issue.
The configured Hyprland portal now delivers actual native frames. A pending
permission dialog is not a capture failure. Qt's current public capture interface
does not expose portal restore-token reuse; a single approved session is reused
for sequential scenarios instead.

### Findings and changes

- The old benchmark pumped Qt events manually and slept 20 ms per iteration.
  The new native suite and the repaired benchmark run a normal Qt event loop.
  The historical 14.70 FPS run cannot be attributed solely to the encoder.
- The generic `QVideoFrame.toImage()` path cost approximately 9.5–11 ms per 1080p
  BGRA frame. Mapping CPU RGB frames and using QImage conversion reduced the
  measured median to approximately 1 ms. Unsupported layouts, rotations and
  GPU/YUV formats still use Qt's conversion. Borrowed buffers never escape
  unmap; recordings retain owned pixels.
- Resetting a rate-limit deadline on each callback discarded valid frames after
  small scheduling variations. Admission now uses a fixed timestamp grid.
- Preview startup requested 60 FPS even when recording selected 30. Qt applies
  its maximum rate when starting the native stream. Startup now negotiates the
  selected rate; explicitly changing FPS restarts an idle preview through the
  portal. Automatic selection is bounded by measured capture **and** encoding,
  as well as display refresh; a fast encoder no longer implies fast capture.
- Qt 6.12 sets a screen frame's start timestamp to the previous grab's end.
  The first interval can include the entire permission wait. Normalization now
  uses this grab's end timestamp, with start/arrival fallback when unavailable.
- Eviction of already consumed preview history was incorrectly counted as
  recording loss. Metrics now distinguish retained history from unread backlog,
  and count overflow only for an active reader. CPU sampling reuses a Process
  instance so successive samples measure an interval instead of always zero.

The first negotiated-30 trial (`native-handoff-negotiated30`) lost its visible
motion window. It verifies finalization/compression, but is excluded from
continuous-motion performance conclusions. `native-final-30` repeats those
scenarios with the motion window checked through Hyprland. Phone frames in native
Both tests are explicitly a **synthetic 640×360 camera fixture**, not a device.

Detailed measurement tables and retained evidence are recorded below after full
decode and timestamp analysis. CPU is process CPU (100% = one logical core);
RAM is peak sampled RSS, not system usage. GPU utilization on this Intel host is
unavailable and remains null.

### Native results

Each recording stage requested 30 seconds; the source was the physical display.
The motion source was fullscreen for screen-only runs and tiled alongside the
preview for preview/Both stages. Scene complexity therefore differs. The 30 FPS
suite also ran a small synthetic phone producer. These are single-host observations,
not universal performance promises or a controlled CPU benchmark across scenes.

| Scenario | Sink FPS | Saved FPS (full decode) | Process CPU mean | Peak sampled RSS |
|---|---:|---:|---:|---:|
| Screen, selected/native 30 | 29.93 | 29.93 | 78.9% | 369 MiB |
| Preview only, native 30 | 29.30 | — (29.13 preview updates/s) | 58.1% | 430 MiB |
| Preview + Screen, native 30 | 29.20 | 29.20 | 122.1% | 451 MiB |
| Native screen + fixture PiP, native 30 | 28.03 | 28.04 | 100.7% | 470 MiB |
| Native screen + fixture, separate files | 29.10 | 29.10 | 84.4% | 495 MiB |
| Screen, selected/native 60 | 32.63 | 32.65 | 80.4% | 341 MiB |

The first screen file contains 899 decoded frames over 30.033 seconds. The 60 FPS
file contains 980 frames over 30.017 seconds. Its nominal metadata says 60; its
actual cadence is 32.65. All five original files in the 30 FPS suite finalized,
were indexed, and automatically produced validated MP4 exports. Originals remain.
The same Recorder ran Screen, preview + Screen, PiP and separate-file sessions
consecutively after Stop without restarting the process.

The final 60 FPS diagnostic logged each PipeWire receipt inside Qt. During
recording it saw 979 incoming buffers at 32.63/s, matching Qt sink delivery at
32.63/s. Conversion and recording therefore do not account for the missing
27 FPS in that run: the limit is upstream of Qt's application-facing sink, in
the native portal/PipeWire delivery path. A minimal probe without conversion or
encoding delivered 37.80 FPS at requested 60. Raising the probe request to 120
on the same 60 Hz monitor still delivered only 36.71 FPS. This diagnostic did
not record or claim 120 FPS and no such workaround is enabled in production.
The precise internal compositor/portal scheduling or SHM-transfer cause is
**not yet isolated**; do not describe Qt polling or the encoder as a proven cause.

The [portal configuration documentation](https://wiki.hypr.land/hypr-ecosystem/user/xdg-desktop-portal-hyprland/)
distinguishes a maximum FPS from actual delivery and describes SHM's performance
cost. No host portal configuration was changed for these measurements.

Earlier evidence is retained rather than overwritten:

| Earlier run | Screen 30 | Screen 60 | Notes |
|---|---:|---:|---|
| Handoff `performance-native-wayland-1080-30-fixed2` | 14.70 | — | Manual Qt event pumping; 441 frames/30.000 s |
| Normal Qt event loop, old conversion (`native-handoff-baseline`) | 28.20 | 34.47 | Native stream requested 60 for both stages |
| Fast conversion, native still 60 (`native-handoff-after`) | 28.27 | 35.48 | Lower CPU/RSS, upstream delivery still limited |
| Final selected-rate negotiation (`native-final-30`, `native-final-60`) | 29.93 | 32.65 | Fresh approved sessions; varying native delivery |

With the native stream still requested at 60, the conversion-change comparison
recorded process CPU of 103.3% → 69.3% and peak sampled RSS of 517 → 349 MiB
for Screen/30; median conversion time fell from 9.53 to 1.02 ms. Saved cadence
changed only from 28.20 to 28.27 FPS, reinforcing the upstream delivery limit.
The earlier five-second handoff file was independently rechecked with FFprobe:
108 frames over 5.266 seconds (20.51 FPS), not a matched 30-second workload.

These runs demonstrate the conversion-cost reduction and correct default-rate
negotiation, but **do not demonstrate a sustained 60 FPS improvement**. PiP and
preview still miss some 30 FPS intervals. General release readiness remains
blocked; a successful capture file alone is not a stable-performance pass.

Raw reports and recordings are retained in `artifacts/native-final-30/` and
`artifacts/native-final-60/`. `native-final-frame-analysis.json` contains FFprobe
stream/container metadata, full decoded-frame counts, timestamp gaps and hashes.

### File integrity, frame pacing and browser regressions

Full decode and FFprobe passed for all six native MKV originals and five MP4
exports. Every export preserved its original's decoded frame count. All eleven
files had strictly increasing video timestamps and zero consecutive identical
luma hashes. This establishes distinct output images for these moving fixtures;
it does not imply that every compositor frame was captured.

| Original | Decoded frames | Timestamp FPS | Interval p50 / p95 / p99 | Maximum gap | Container bitrate |
|---|---:|---:|---:|---:|---:|
| Screen/30 | 899 | 29.933 | 33 / 34 / 34 ms | 67 ms | 2.91 Mbit/s |
| Preview + Screen/30 | 877 | 29.200 | 33 / 34 / 67 ms | 67 ms | 6.73 Mbit/s |
| PiP/30 | 842 | 28.033 | 33 / 66 / 67 ms | 67 ms | 4.12 Mbit/s |
| Separate screen/30 | 874 | 29.100 | 33 / 34 / 67 ms | 67 ms | 3.99 Mbit/s |
| Separate fixture phone/30 | 874 | 29.100 | 33 / 34 / 67 ms | 67 ms | 0.26 Mbit/s |
| Screen/60 | 980 | 32.633 | 33 / 50 / 50 ms | 50 ms | 2.59 Mbit/s |

Timestamp FPS uses `(frames - 1) / (last PTS - first PTS)`. Saved FPS in the
previous table uses frame count divided by validated video duration. Small
rounding differences are expected; neither uses nominal FPS as measured FPS.
The measured recording queue overflow was zero in each native stage. Detailed
p50/p95/p99 conversion, composition, encoder, mux and source-age timings, plus
fsync measurements, are in the [retained evidence](verification/native-performance-2026-10-12.json).
Source-age timing begins at Qt delivery; true compositor-to-file latency is not
instrumented and must not be inferred by adding unrelated percentile values.

The current browser regression used the same moving 1920×1080/30 fixture as the
preserved camera-quality baseline. Strict TLS 1.3 trust in an isolated NSS store,
secure context, encrypted WebRTC, pause/resume, camera reacquisition, reconnection,
desktop disconnect, late permission cancellation and fresh QR reuse all passed.
No certificate validation bypass was used and no page errors occurred.

- Granted camera settings: 1920×1080, 30 FPS.
- Sender: 1920×1080, 30 FPS; max bitrate 7.46 Mbit/s, sampled target 5.88 Mbit/s.
- Receiver during recording: 1920×1080, 30.11 FPS, sampled bitrate 6.53 Mbit/s,
  zero reported lost RTP packets.
- Saved: 1920×1080 H.264/AAC, 65 video frames, 2.176 seconds of video,
  29.87 measured FPS; container bitrate 15.14 Mbit/s. Audio and video start at 0.
- Original preserved quality baseline received/saved 640×360 from that 1080p
  source. The automated comparison verifies full source resolution now reaches
  the saved file, with no upscale or cadence regression.

Evidence: `artifacts/browser-performance-final/result.json`, `ffprobe.json`, and
`comparison.json`. These are desktop Chromium synthetic-camera measurements,
not proof of Android/iPhone camera quality, local Wi-Fi throughput or Safari trust.
The exact physical-device trust and recording checklist remains in
[CRITICAL_FIXES.md](CRITICAL_FIXES.md#manual-device-verification).

The current native X11 path was also exercised under Xvfb: H.264 1280×720,
154 decoded frames over 5.133 seconds, 30.00 actual FPS, Completed state and no
recorder error. Evidence: `artifacts/x11-performance-final/`. This verifies virtual
X11 capture, not a physical X11 desktop or Windows.

127 Python/Qt/media/security regressions passed. They include actual Qt Stop
clicks, repeated sessions, encoder failure, incomplete initialization, phone
interruption, encrypted software-peer remote Record/Stop in all layouts, and
automatic compression. Ruff, JS syntax, startup/quality-policy and phone control
Node checks also passed. Native benchmark media analysis and browser runs were
performed separately from performance measurement to avoid contaminating CPU/FPS.

### Encoder availability

System FFmpeg successfully initialized Intel VA-API on `/dev/dri/renderD128`
and encoded 90 H.264 1920×1080 frames with valid timestamps. However, the
application's PyAV build does not expose `h264_vaapi`. This is **not** a working
in-application hardware path; native app measurements use libx264. NVENC, QSV
and AMF remain unavailable on this host. A different encoder integration must
be validated separately before being offered as a working option.

### Reproducing native measurements

Keep the motion window visible on the selected display. Run one capture process
at a time and approve its normal portal picker. Do not run CPU-heavy decoding,
other benchmarks or test suites concurrently with the measurement.

```bash
.venv/bin/python tools/wayland_motion_source.py
PYTHONPATH=src QT_QPA_PLATFORM=wayland .venv/bin/python tools/native_performance_suite.py \
  --native-fps 30 --stages record30,preview30,preview-record30,pip30,separate30 \
  --compress --output artifacts/native-30
PYTHONPATH=src QT_QPA_PLATFORM=wayland .venv/bin/python tools/native_performance_suite.py \
  --native-fps 60 --stages record60 --output artifacts/native-60
.venv/bin/python tools/analyze_frame_pacing.py recording.mkv --output pacing.json
```

Consecutive decoded luma hashes detect repeated image content; timestamps measure
actual cadence rather than nominal stream metadata. A changing desktop can still
contain identical frames, so this metric is reported separately from source
sequence counts and recorder-generated duplication.

## Verification commands

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests tools
git diff --check
node --check src/framelink/web/camera.js
node --check src/framelink/web/quality.js
node --check src/framelink/web/recording.js
node --check tests/camera_startup.cjs
node --check tests/camera_quality.cjs
node --check tests/phone_recording.cjs
```

## Files changed in the native-performance follow-up

- `capture/desktop.py`: CPU RGB mapping, timestamp normalization, stable rate
  admission and bounded capture metrics.
- `media/buffers.py`: distinguish preview history from unread recording backlog.
- `media/performance.py`: select automatic FPS from capture and encoder capacity.
- `recording/engine.py`: skip stale preview backlog at start, reuse CPU sampler,
  session drop baselines, p99 timings, release consumers before validation.
- `ui/window.py`: negotiate selected capture FPS and restart an idle stream when
  the user explicitly changes its rate; include capture delivery in Auto selection.
- `tools/performance_benchmark.py`: native Qt event loop and preparation-aware
  measurement timing; `qt_screen_capture_probe.py`: clock starts at first frame.
- `tools/native_performance_suite.py` and `tools/analyze_frame_pacing.py`: sequential
  native scenarios, compression verification and decoded frame/timestamp analysis.
- `tests/test_performance_metrics.py` and `tests/test_ui.py`: jitter, pixel ownership,
  portal-wait timestamps, real queue overflow, selected-rate negotiation and Auto
  capability regression coverage. Existing Stop/network tests were rerun unchanged.

Paths above are relative to `src/framelink/` unless prefixed with `tools/` or
`tests/`. Documentation and evidence changes are in this file, `VERIFICATION.md`,
`release-verification.json` and `verification/native-performance-2026-10-12.json`.
No commits, pushes, packages or releases were created during this follow-up.
