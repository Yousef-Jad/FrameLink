# README compression evidence

Rechecked on 2026-10-13. The README uses an actual native desktop recording and its matching export, captured over a clean desktop workspace on Hyprland, displaying cosmic wallpaper details and geometric clock widgets without terminal commands or sensitive parameters.

## Matching files

Paths are relative to the repository's local test artifacts. These videos are not distributed with the documentation; hashes and FFprobe results are recorded in [the evidence manifest](verification/readme-compression.json).

- Original: `artifacts/clean-comparison/FrameLink-desktop-orig.mkv`
- Export: `artifacts/clean-comparison/FrameLink-desktop-compressed.mp4`

| Property | Original | Compressed |
|---|---|---|
| File bytes | 1,471,470 | 805,721 |
| Decimal MB (1 MB = 1,000,000 bytes) | 1.471470 | 0.805721 |
| Container | Matroska | MP4 |
| Video codec | H.264 | H.264 |
| Resolution | 1920×1080 | 1920×1080 |
| Decoded video frames | 900 | 900 |
| Audio | None | None |

Size reduction is `100 × (1 − 805,721 / 1,471,470) = 45.2438%`. Rounded README values are 1.47 MB, 0.81 MB and 45.2% saved. Both files remain present, so this comparison itself does not reclaim disk space.

The original runs 30.000 seconds (900 frames at 30 fps). Full decoding with FFprobe's `-count_frames` confirms both frame counts. FFmpeg also decoded both complete files without reporting errors. There is no audio in this pair, so it provides no audio quality or synchronization evidence.

## Actual export settings

The export was produced via FrameLink's native compression engine using `libx264`, CRF 26, preset `medium`, no scaling, no frame-rate reduction, and audio bitrate 128,000 bit/s.

H.264 re-encoding is lossy. Equal frame counts and resolution do not establish lossless quality. File-size savings depend on source complexity and original encoder settings. This example is not a comparison of HEVC or AV1, a long-duration benchmark, or a Telegram playback test. See [current compression verification](COMPRESSION_VERIFICATION.md) for separate experiments.

## Image construction

`assets/compression-comparison.png` is a lossless 1920×1080 RGB PNG:

1. Decode each video in presentation order with PyAV. Select the first frame with a timestamp at least 10 seconds.
2. Both selected frames are zero-based frame 300, with presentation timestamp exactly **10.000 seconds**. No seek approximation, independent scaling, sharpening, blur, or color correction is applied.
3. Copy pixels x=0…959 from the original and x=960…1919 from the export. Add a one-pixel vertical divider at x=960 and restrained ORIGINAL/COMPRESSED badges centered in each half.
4. Extract the exact same 90×38 region at coordinates `(x=464, y=292)` from both original and compressed source frames, showing the clean geometric clock date text `13 OCT`.
5. Enlarge each crop using 8× nearest-neighbor scaling (no bilinear or bicubic interpolation) to preserve exact pixel values.
6. Display both magnified detail regions in dark-styled cards at the bottom of the comparison, with a subtle outline indicating where the detail crop was extracted.

To inspect the streams independently:

```sh
ffprobe -v error -count_frames -show_streams -show_format -of json ORIGINAL.mkv
ffprobe -v error -count_frames -show_streams -show_format -of json COMPRESSED.mp4
ffmpeg -v error -i ORIGINAL.mkv -f null -
ffmpeg -v error -i COMPRESSED.mp4 -f null -
```

## Pairing screenshot provenance

`assets/framelink-phone-pairing.png` was captured directly from the actual Qt MainWindow running on Wayland, in Phone mode and the application's dark theme. For safe documentation capture, hardware/server startup was disabled using the existing smoke flag; no camera connection or recording is implied. The existing QR renderer received the deliberately invalid URL `https://framelink.invalid/#demonstration-only`, and the existing status field labels it **Demonstration QR · not connectable**. `.invalid` cannot be a usable local pairing address. No private address, bearer token, certificate key or real device identity is present. The image was not composited or generated.
