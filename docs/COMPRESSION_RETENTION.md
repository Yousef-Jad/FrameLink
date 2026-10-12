# Compression, quality and actual disk savings

Automatic compression begins after recording finalization and validation. The
source stays intact throughout encoding and verification. Keep Original is off by
default. Turning it on retains the source even when a smaller export is valid.

## Eligibility and safe replacement

Only recordings made by the updated recording engine are eligible. Eligibility is
snapshotted at Start and written explicitly into the library after successful
finalization. Database migration gives every pre-existing row `cleanup_eligible=0`;
imports, recovered files and compressed exports also default to zero. No date-based
guessing or directory-wide deletion is used. Keep Original at Start or at cleanup
prevents removal. Old queue entries cannot make old recordings eligible.

This applies to future MP4 originals as well as an explicitly eligible MKV source.
Current recording creates fragmented MP4, not MKV. Existing MKV files are protected.

Replacement requires all of the following:

- Full output decode, non-empty video/audio, no corrupt decoded frames, the expected
  number of written frames and expected output dimensions.
- A full source decode and preservation of source frame count, dimensions and
  audio presence. Every video's frame timestamp is compared, with at most 40 ms
  relative pacing difference; total duration must differ by at most 100 ms. A/V
  start and end offsets must each change by no more than 50 ms.
- Up to 32 evenly spaced native-resolution frame pairs, including first and last,
  pass mean luma block SSIM ≥ 0.98, minimum sampled SSIM ≥ 0.95, mean luma PSNR
  ≥ 38 dB and minimum sampled sharp-edge PSNR ≥ 35 dB. Edge metrics specifically
  guard against softened text and fine boundaries. These project thresholds are
  conservative engineering choices, not a universal perceptual quality standard.
- The actual export byte count is strictly smaller than the source.
- Source identity (device, inode, byte count and modification time) is unchanged,
  its library path is unchanged, and it is not a symlink.
- The output has been promoted without overwriting another file, flushed to disk,
  and committed as the library's durable replacement before source unlink.

Rename and delete operations share a library mutation lock with final replacement.
Cancellation is checked before promotion and again before unlink. Database failure
before replacement, failed validation and inability to unlink all retain the source.
A crash between replacement and unlink can leave an extra source; cleanup is not
automatically retried. A crash after unlink retains the valid indexed MP4. Pending
statistics remain conservative rather than claiming space was reclaimed.

Automatic outputs that fail the quality gate or are not smaller are discarded.
Manual reduced-resolution or reduced-FPS exports can be saved, but cannot replace
their source. Cancelled or failed jobs may leave partial exports for diagnosis;
these are never used to authorize source deletion.

The Library reports decimal MB/GB before and after compression. It distinguishes
export size reduction from bytes actually reclaimed. Keep Original, protected
sources, unsuccessful cleanup and rejected exports all show zero disk space saved.
No pre-existing user recordings are deleted by this policy.

## Encoding and compatibility

Balanced uses software H.264, CRF 22, medium preset, native dimensions and cadence.
Exports use offline encoder settings and a longer keyframe interval instead of
the low-latency capture settings. This allows more efficient coding without using
aggressive quality loss as the primary size-reduction method.

Native-cadence exports preserve each source frame on a 90 kHz timeline. Rounding
the average source FPS previously merged some frames in variable-cadence screen
recordings; the ten-minute verification exposed that defect. Only an explicit
FPS reduction may now drop frames, and such exports cannot replace the source.

HEVC and AV1 remain explicit advanced choices. An encoder must initialize and
produce packets before benchmark results count as available. The benchmark uses
the same native screen clip for all codecs and reports sizes, elapsed time and
measured quality separately; identical CRF numbers across codecs are not equivalent
quality levels. No hardware acceleration is assumed from an encoder name.

The Telegram compatibility preset remains H.264/AAC MP4. Telegram documents MPEG4
video upload and document fallback for other formats; this does not establish
universal HEVC/AV1 inline playback on every client. No files are sent to Telegram
by this application or the benchmark. See [Telegram's video API documentation](https://core.telegram.org/bots/api#sendvideo).

Quality metrics are not proof of visually lossless output. Sampling may miss a
brief localized defect; enable Keep Original for archival material or when every
detail must be preserved. The internal metric is 8×8 luma block SSIM with separate
edge PSNR; it is not VMAF and is not claimed numerically identical to FFmpeg's SSIM
filter. See [FFmpeg's metrics documentation](https://ffmpeg.org/ffmpeg-filters.html#ssim).

## Reproduction

```
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_compression_cleanup.py
PYTHONPATH=src .venv/bin/python tools/compression_benchmark.py recording.mp4 --output artifacts/compression-comparison
```

The benchmark leaves its source unchanged, hashes it before and after, measures
each codec on the same 30-second excerpt, and encodes the complete input using
Balanced. The full encode is measured rather than extrapolated from the excerpt.
Its outputs are comparison artifacts, not user-library recordings eligible for
cleanup. Automated safety tests perform actual replacement only on fresh fixtures.

Measured ten-minute native capture, codec comparison, quality scores, actual elapsed
times and remaining platform limitations are recorded in
[COMPRESSION_VERIFICATION.md](COMPRESSION_VERIFICATION.md).
