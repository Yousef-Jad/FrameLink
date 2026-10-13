# iPhone video reliability investigation

## Confirmed defects and limits of diagnosis

The reported real Safari session is not available as a video file or correlated client/server logs in this checkout. Its initial loss of video cannot be attributed to ICE, codecs, camera suspension, or decoding from the displayed connection state alone. Physical iPhone reproduction remains required.

Code inspection and regression reproduction confirm that the mobile watchdog called `disconnect()` after 20 seconds without a desktop message. That stops all camera tracks, discards the pairing session, and returns the page to the connection UI. This explains how an interrupted heartbeat escalates into Camera Off and another permission request. Temporary transport recovery should not perform an explicit disconnect.

The desktop heartbeat only diagnosed zero frames since connection. It missed a stream stopping after its first frames and misleadingly described every connected-without-video case as missing RTP. The desktop Start availability checked controller state rather than fresh phone frames; the Recorder itself already rejected missing or stale phone input. Mobile status also inferred Live from recording state without a video acknowledgment.

## Changes

- Missing heartbeat retries negotiation with the existing session and live MediaStream. Reacquisition occurs only if no live video track remains. Explicit Disconnect still stops the camera and revokes the session.
- Five seconds without valid video produces `video-stalled`, independently of connected ICE/DTLS. A subsequent valid frame restores video acknowledgment. The mobile page retries the same session upon that status.
- Mobile Live requires desktop `video-received` or `preview-ready`; opening the local camera alone does not mean desktop readiness.
- Desktop Phone/Both Start requires a phone frame less than three seconds old. Recorder validation remains authoritative, including remote Record requests.
- Receiver diagnostics distinguish video RTP packets, decoded/buffered frames, unique Qt presentation callbacks, last receive/presentation times and frame age. Camera reports live/ended, muted/enabled, mute/unmute/end event counts, restart/reconnect counts, visibility and transport states.
- Late frames from a replaced peer are rejected after awaiting receive and conversion. TLS, certificate validation and pairing authorization are unchanged.

## Available verification

- Full local suite: 167 passed; focused network/remote/stall tests: 25 passed after the peer-race guard.
- Ruff, JavaScript syntax, startup/watchdog tests and remote Record/Stop acknowledgment tests passed.
- Local browser integration used Chromium's explicitly synthetic camera with a trusted temporary root, encrypted WebRTC, aiortc, VideoBuffer, actual Qt presentation and real MP4 recording. TLS endpoint validation passed, no page errors occurred, and the recording decoded to 66 H.264 frames at 1920×1080 with audio over 3.307 seconds. Startup snapshot: 266 video RTP packets, 73 decoded/buffered frames, live unmuted track, one camera acquisition, zero reconnects. These are transport/control checks, not iPhone quality measurements.
- The test harness pumps Qt briefly to acknowledge preview readiness; its single presentation callback is not sustained preview-FPS evidence. CPU-heavy synthetic 1080p integration averaged approximately 20 FPS and is not a performance guarantee.

Local artifacts: `artifacts/iphone-reliability-tests.log` and `artifacts/iphone-reliability-browser/result.json`. These checks preceded publication. Source publication does not certify physical Safari reliability or update older release binaries.

## Required iPhone verification

1. Use trusted HTTPS without bypassing Safari warnings. Start Phone and record client lifecycle plus desktop receiver diagnostics on the same timeline.
2. Confirm local track state, ICE/DTLS, packet count, decoded/buffered/presented count, and last frame ages before and during the failure. If packets rise but decoded frames do not, investigate codec/decode; if decoded/buffered frames rise but Qt presentations stop, investigate rendering. No packets alone does not establish an ICE fault.
3. Temporarily interrupt Wi-Fi and restore it. Confirm the pairing session/QR stays unchanged, the live camera is reused, and no extra permission prompt is requested by the application. Safari may still require user interaction when the OS ends the track.
4. Stop the sender's frames while transport remains connected. Confirm stalled status, disabled Phone/Both Start, preserved session, and automatic return to acknowledged video.
5. Test camera switching, actual ended tracks, foreground/background and screen lock, then remote Record/Stop and playable audio/video output. Safari background suspension cannot be prevented or promised away.

The first iPhone video-loss cause remains unverified until these correlated measurements are collected. This patch fixes confirmed lifecycle escalation and readiness defects; it does not certify Safari reliability.
