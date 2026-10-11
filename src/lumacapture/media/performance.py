import ctypes
import ctypes.util
from pathlib import Path
import sys
import threading
import psutil


class GpuUtilization:
    """System-wide utilization where a documented driver interface is available."""

    def __init__(self):
        self.nvml = None
        self.handle = ctypes.c_void_p()
        self.amd = (
            list(Path("/sys/class/drm").glob("card*/device/gpu_busy_percent"))
            if sys.platform.startswith("linux")
            else []
        )
        name = "nvml.dll" if sys.platform == "win32" else ctypes.util.find_library("nvidia-ml")
        if name:
            try:
                lib = ctypes.CDLL(name)
                if lib.nvmlInit_v2() == 0:
                    if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.handle)) == 0:
                        self.nvml = lib
                    else:
                        lib.nvmlShutdown()
            except (OSError, AttributeError):
                pass

    def read(self):
        if self.nvml:

            class Rates(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            rates = Rates()
            if self.nvml.nvmlDeviceGetUtilizationRates(self.handle, ctypes.byref(rates)) == 0:
                return rates.gpu
        for path in self.amd:
            try:
                return int(path.read_text().strip())
            except (OSError, ValueError):
                pass
        return None

    def close(self):
        if self.nvml:
            self.nvml.nvmlShutdown()
            self.nvml = None


class ResourceMonitor:
    def __init__(self):
        self.stop_event = threading.Event()
        self.gpu = None
        self.memory_pressure = 0
        self.thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)
        self.thread.start()

    def _run(self):
        provider = GpuUtilization()
        try:
            while not self.stop_event.is_set():
                self.gpu = provider.read()
                self.memory_pressure = psutil.virtual_memory().percent
                self.stop_event.wait(1)
        finally:
            provider.close()

    def stop(self):
        self.stop_event.set()
