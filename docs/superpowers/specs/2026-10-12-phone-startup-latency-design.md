# FrameLink Phone Startup Latency Design

## Goal

Make the phone camera startup path observable and low-latency, then remove the
verified causes of delayed first video while preserving 1080p/30 quality and
the existing TLS/pairing model.

## Scope

This change covers browser timing, WebRTC negotiation and reconnection,
aiortc receiver telemetry, desktop preview delivery, and the separation of
preview and recording frame retention. It does not change certificate
generation or trust policy, and it does not declare iPhone Safari trust fixed.

## Design

The browser records monotonic `performance.now()` marks for page navigation,
permission observation, `getUserMedia`, SDP offer creation, answer receipt, and
ICE connection. The desktop records `time.monotonic()` marks for the first
inbound RTP packet observed by receiver statistics, the first decoded frame,
and the first frame actually presented by the Qt preview. The report keeps the
two clock domains explicit and derives only durations that share a clock.

The receiver starts consuming the incoming video track as soon as the
`track` event fires, independent of recording. A short receiver-statistics
watcher records first RTP packet arrival without delaying `track.recv()`. The
decoded frame is inserted into independent buffers: a bounded latest-frame
preview buffer and a deeper timestamped recording buffer sharing the same
decoded NumPy array. Qt consumes only the preview buffer and acknowledges the
first displayed frame back to the server.

Phone state messages are ordered: pairing, negotiation, WebRTC connected,
awaiting video, receiving video, and preview ready. The browser does not show
live/connected status until the desktop sends a video-received acknowledgment.
Temporary WebRTC failures retain the session, camera stream, certificate, and
pairing address; bounded renegotiation retries reuse the existing session and
only a deliberate disconnect clears it.

## Error handling and compatibility

If an API cannot expose a permission transition or a receiver statistic, the
trace records `null` plus an explicit availability note rather than inventing a
timestamp. The existing no-STUN local ICE configuration remains in use.
Frame dimensions and granted browser settings remain authoritative; no
upscaling or quality-reducing startup fallback is introduced. Existing Windows,
Linux X11, and Linux Wayland capture paths are unchanged.

## Verification

Regression tests cover timing marks, first-frame acknowledgment, state ordering,
latest-frame preview behavior, independent recording retention, reconnection
without QR rotation, and the existing synthetic WebRTC path. The browser
integration report records all available T0–T8 marks, first-frame latency,
received FPS, and drops. A physical iPhone Safari run remains a separate manual
gate and must be reported as unverified when unavailable. HTTPS warnings are
never bypassed.
