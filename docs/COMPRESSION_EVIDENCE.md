# README compression evidence

Rechecked on 2026-10-12. The README uses an existing native Hyprland screen recording and its matching export, not stock imagery or a simulated recording. The recorded desktop displayed FrameLink's continuous-motion test window, with text and moving fine-edged shapes. This simple scene is not representative of every desktop or camera recording.

## Matching files

Paths are relative to the repository's local test artifacts. These videos are not distributed with the documentation; hashes and FFprobe results are recorded in [the evidence manifest](verification/readme-compression.json).

- Original: `artifacts/native-final-30/recordings/FrameLink-20261012-123001-34224f-screen.mkv`
- Export: `artifacts/native-final-30/recordings/FrameLink-20261012-123001-34224f-screen-export-21f02e96.mp4`

| Property | Original | Compressed |
|---|---|---|
| File bytes | 10,916,125 | 4,519,737 |
| Decimal MB (1 MB = 1,000,000 bytes) | 10.916125 | 4.519737 |
| Container | Matroska | MP4 |
| Video codec | H.264 | H.264 |
| Resolution | 1920×1080 | 1920×1080 |
| Decoded video frames | 899 | 899 |
| Audio | None | None |

Size reduction is `100 × (1 − 4,519,737 / 10,916,125) = 58.5958%`. Rounded README values are 10.92 MB, 4.52 MB and 58.6%. Both files remain present, so this comparison itself does not reclaim disk space. This is a historical MKV recording; current recordings use fragmented MP4.

The original runs approximately 30.033 seconds. Full decoding with FFprobe's `-count_frames` confirms both frame counts. FFmpeg also decoded both complete files without reporting errors. There is no audio in this pair, so it provides no audio quality or synchronization evidence.

## Actual export settings

The matching job in the read-only historical SQLite library specifies `libx264`, CRF 24, preset `medium`, no scaling, no frame-rate reduction, audio bitrate 128,000 bit/s (unused here), and no target size. These historical settings differ from the current Balanced preset; the screenshot does not establish results for current defaults.

H.264 re-encoding is lossy. Equal frame counts and resolution do not establish lossless quality. File-size savings depend on source complexity and original encoder settings. This example is not a comparison of HEVC or AV1, a long-duration benchmark, or a Telegram playback test. See [current compression verification](COMPRESSION_VERIFICATION.md) for separate experiments.

## Image construction

`assets/compression-comparison.png` is a lossless 1920×1080 RGB PNG:

1. Decode each video in presentation order with PyAV. Select the first frame with a timestamp at least 10 seconds.
2. Both selected frames are zero-based frame 300, with presentation timestamp exactly **10.000 seconds**. No seek approximation, independent scaling, sharpening, blur, or color correction is applied.
3. Copy pixels x=0…959 from the original and x=960…1919 from the export. Add a one-pixel vertical divider at x=960 and small ORIGINAL/COMPRESSED labels in unobtrusive upper margins. Both halves retain their original coordinates and framing.

To inspect the streams independently:

```sh
ffprobe -v error -count_frames -show_streams -show_format -of json ORIGINAL.mkv
ffprobe -v error -count_frames -show_streams -show_format -of json COMPRESSED.mp4
ffmpeg -v error -i ORIGINAL.mkv -f null -
ffmpeg -v error -i COMPRESSED.mp4 -f null -
```

## Pairing screenshot provenance

`assets/framelink-phone-pairing.png` was captured directly from the actual Qt MainWindow running on Wayland, in Phone mode and the application's dark theme. For safe documentation capture, hardware/server startup was disabled using the existing smoke flag; no camera connection or recording is implied. The existing QR renderer received the deliberately invalid URL `https://framelink.invalid/#demonstration-only`, and the existing status field labels it **Demonstration QR · not connectable**. `.invalid` cannot be a usable local pairing address. No private address, bearer token, certificate key or real device identity is present. The image was not composited or generated.
