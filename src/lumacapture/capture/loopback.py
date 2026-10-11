"""Windows WASAPI loopback. Imported lazily so Linux never loads PortAudio."""

import time
import av
import numpy as np


class WasapiLoopback:
    def __init__(self, buffer, error=lambda message: None):
        self.buffer, self.error = buffer, error
        self.api = None
        self.stream = None

    @staticmethod
    def devices():
        import pyaudiowpatch as pa

        with pa.PyAudio() as api:
            return [dict(item) for item in api.get_loopback_device_info_generator()]

    def start(self, device):
        import pyaudiowpatch as pa

        self.stop()
        self.api = pa.PyAudio()
        channels = min(2, int(device["maxInputChannels"]))
        rate = int(device["defaultSampleRate"])
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)

        def callback(data, count, timing, status):
            try:
                samples = np.frombuffer(data, np.float32).reshape(-1, channels).T
                if channels == 1:
                    samples = np.repeat(samples, 2, axis=0)
                frame = av.AudioFrame.from_ndarray(
                    np.ascontiguousarray(samples), format="fltp", layout="stereo"
                )
                frame.sample_rate = rate
                outputs = resampler.resample(frame)
                stamp = time.monotonic() - sum(f.samples for f in outputs) / 48000
                for out in outputs:
                    self.buffer.put(out.to_ndarray(), stamp)
                    stamp += out.samples / 48000
                if status:
                    self.error("WASAPI reported an audio discontinuity")
                return None, pa.paContinue
            except Exception as exc:
                self.error(f"WASAPI loopback failed: {exc}")
                return None, pa.paAbort

        try:
            self.stream = self.api.open(
                format=pa.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device["index"],
                frames_per_buffer=1024,
                stream_callback=callback,
            )
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.api:
            self.api.terminate()
        self.stream = self.api = None
        self.buffer.clear()
