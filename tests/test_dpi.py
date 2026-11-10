"""DPI API selection and metrics, with no process-wide display changes."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dpi


class Function:
    """A callable DLL export that also records the configured ctypes signature."""
    def __init__(self, result=0, effect=None):
        self.result, self.effect, self.calls = result, effect, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.effect(*args) if self.effect else self.result


class DpiTests(unittest.TestCase):
    def dlls(self, user32=None, shcore=None, error=0):
        self.addCleanup(patch.stopall)
        patch("dpi.sys.platform", "win32").start()
        patch("dpi._last_error", return_value=error).start()
        return patch("dpi._library", side_effect=lambda name: {"user32": user32, "shcore": shcore}[name]).start()

    def test_non_windows_does_not_load_or_change_windows_settings(self):
        with patch("dpi.sys.platform", "linux"), patch("dpi._library") as load:
            self.assertEqual(dpi.enable_high_dpi(), "not-windows")
            self.assertIsNone(dpi.window_dpi(Mock()))
            load.assert_not_called()

    def test_modern_api_uses_pointer_sized_pmv2_without_fallback(self):
        modern, fallback = Function(1), Function(0)
        self.dlls(SimpleNamespace(SetProcessDpiAwarenessContext=modern),
                  SimpleNamespace(SetProcessDpiAwareness=fallback))
        self.assertEqual(dpi.enable_high_dpi(), "per-monitor-v2")
        self.assertEqual(modern.argtypes, [ctypes.c_void_p])
        self.assertEqual(modern.calls[0][0].value, ctypes.c_void_p(-4).value)
        self.assertEqual(fallback.calls, [])

    def test_preserves_host_awareness(self):
        def already(_process, pointer):
            pointer._obj.value = 1
            return 0
        modern = Function(1)
        self.dlls(SimpleNamespace(SetProcessDpiAwarenessContext=modern),
                  SimpleNamespace(GetProcessDpiAwareness=Function(effect=already)))
        self.assertEqual(dpi.enable_high_dpi(), "existing")
        self.assertEqual(modern.calls, [])

    def test_legacy_awareness_query_also_preserves_host(self):
        modern = Function(1)
        self.dlls(SimpleNamespace(IsProcessDPIAware=Function(1), SetProcessDpiAwarenessContext=modern))
        self.assertEqual(dpi.enable_high_dpi(), "existing")
        self.assertEqual(modern.calls, [])

    def test_access_denied_does_not_attempt_to_override_existing_policy(self):
        modern, fallback = Function(0), Function(0)
        self.dlls(SimpleNamespace(SetProcessDpiAwarenessContext=modern),
                  SimpleNamespace(SetProcessDpiAwareness=fallback), error=5)
        self.assertEqual(dpi.enable_high_dpi(), "existing")
        self.assertEqual(fallback.calls, [])

    def test_unsupported_pmv2_falls_back_to_windows_81_api(self):
        modern, fallback, legacy = Function(0), Function(0), Function(1)
        self.dlls(SimpleNamespace(SetProcessDpiAwarenessContext=modern, SetProcessDPIAware=legacy),
                  SimpleNamespace(SetProcessDpiAwareness=fallback), error=87)
        self.assertEqual(dpi.enable_high_dpi(), "per-monitor")
        self.assertEqual(fallback.calls, [(2,)])
        self.assertEqual(legacy.calls, [])

    def test_hresult_access_denied_is_handled_as_signed_32_bit(self):
        legacy = Function(1)
        self.dlls(SimpleNamespace(SetProcessDPIAware=legacy),
                  SimpleNamespace(SetProcessDpiAwareness=Function(-2147024891)))
        self.assertEqual(dpi.enable_high_dpi(), "existing")
        self.assertEqual(legacy.calls, [])

    def test_old_windows_uses_system_awareness_without_new_apis(self):
        self.dlls(SimpleNamespace(SetProcessDPIAware=Function(1)))
        self.assertEqual(dpi.enable_high_dpi(), "system")

    def test_absent_apis_do_not_prevent_app_startup(self):
        self.dlls()
        self.assertEqual(dpi.enable_high_dpi(), "unavailable")

    def test_window_dpi_uses_64_bit_window_handle_and_controls_scale(self):
        query = Function(192)
        self.dlls(SimpleNamespace(GetDpiForWindow=query))
        root = Mock()
        root.winfo_id.return_value = 0x100000002
        self.assertEqual(dpi.window_dpi(root), 192)
        self.assertEqual(dpi.logical_scale(root), 2)
        self.assertEqual(query.argtypes, [ctypes.c_void_p])
        self.assertEqual(query.calls[0], (0x100000002,))

    def test_invalid_window_dpi_uses_tk_pixels_per_point(self):
        self.dlls(SimpleNamespace(GetDpiForWindow=Function(0)))
        root = Mock()
        root.tk.call.return_value = 2.0
        self.assertEqual(dpi.logical_scale(root), 1.5)

    def test_tk_fallback_rejects_nonfinite_or_invalid_scales(self):
        with patch("dpi.window_dpi", return_value=None):
            for value in ("nan", "inf", 0, -1, "invalid"):
                root = Mock()
                root.tk.call.return_value = value
                self.assertEqual(dpi.logical_scale(root), 1)

    def test_sync_uses_dpi_per_point_once_not_percentage_as_tk_scaling(self):
        root = Mock()
        with patch("dpi.window_dpi", return_value=192):
            self.assertEqual(dpi.sync_tk_scaling(root), 2)
        root.tk.call.assert_called_once_with("tk", "scaling", 192 / 72)

    def test_portable_sync_preserves_tk_native_scaling(self):
        root = Mock()
        root.tk.call.return_value = 1.3333333333333333
        with patch("dpi.window_dpi", return_value=None):
            self.assertEqual(dpi.sync_tk_scaling(root), 1)
        root.tk.call.assert_called_once_with("tk", "scaling")

    def test_work_area_keeps_negative_monitor_coordinates_and_excludes_taskbar(self):
        def info(_monitor, pointer):
            self.assertEqual(pointer._obj.cbSize, 40)
            pointer._obj.rcWork = dpi._RECT(-3840, 0, 0, 2100)
            return 1
        from_window = Function(0x100000003)
        self.dlls(SimpleNamespace(MonitorFromWindow=from_window, GetMonitorInfoW=Function(effect=info)))
        root = Mock()
        root.winfo_id.return_value = 0x100000002
        self.assertEqual(dpi.work_area(root), (-3840, 0, 0, 2100))
        self.assertEqual(from_window.restype, ctypes.c_void_p)
        self.assertEqual(from_window.calls, [(0x100000002, 2)])

    def test_work_area_falls_back_to_portable_screen_metrics(self):
        root = Mock()
        root.winfo_screenwidth.return_value = 1920
        root.winfo_screenheight.return_value = 1080
        with patch("dpi.sys.platform", "linux"), patch("dpi._library") as load:
            self.assertEqual(dpi.work_area(root), (0, 0, 1920, 1080))
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
