# Compression verification, 2026-10-12

This record describes local development verification, not Windows or physical-phone
certification. No release was published and no pre-existing user recording was
removed. Destructive replacement tests use newly generated fixtures only.

## Native ten-minute input

Actual Hyprland portal → PipeWire → Qt desktop capture recorded a visible moving
window alongside terminal text. This is native desktop capture, not feeding
synthetic frames directly to the recorder. The moving shapes are controlled visual
test content; these numbers do not represent arbitrary camera scenes or films.

The recording clock ran for 599.916 seconds after the first valid captured frame;
10.482 seconds waiting for portal permission was excluded. The finalized source is
1920×1080 H.264 MP4, 279,939,084 bytes, 15,026 decoded frames and 599.940 seconds.
Its actual average cadence is approximately 25.046 FPS. Other validation workloads
ran on the laptop during capture, so this is not a clean capture-performance test.
There is no audio track in this long screen sample; AAC, audio timing and source
selection are tested separately with real encoded tone fixtures.

The old benchmark harness reported `Recorder ended in Finalizing recording` after
its fixed 30-second join expired during full decode. The recorder continued,
finalized, validated and indexed the file successfully. That original report is
preserved, not rewritten as a pass. The harness now scales its finalization wait
with the recording duration. Independent full decode in the compression benchmark
confirms the finalized input. Native capture reported no backend errors. Sampled
mean process CPU was 107.6% (100% is one core), with recording RSS up to 365.34 MB.

Evidence: `artifacts/compression-10min/native/`, including the original harness log,
SQLite library, finalized MP4 and recorder performance report. Source SHA-256:
`4dfeaf58fe96f177bf8c8aa4fcc9fe14d371eb3a5d522a10717f2534ea44d80a`.

## Full-duration result

The complete 599.94-second recording was encoded and verified, not estimated from
the short excerpt. Balanced H.264 CRF 22 medium produced:

| Measurement | Source | Export |
|---|---:|---:|
| File bytes | 279,939,084 | 59,478,817 |
| Decimal MB | 279.94 | 59.48 |
| Decoded frames | 15,026 | 15,026 |
| Resolution | 1920×1080 | 1920×1080 |
| Decoded duration | 599.939926 s | 599.939927 s |
| Actual average cadence | 25.04584 FPS | 25.04584 FPS |

Size reduction: **78.75294%**, or **220,460,267 bytes**. Measured encoding plus
integrity/quality verification took **799.51 seconds (13 minutes 19.51 seconds)**.
This wall time includes overlapping local checks and must not be presented as an
isolated hardware benchmark. Every decoded video frame's relative timestamp
passed comparison; all 32 quality samples passed. Mean block SSIM was 0.998013,
minimum sampled SSIM 0.996650, mean luma PSNR 45.7165 dB, and minimum sampled edge
PSNR 36.8434 dB. Full decode and FFprobe both confirmed H.264 MP4 and 1080p output.

The benchmark deliberately retains its source for comparison; its SHA-256 remained
unchanged. The size difference is reclaimable by replacement, not space already
freed in this retained benchmark directory. Actual source unlink and durable library
replacement are verified on new disposable MKV/MP4 fixtures. No existing user
recording was opted into cleanup or deleted.

Evidence: `artifacts/compression-10min/verified/report.json`,
`artifacts/compression-10min/source-ffprobe.json`,
`artifacts/compression-10min/verified/full-ffprobe.json`, and
`artifacts/compression-10min/verified/full-balanced.mp4`.

## Codec comparison

All codecs actually initialized and produced decodable 1080p MP4. Each used the
same approximately 30-second, 830-frame native excerpt, 13,795,641 bytes. Elapsed
times include encoding, full integrity validation and the quality gate. Other
local checks overlapped some runs; these are observed wall times, not isolated
throughput rankings. CRF numbers across codecs are not quality-equivalent.

| Encoder / settings | Output bytes | Reduction | Elapsed | Mean luma block SSIM | Mean PSNR | Minimum edge PSNR |
|---|---:|---:|---:|---:|---:|---:|
| H.264 / CRF 22 medium | 2,899,539 | 78.98% | 51.80 s | 0.99777 | 45.76 dB | 36.84 dB |
| H.265 / CRF 22 medium | 3,797,406 | 72.47% | 87.56 s | 0.99757 | 46.35 dB | 39.11 dB |
| AV1 SVT / CRF 28 preset 6 | 3,342,375 | 75.77% | 49.67 s | 0.99811 | 47.06 dB | 41.57 dB |

All three preserve 830 frames and pass the conservative gate. H.264 is the smallest
at these particular tested settings; no universal codec ranking is implied. H.264
remains the default for compatibility and Telegram export. HEVC and AV1 are advanced
choices; inline Telegram playback across devices was not tested and is not claimed.

Native-resolution crops of the same decoded frame are saved as
`artifacts/compression-10min/verified/text-*.png`. Inspection of source and H.264
crops showed readable text retained; this is a spot check, not a guarantee about
every frame. The internal metric is block SSIM, not VMAF or FFmpeg's exact SSIM
implementation. See [quality policy](COMPRESSION_RETENTION.md).

## Defect found by the long-input investigation

The initial H.264 excerpt export incorrectly rounded the average source rate to
26 FPS, retaining only 781 of 830 frames. The new destructive replacement gate
rejected it. Native-rate compression now uses a 90 kHz timestamp grid rather than
rounding to average FPS. Re-running the excerpt preserved all 830 frames and passed
quality validation. A dedicated regression compares every timestamp for irregular
source cadence. The abandoned outputs under `artifacts/compression-10min/encoded/`
are not approved replacements or successful benchmark results.

## UI and failure coverage

Actual Qt screenshots are in `artifacts/compression-ui/`: the disabled-by-default
Keep Original setting and a library card showing measured before/after bytes,
reduction and actual reclaimed bytes from a new fixture replacement. Small files
use KB/B rather than rounding to misleading zero MB.

Regression coverage includes old-schema migration, protected existing files,
Keep Original at Start and during compression, actual MKV/MP4 replacement, source
mutation, corruption, non-beneficial size, quality failure, reduced FPS, cancellation,
database failure before replacement, post-unlink statistics failure, unlink failure,
delayed audio start, source routing, repeated Stop and automatic compression in all
recording modes, and encrypted remote recording controls.

Final local suite: **165 tests passed in 84.43 seconds**, with Ruff and whitespace
checks passing. Report: `artifacts/compression-cleanup-tests.xml`. Runtime Windows
verification was not performed; the writable fsync handle follows the existing
Windows-safe finalization convention.

Physical Windows filesystem sharing/permissions, Windows capture, physical mobile
browsers and long-duration A/V drift remain unverified on this revision. Existing
release gates stay closed.
