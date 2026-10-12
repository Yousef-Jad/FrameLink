# Phone Startup Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure T0–T8 and reduce phone-camera startup latency by fixing state acknowledgment, receiver telemetry, preview delivery, and reconnection behavior.

**Architecture:** Keep browser capture and aiortc on their existing execution paths. Add explicit browser/server/Qt timing records, consume the WebRTC track immediately, split preview and recording retention at the source boundary, and retain the current pairing session through temporary WebRTC failures.

**Tech Stack:** Python 3.12+, PySide6, aiortc, aiohttp, PyAV, NumPy, SQLite, browser JavaScript, Playwright Chromium, pytest, Node.js.

**Spec:** `docs/superpowers/specs/2026-10-12-phone-startup-latency-design.md`

## Global Constraints

- Preserve the existing FrameLink application and cross-platform Windows/Linux architecture.
- Do not bypass TLS validation, Safari warnings, or certificate trust requirements.
- Do not regenerate certificates or rotate QR/session credentials for temporary WebRTC reconnects.
- Do not lower capture resolution, frame rate, or bitrate solely to improve startup latency.
- Use actual granted camera settings and actual received frames; never upscale unsupported input.
- Keep recording frame retention independent from preview latest-frame rendering.
- Report physical iPhone verification honestly and do not publish a release while required gates remain unverified.

---

### Task 1: Add browser lifecycle timing marks

**Files:**
- Modify: `src/framelink/web/index.html`
- Modify: `src/framelink/web/camera.js`
- Test: `tests/camera_startup.cjs`

**Interfaces:**
- Produces `window.framelinkTiming` with browser monotonic marks and availability notes.
- Exposes `window.framelinkTiming.snapshot()` returning a JSON-safe object.

- [ ] **Step 1: Write the failing Node test**

  Load `camera.js` in a VM with fake DOM/media objects, call the timing helper,
  and assert the snapshot contains `t0_page_opened`, `t2_get_user_media_completed`,
  `t3_sdp_offer_created`, `t4_sdp_answer_received`, and `t5_ice_connected`, with
  marks monotonically ordered when present. Also assert an unsupported permission
  observer records `permission_observation: "unavailable"` rather than a fake mark.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `node tests/camera_startup.cjs`

  Expected: FAIL because the current mobile page has no lifecycle timing object.

- [ ] **Step 3: Implement the minimal browser timing API**

  Add an inline navigation mark before deferred scripts in `index.html`. In
  `camera.js`, add a monotonic mark helper based on `performance.now()`, observe
  the camera permission state only when `navigator.permissions` supports it, and
  mark `getUserMedia` completion, offer creation, answer receipt, and ICE
  connection at their exact existing await/event boundaries. Keep the timing
  object in memory only and include an explicit `clock: "performance.now"` field.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run: `node tests/camera_startup.cjs`

  Expected: PASS with no browser API errors.

### Task 2: Add server receiver timing, states, and acknowledgment

**Files:**
- Modify: `src/framelink/networking/server.py`
- Test: `tests/test_network.py`

**Interfaces:**
- `PhoneServer.metrics` gains `first_rtp_monotonic`, `first_frame_decoded_monotonic`, `first_preview_displayed_monotonic`, `first_frame_sequence`, `receiver_packets_lost`, `receiver_jitter`, and `receiver_bitrate`.
- `PhoneServer.mark_preview_displayed(sequence: int)` records T8 and sends the `preview-ready` acknowledgment once.
- Server phone status messages use `WebRTC connected · awaiting video frames`, `Receiving video (...)`, and `Preview ready (...)` distinctly.

- [ ] **Step 1: Write failing async tests**

  Extend the synthetic WebRTC test to assert that the state reaches receiving
  video, metrics include a first decoded timestamp and positive frame count,
  `mark_preview_displayed` changes the state to preview ready, and the paired
  data channel receives a `preview-ready` message. Add a test that a temporary
  peer close leaves `session_secret` and the existing token valid.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_network.py -m integration`

  Expected: FAIL because the current server has no first-frame timestamps,
  preview acknowledgment, receiver packet metrics, or preview-ready state.

- [ ] **Step 3: Implement receiver instrumentation without blocking decode**

  Start a short-lived asyncio stats watcher per peer. It polls `inbound-rtp`
  and matching transport/candidate stats until the first packet is observed,
  then records packet count, loss, jitter, and bitrate snapshots. Keep the
  existing `track_received` task and call `track.recv()` immediately. After
  `frame.to_ndarray` completes, record T7 before inserting into the source
  buffers. Send the first video acknowledgment through the data channel or
  queue it until the channel opens. Update heartbeat payloads with receiver
  diagnostics. Cancel the watcher during peer cleanup.

- [ ] **Step 4: Implement ordered server states and preview acknowledgment**

  Do not report the phone as live on `connectionState == "connected"`; report
  awaiting video. Change state only after the first decoded frame, and only
  change to preview ready from `mark_preview_displayed`. Add a helper for safe
  data-channel status sends so reconnect/close races do not raise into the
  receiver task.

- [ ] **Step 5: Run the focused tests and verify they pass**

  Run: `.venv/bin/python -m pytest -q tests/test_network.py -m integration`

  Expected: PASS with ordered states and receiver timing evidence.

### Task 3: Separate preview and recording frame retention

**Files:**
- Modify: `src/framelink/media/buffers.py`
- Modify: `src/framelink/networking/server.py`
- Modify: `src/framelink/ui/window.py`
- Modify: `src/framelink/recording/engine.py`
- Test: `tests/test_network.py`
- Test: `tests/test_preview_scaling.py`

**Interfaces:**
- `Sources.phone` remains the timestamped recording source for compatibility.
- `Sources.phone_preview` is a two-frame latest-frame source for Qt preview.
- The server publishes each decoded frame to both sources without copying the NumPy pixel array.
- `MainWindow.tick()` reads `phone_preview`, then calls `server.mark_preview_displayed(sample.sequence)` after `PreviewWidget.set_frame()`.

- [ ] **Step 1: Write failing buffer/UI tests**

  Assert that publishing more than the preview capacity leaves the newest
  preview sample available while the recording buffer retains the older sample
  needed by a timestamped lookup. Add a Qt test that the first phone sample
  causes `mark_preview_displayed` exactly once and updates the preview state.

- [ ] **Step 2: Run focused tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_network.py tests/test_preview_scaling.py`

  Expected: FAIL because `Sources.phone_preview` and the Qt acknowledgment do
  not exist.

- [ ] **Step 3: Add the independent source buffers**

  Add `phone_preview = VideoBuffer(2)` to `Sources`. In server cleanup and
  disconnect paths clear both phone sources. Publish the same decoded RGB array
  to both buffers, keeping the recording source capacity deep enough for the
  existing playout delay and the preview source latest-frame oriented.

- [ ] **Step 4: Route Qt preview and acknowledge actual presentation**

  Change phone preview reads in `MainWindow.tick()` to `phone_preview`, retain
  recording reads from `phone`, and call `mark_preview_displayed` only after
  `PreviewWidget.set_frame` has accepted the copied QImage. Keep the 125 ms Qt
  timer as the presentation cadence and expose preview latency in diagnostics.

- [ ] **Step 5: Run focused tests and verify they pass**

  Run: `.venv/bin/python -m pytest -q tests/test_network.py tests/test_preview_scaling.py`

  Expected: PASS; recording source history remains available while preview
  stays latest-frame oriented.

### Task 4: Fix browser state acknowledgment and reconnection reuse

**Files:**
- Modify: `src/framelink/web/camera.js`
- Modify: `src/framelink/web/quality.js`
- Modify: `src/framelink/web/index.html`
- Test: `tests/camera_startup.cjs`

**Interfaces:**
- Browser status stays `Connected · awaiting video` until the desktop sends
  `video-received` or `preview-ready`.
- `scheduleReconnect()` reuses the current session/token and existing stream;
  it does not call `disconnect()` merely because a retry budget was reached.

- [ ] **Step 1: Extend the failing JS test**

  Feed the fake data channel `ready`, `video-received`, and `preview-ready`
  messages and assert only the latter two change the status to live. Trigger
  repeated failed negotiations and assert `sessionStorage` credentials remain
  present and no `/pair` request or token rotation occurs.

- [ ] **Step 2: Run the test and verify it fails**

  Run: `node tests/camera_startup.cjs`

  Expected: FAIL because current `ready` immediately sets live status and
  retry exhaustion calls `disconnect()`.

- [ ] **Step 3: Implement acknowledgment-gated UI state**

  Handle server `status` values explicitly. Set `heartbeat` for all valid server
  messages, but set `LIVE TO STUDIO` only for `video-received` or
  `preview-ready`. Preserve camera controls while waiting for the first frame.

- [ ] **Step 4: Implement session-preserving retry behavior**

  On failed/disconnected peers, close only the current `RTCPeerConnection`,
  keep `stream`, `session`, and `token` state, and retry with bounded backoff.
  After the retry ceiling, leave the page in a retryable waiting state rather
  than revoking the session; an explicit Disconnect still clears everything.
  Reuse the same origin and do not touch certificate creation.

- [ ] **Step 5: Run JS checks and verify they pass**

  Run: `node tests/camera_startup.cjs && node tests/camera_quality.cjs && node --check src/framelink/web/camera.js && node --check src/framelink/web/quality.js`

  Expected: PASS.

### Task 5: Add end-to-end latency report and UI diagnostics

**Files:**
- Modify: `tools/browser_check.py`
- Modify: `src/framelink/ui/window.py`
- Modify: `docs/CRITICAL_FIXES.md`
- Create: `tests/test_startup_report.py`

**Interfaces:**
- Browser integration writes `startup_timing` with browser marks, desktop
  marks, stage durations, first-frame latency, effective received FPS, and
  explicit unavailable fields.
- `MainWindow` diagnostics tooltip includes first decoded/displayed timestamps,
  packet loss, jitter, and preview latency when a phone server is active.

- [ ] **Step 1: Write failing report-format tests**

  Give `tools/browser_check.py` report data with all marks and assert the report
  contains T0–T8 names, monotonic same-clock deltas, `first_frame_latency_ms`,
  and `received_fps`. Give it a report with unavailable permission/packet marks
  and assert the output retains `null` plus the availability reason.

- [ ] **Step 2: Run focused tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_startup_report.py`

  Expected: FAIL because startup timing is not currently emitted or normalized.

- [ ] **Step 3: Implement report extraction and live diagnostics**

  Capture the browser timing snapshot after first preview acknowledgment and
  combine it with the server metrics and recorder timing. Compute browser-only
  T0–T5 deltas and desktop-only T6–T8 deltas; label cross-clock end-to-end
  values as unavailable unless a synchronized request boundary exists. Add the
  fields to the JSON result and metrics tooltip without changing TLS behavior.

- [ ] **Step 4: Run focused tests and the synthetic integration**

  Run: `.venv/bin/python -m pytest -q tests/test_startup_report.py`

  Then run: `FRAMELINK_CAMERA_FIXTURE=artifacts/critical-fixes/camera-1080.y4m FRAMELINK_BROWSER_OUTPUT=artifacts/delay-after .venv/bin/python tools/browser_check.py`

  Expected: PASS and a report containing measured first RTP, decode, and Qt
  display timestamps.

### Task 6: Full regression and release gate review

**Files:**
- Modify: `docs/VERIFICATION.md`
- Modify: `docs/release-verification.json`
- Modify: `README.md`

- [ ] **Step 1: Run the focused regression suite**

  Run: `.venv/bin/python -m pytest -q tests/test_network.py tests/test_preview_scaling.py tests/test_ui.py tests/test_camera_quality.py tests/test_startup_report.py`

- [ ] **Step 2: Run the complete source suite and static checks**

  Run: `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check src tests tools`.

- [ ] **Step 3: Inspect media and startup evidence**

  Run FFprobe on the synthetic output and verify width, height, FPS, bitrate,
  duration, and decoded frame count. Inspect the startup report for the first
  frame latency and stage that dominates it. Run preview checks and JS syntax
  checks again.

- [ ] **Step 4: Update verification documentation honestly**

  Record measured synthetic results and state that physical iPhone Safari,
  rotation, Wi-Fi interruption, and warning-free trust remain manual gates when
  no device is available. Keep the TLS issue explicitly separate and do not
  mark `ios_safari` or the release gate verified.

- [ ] **Step 5: Run the release gate**

  Run: `.venv/bin/python tools/release_gate.py docs/release-verification.json`.

  Expected: the gate remains nonzero until all unrelated platform/device and
  distribution requirements are actually verified; no release is published.
