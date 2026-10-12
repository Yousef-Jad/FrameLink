# Recording mode audio defaults

Phone selects the phone microphone. Screen and Both select the computer microphone
and disable phone audio. Both layouts, including separate output files, use the same
selected audio source. System/loopback audio is initially off. The computer input
initially follows the operating system's default input, which the user can change
to the built-in laptop microphone in Studio settings.

The transport microphone button controls the current mode's microphone. Inspector
and transport controls stay synchronized. Switching modes restores the defaults;
selecting the current mode preserves an explicit mute. Audio settings are locked
during recording because the recorder snapshots them at Start. The level meter
uses the selected recording mix, including the phone source in Phone mode.

Phone microphone permission and an unmuted browser track remain necessary. These
defaults do not override browser permissions or the phone's mute control. An absent
computer microphone is reported at Start instead of silently selecting another
source. No system audio is enabled automatically.

## Local verification, 2026-10-12

- `tests/test_mode_audio.py` records distinct 440 Hz phone, 880 Hz computer, and
  1760 Hz system fixtures through the real recorder and AAC encoder. Decoding each
  saved MP4 confirms the selected frequency; each excluded frequency must remain
  below 5% of its spectral peak. These are synthetic audio sources, not physical
  phone tests.
- FFprobe inspected five saved H.264/AAC MP4 files (Phone, Screen, Both/PiP and both
  separate outputs), each 1.021354 seconds. Decoded dominant frequencies were
  440.53 Hz for Phone and 879.85 Hz for all computer-microphone outputs. Evidence:
  `artifacts/mode-audio/routing-measurements.json` and adjacent MP4 files.
- The actual Linux default digital microphone delivered 92,160 stereo samples at
  48 kHz during a two-second capture check, without capture errors. This checks
  native input delivery, not spoken-word quality or long-duration synchronization.
  Evidence: `artifacts/mode-audio/native-microphone.json`.
- Native testing exposed Qt 6.12's distinct `QAudio`/`QtAudio` enum classes. Loading
  the signal enum before connection and comparing error values prevents conversion
  failures and false `NoError` error messages. Regression coverage checks both the
  real source's no-error result and an I/O error.
- UI tests cover mode changes, mute synchronization, system default device
  selection, unavailable microphones, and hardware-independent source routing.
  Stop/compression and encrypted remote-control regressions use substituted local
  audio inputs so automated tests do not capture ambient sound.
- All 142 Python/Qt/media/security tests passed; Ruff and whitespace checks passed.
  Test report: `artifacts/mode-audio-tests.xml`.

Physical Android/iPhone microphone capture, Windows microphone permissions and
capture, and sustained A/V synchronization remain unverified on this revision.
Release gates for those scenarios remain closed.
