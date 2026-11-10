"""Windows DPI awareness and monitor metrics, without creating any windows.

Call ``enable_high_dpi`` before Tk() or any other GUI is created. Tk's scaling
value is pixels per point; application dimensions use 96-DPI logical pixels.
No process settings are changed merely by importing this module.
"""
import ctypes
from functools import lru_cache
import math
import sys


_HANDLE = ctypes.c_void_p
_BOOL = ctypes.c_int32
_UINT = ctypes.c_uint32
_HRESULT = ctypes.c_int32
_ERROR_ACCESS_DENIED = 5
_E_ACCESSDENIED = 0x80070005


class _RECT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", _UINT), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", _UINT)]


@lru_cache(maxsize=2)
def _library(name):
    try:
        return ctypes.WinDLL(name, use_last_error=True)
    except (AttributeError, OSError):
        return None


def _function(library, name, arguments, result):
    function = getattr(library, name, None)
    if function is not None:
        function.argtypes = arguments
        function.restype = result
    return function


def _last_error():
    return getattr(ctypes, "get_last_error", lambda: 0)()


def _already_aware(user32, shcore):
    get_awareness = _function(shcore, "GetProcessDpiAwareness",
                              [_HANDLE, ctypes.POINTER(ctypes.c_int32)], _HRESULT)
    if get_awareness is not None:
        awareness = ctypes.c_int32()
        try:
            if get_awareness(None, ctypes.byref(awareness)) == 0:
                return awareness.value > 0
        except OSError:
            pass
    is_aware = _function(user32, "IsProcessDPIAware", [], _BOOL)
    try:
        return bool(is_aware()) if is_aware is not None else False
    except OSError:
        return False


def enable_high_dpi():
    """Set the best available awareness before creating Tk, preserving host policy.

    Returns a diagnostic label. Access denied means a host/manifest has already
    selected awareness, so lower-level calls must not attempt to override it.
    Per-monitor mode still requires the UI to update its layout when DPI changes.
    """
    if sys.platform != "win32":
        return "not-windows"
    user32, shcore = _library("user32"), _library("shcore")
    if _already_aware(user32, shcore):
        return "existing"
    set_context = _function(user32, "SetProcessDpiAwarenessContext", [_HANDLE], _BOOL)
    if set_context is not None:
        try:
            # A pointer-sized signed pseudo-handle, not a 32-bit int on Win64.
            if set_context(_HANDLE(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
                return "per-monitor-v2"
            if _last_error() == _ERROR_ACCESS_DENIED:
                return "existing"
        except OSError:
            pass
    set_awareness = _function(shcore, "SetProcessDpiAwareness", [ctypes.c_int32], _HRESULT)
    if set_awareness is not None:
        try:
            result = set_awareness(2) & 0xFFFFFFFF  # PROCESS_PER_MONITOR_DPI_AWARE
            if result == 0:
                return "per-monitor"
            if result == _E_ACCESSDENIED:
                return "existing"
        except OSError:
            pass
    set_legacy = _function(user32, "SetProcessDPIAware", [], _BOOL)
    if set_legacy is not None:
        try:
            if set_legacy():
                return "system"
            if _last_error() == _ERROR_ACCESS_DENIED:
                return "existing"
        except OSError:
            pass
    return "unavailable"


def window_dpi(root):
    """Effective DPI of this HWND, or None when Windows cannot report it."""
    if sys.platform != "win32":
        return None
    query = _function(_library("user32"), "GetDpiForWindow", [_HANDLE], _UINT)
    if query is None:
        return None
    try:
        value = int(query(root.winfo_id()))
        return value if 48 <= value <= 768 else None
    except (OSError, AttributeError, TypeError, ValueError):
        return None


def logical_scale(root):
    """Physical pixels per 96-DPI application pixel, with a portable Tk fallback."""
    dpi = window_dpi(root)
    if dpi is not None:
        return dpi / 96.0
    try:
        value = float(root.tk.call("tk", "scaling")) * 72.0 / 96.0
        if math.isfinite(value) and 0.5 <= value <= 8:
            return value
    except (AttributeError, TypeError, ValueError):
        pass
    return 1.0


def sync_tk_scaling(root):
    """Match Tk point-sized fonts to the current Windows monitor before layout."""
    dpi = window_dpi(root)
    if dpi is not None:
        root.tk.call("tk", "scaling", dpi / 72.0)
        return dpi / 96.0
    return logical_scale(root)


def work_area(root):
    """Monitor's usable rectangle (left, top, right, bottom), excluding taskbars.

    Coordinates may be negative on secondary monitors. Other platforms retain
    Tk's screen metrics; no Windows-only APIs are loaded there.
    """
    if sys.platform == "win32":
        user32 = _library("user32")
        from_window = _function(user32, "MonitorFromWindow", [_HANDLE, _UINT], _HANDLE)
        get_info = _function(user32, "GetMonitorInfoW", [_HANDLE, ctypes.POINTER(_MONITORINFO)], _BOOL)
        if from_window is not None and get_info is not None:
            try:
                monitor = from_window(root.winfo_id(), 2)  # MONITOR_DEFAULTTONEAREST
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(info)
                if monitor and get_info(monitor, ctypes.byref(info)):
                    rect = info.rcWork
                    if rect.right > rect.left and rect.bottom > rect.top:
                        return rect.left, rect.top, rect.right, rect.bottom
            except (OSError, AttributeError, TypeError, ValueError):
                pass
    return 0, 0, max(1, root.winfo_screenwidth()), max(1, root.winfo_screenheight())
