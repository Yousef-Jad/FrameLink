"""Use documented Windows composition attributes; keep opaque content elsewhere."""

import sys


def apply_window_effects(window, dark):
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        dwm = ctypes.WinDLL("dwmapi")
        set_attribute = dwm.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        mode = ctypes.c_int(bool(dark))
        set_attribute(int(window.winId()), 20, ctypes.byref(mode), ctypes.sizeof(mode))
        if sys.getwindowsversion().build >= 22621:
            backdrop = ctypes.c_int(2)  # DWMSBT_MAINWINDOW, documented system backdrop.
            return (
                set_attribute(int(window.winId()), 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)) == 0
            )
    except (AttributeError, OSError):
        pass
    return False
