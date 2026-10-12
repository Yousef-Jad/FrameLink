import time
import av
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudio, QAudioSource, QAudioFormat


class AudioCapture(QObject):
    error = Signal(str)

    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer
        self.source = None
        self.io = None
        self.pending = bytearray()
        self.resampler = None
        self.format = None

    def start(self, device):
        self.stop()
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        self.format = fmt
        self.resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
        self.source = QAudioSource(device, fmt, self)
        self.source.setBufferSize(max(4096, fmt.bytesForDuration(100000)))
        self.io = self.source.start()
        if self.io is None:
            self.stop()
            raise RuntimeError("Audio device could not be opened")
        self.io.readyRead.connect(self.read)
        self.source.stateChanged.connect(self._state_changed)

    def _state_changed(self, state):
        if self.source:
            error = self.source.error()
            # Qt 6.12 bindings expose distinct QAudio/QtAudio enum classes.
            # Compare their values so NoError never becomes a false alarm.
            if error.value != QAudio.Error.NoError.value:
                self.error.emit(f"Audio capture error: {error.name}")

    def read(self):
        if self.io is None:
            return
        self.pending.extend(bytes(self.io.readAll()))
        fmt = self.format
        stride = fmt.bytesPerFrame()
        size = len(self.pending) // stride * stride
        if not size:
            return
        raw = bytes(self.pending[:size])
        del self.pending[:size]
        types = {
            QAudioFormat.SampleFormat.Float: np.float32,
            QAudioFormat.SampleFormat.Int16: np.int16,
            QAudioFormat.SampleFormat.Int32: np.int32,
            QAudioFormat.SampleFormat.UInt8: np.uint8,
        }
        data = np.frombuffer(raw, dtype=types[fmt.sampleFormat()]).astype(np.float32)
        if fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16:
            data /= 32768
        elif fmt.sampleFormat() == QAudioFormat.SampleFormat.Int32:
            data /= 2147483648
        elif fmt.sampleFormat() == QAudioFormat.SampleFormat.UInt8:
            data = (data - 128) / 128
        data = data.reshape(-1, fmt.channelCount()).T
        if data.shape[0] == 1:
            data = np.repeat(data, 2, axis=0)
        else:
            data = data[:2]
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data), format="fltp", layout="stereo")
        frame.sample_rate = fmt.sampleRate()
        frames = self.resampler.resample(frame)
        total = sum(f.samples for f in frames)
        stamp = time.monotonic() - total / 48000
        for f in frames:
            self.buffer.put(f.to_ndarray(), stamp)
            stamp += f.samples / 48000

    def stop(self):
        if self.source:
            self.source.stop()
            self.source.deleteLater()
        self.source = None
        self.io = None
        self.pending.clear()
        self.buffer.clear()
