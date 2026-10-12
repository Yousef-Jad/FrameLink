# MP4 output and Windows Desktop default

New recordings use MP4 in Screen, Phone, Both/PiP and Both/separate modes.
Windows defaults directly to the user's Desktop through Qt's native
`QStandardPaths.DesktopLocation`, respecting redirected/OneDrive Desktop folders.
Linux retains `~/Videos/FrameLink`. An explicitly selected output folder still
wins over the default.

Recording writes fragmented `.partial.mp4` with short fragments for interruption
recovery. Stop flushes the encoder, fsyncs, fully decodes and validates the media,
then promotes it to `.mp4` without overwriting another file. Automatic compression
creates a separate validated MP4; the later [retention policy](COMPRESSION_RETENTION.md)
may replace eligible future originals after integrity, quality and size checks.
Existing recordings remain protected. No MKV recording
or JPG sidecar is generated. PNG thumbnails and performance/session JSON are
kept in `recording-cache/` under the application's data directory.

Recovery remuxes decodable interrupted MP4 or legacy MKV to a new
`.recovered.mp4`. It leaves interrupted originals untouched and ignores incomplete
compression exports. Already finalized MP4 files are indexed after a crash between
promotion and database commit. Existing user recordings are not deleted or
converted in place.

## Executed verification

- 134 Python/Qt/media/security tests passed; Ruff and diff whitespace checks passed.
- Real Qt Stop clicks and two consecutive sessions in all four layouts produced
  only MP4 files in the output directory. Automatic compression completed; cache
  thumbnails were outside that directory. Encoder-error and preparation-cancel
  behavior retained partial files.
- Actual subprocess termination followed by recovery passed for both MP4 and
  legacy MKV, with and without audio. Recovered output was MP4 and the source
  survived. Orphan MP4 indexing and exclusion of interrupted exports passed.
- Redirected Windows Desktop path selection and preservation of an explicit
  output setting passed automated tests on Linux with a substituted Qt location.
- Native Qt X11 under Xvfb: H.264 MP4, 1280×720, 152 fully decoded frames,
  5.067 seconds, 30.00 measured FPS. The output directory contained just the MP4.
  Evidence: `artifacts/mp4-native-x11/ffprobe.json` and `run.log`.
- Chromium with a synthetic moving 1080p camera and microphone produced H.264/AAC
  MP4: 66 decoded frames, 2.240 seconds, 29.46 measured FPS. Both streams begin at
  timestamp zero. Strict TLS, reconnect, pause/resume and camera shutdown passed
  without page errors. Evidence: `artifacts/browser-mp4-output/result.json` and
  `ffprobe.json`.

This is source-level and Linux runtime verification. Windows Desktop resolution
and Windows media-player playback have not been exercised on a physical Windows
machine. The browser camera fixture is not an Android or iPhone device. Earlier
native Wayland benchmarks used MKV and remain historical evidence rather than
verification of this MP4 revision. Release/package gates remain closed.

## Changed implementation

- `storage/paths.py` and `ui/window.py`: native Desktop default, custom paths retained.
- `media/encode.py`: fragmented MP4 recording option; compression remains standard MP4.
- `recording/engine.py`: MP4 names, application-data cache, MP4 recovery and indexing.
- `app.py` and `tools/capture_check.py`: runtime smoke/capture checks expect MP4.
- Recording, recovery, output-path and UI regression tests; README updated to match.

Source paths above are relative to `src/framelink/` unless stated otherwise.
