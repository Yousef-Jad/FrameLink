# Architecture

The desktop application has one Qt GUI thread, native capture callbacks, an asyncio thread for HTTPS/WebRTC, one recording worker, and one persistent compression worker. SQLite operations use short-lived connections with WAL. UI updates cross threads through Qt signals. Capture sources publish immutable RGB frame arrays into bounded buffers; they never wait on disk writes.

Qt's official Python bindings expose the same native capture implementations as its C++ API. PyAV and aiortc share FFmpeg-backed frames in one process and avoid a custom native WebRTC build plus a signaling sidecar. This reduces integration complexity, at the cost of Python orchestration overhead and CPU composition. It is not evidence that this pipeline outperforms a C++ zero-copy implementation.

Windows first attempts Windows Graphics Capture through a native Rust-backed adapter; initialization failure falls back to Qt's DXGI capture. Linux uses Qt's native X11 or portal/PipeWire implementation. Portal permission and target selection remain with the compositor. No screenshot-based capture loop is implemented in application code.

All output files share a monotonic, pause-aware session clock. The encoder worker samples bounded video buffers, composes the scene, mixes 48 kHz stereo audio, and timestamps MKV packets. When the worker falls behind it advances PTS and counts skipped output frames; it does not falsify encoded-FPS telemetry. A still desktop frame may be repeated to represent unchanged content. A 120 ms source playout delay absorbs short network jitter. Receive-time normalization is not a calibrated remote clock or a guarantee of physical lip-sync.

Temporary MKV files use short clusters. Finalization flushes codecs, decodes the output, checks streams and duration, fsyncs, and atomically links to a non-existing final name before removing the temporary name. Filesystems without hard-link support fail safely, retaining the intermediate file. Recording output should therefore use NTFS, ext4, btrfs, or a compatible local filesystem; FAT/exFAT finalization is not currently supported. Recovery writes a new remux and retains interrupted originals.

Compression jobs persist settings and status in SQLite. An interrupted worker never marks a job complete. A validated export gets a unique filename and a library relation to its original. Original deletion is an explicit library operation, not an automatic queue side effect.

The HTTPS listener is opt-in and binds one private IPv4 address. A local root CA signs a short-lived IP certificate. Pairing exchanges a one-use QR bearer token for an in-memory session. Signaling validates Host, Origin, request size and content type, local candidates, and session authorization. DTLS-SRTP protects media. The separate HTTP bootstrap endpoint exposes public trust material only for a bounded period; fingerprint verification is needed because its transport is unauthenticated.

Release preparation is fail-closed: platform builds run separately and publication verifies the exact tagged CI commit plus a manual evidence manifest. Missing physical tests are visible release blockers rather than mocked successes.
