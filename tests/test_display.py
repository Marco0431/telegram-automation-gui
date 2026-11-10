"""Real Tk layout checks with simulated DPI and an entirely offline engine.

Only this Tk interpreter's scale changes; no Windows display setting or process
DPI-awareness API is called. Linux runs these tests under the same Xvfb as the UI.
"""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from config import Config, make_defaults
from test_ui import FakeEngine
from ui import App


SCALES = (1.0, 1.5, 2.0, 2.5, 3.0)
WORK_AREA = (0, 0, 3840, 2160)


def descendants(parent):
    for child in parent.winfo_children():
        yield child
        yield from descendants(child)


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix=".test-display-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.path.write_text(json.dumps(make_defaults("en")), encoding="utf-8")
        self.factor = 1.0
        self.root = self.app = None
        for target, kwargs in (
            ("display.logical_scale", {"side_effect": lambda root: self.factor}),
            ("display.sync_tk_scaling", {"side_effect": self.sync_scale}),
            ("display.work_area", {"return_value": WORK_AREA}),
            ("ui.work_area", {"return_value": WORK_AREA}),
        ):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.destroy_app)

    def sync_scale(self, root):
        root.tk.call("tk", "scaling", self.factor * 96 / 72)
        return self.factor

    def destroy_app(self):
        if self.root is not None:
            if self.app is not None:
                self.app.closing = True
                for name in ("_poll_id", "_dpi_after"):
                    callback = getattr(self.app, name, None)
                    if callback:
                        self.root.after_cancel(callback)
            self.root.destroy()
            self.root = self.app = None

    def new_app(self, factor=1.0):
        self.destroy_app()
        self.factor = factor
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.attributes("-alpha", 0)
        # Permit a simulated 4K layout even when CI's virtual screen is smaller.
        self.root.maxsize(WORK_AREA[2], WORK_AREA[3])
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.cfg, self.engine = Config(self.path), FakeEngine()
        self.app = App(self.root, config=self.cfg, engine=self.engine,
                       start_engine=False, start_tray=False)
        self.root.geometry("+0+0")
        self.root.deiconify()
        self.root.update()

    def change_scale(self, factor):
        self.factor = factor
        if self.app._dpi_after:
            self.root.after_cancel(self.app._dpi_after)
        self.app._refresh_dpi()
        self.root.update()
        self.assertEqual(self.callback_errors, [])

    def assert_fits_work_area(self, window):
        left, top, right, bottom = WORK_AREA
        self.assertGreaterEqual(window.winfo_x(), left)
        self.assertGreaterEqual(window.winfo_y(), top)
        self.assertLessEqual(window.winfo_rootx() + window.winfo_width(), right)
        self.assertLessEqual(window.winfo_rooty() + window.winfo_height(), bottom)
        minimum_width, minimum_height = window.minsize()
        self.assertLessEqual(minimum_width, right - left)
        self.assertLessEqual(minimum_height, bottom - top)

    def open_group_picker(self):
        self.app.dialogs = [
            {"id": -123, "name": "Reading group / 阅读", "kind": "group"},
            {"id": -456, "name": "Project group", "kind": "group"},
        ]
        self.app.choose_groups("ai")
        self.app._group_dialog.attributes("-alpha", 0)
        self.root.update()
        tree = next(widget for widget in descendants(self.app._group_dialog)
                    if isinstance(widget, ttk.Treeview))
        return self.app._group_dialog, tree

    def test_fonts_fields_and_spacing_scale_together_and_return_to_original_size(self):
        self.new_app()
        entry = self.app.field_widgets["api_id"]
        next_entry = self.app.field_widgets["api_hash"]
        original_line = self.app.fonts["body"].metrics("linespace")
        original_gap = next_entry.winfo_y() - entry.winfo_y() - entry.winfo_height()
        original_height = entry.winfo_height()
        self.assertGreater(original_gap, 0)
        for factor in (*SCALES, 2.0, 1.0):
            with self.subTest(scale=factor):
                self.change_scale(factor)
                line = self.app.fonts["body"].metrics("linespace")
                gap = next_entry.winfo_y() - entry.winfo_y() - entry.winfo_height()
                # Font hinting rounds at small sizes, so compare proportions.
                self.assertAlmostEqual(line / original_line, factor, delta=0.25)
                self.assertAlmostEqual(gap / original_gap, factor, delta=0.15)
                field_line = int(self.root.tk.call("font", "metrics", entry.cget("font"), "-linespace"))
                self.assertGreaterEqual(field_line, line * 0.9)
                self.assertGreaterEqual(entry.winfo_height(), field_line)
                self.assert_fits_work_area(self.root)
        self.assertEqual(entry.winfo_height(), original_height)
        self.assertEqual(self.app.fonts["body"].metrics("linespace"), original_line)
        self.assertEqual(self.engine.commands, [])

    def test_startup_window_and_group_picker_fit_at_all_supported_scales(self):
        for factor in SCALES:
            with self.subTest(scale=factor):
                self.new_app(factor)
                self.assert_fits_work_area(self.root)
                dialog, tree = self.open_group_picker()
                self.assert_fits_work_area(dialog)
                row = tree.bbox("-123")
                self.assertTrue(row)
                self.assertGreaterEqual(row[3], self.app.fonts["body"].metrics("linespace"))
                self.assertLessEqual(row[0] + row[2], tree.winfo_width())
                self.assertEqual(self.callback_errors, [])

    def test_moving_from_4k_to_smaller_monitor_shrinks_window_and_keeps_footer_usable(self):
        self.new_app(2.0)
        self.assertGreater(self.root.winfo_width(), 1920)
        self.assertGreater(self.root.winfo_height(), 1080)
        smaller_area = (0, 0, 1920, 1080)
        with patch("display.work_area", return_value=smaller_area), \
                patch("ui.work_area", return_value=smaller_area):
            self.change_scale(1.0)
            self.assertLessEqual(self.root.winfo_width(), 1920)
            self.assertLessEqual(self.root.winfo_height(), 1080)
            footer = self.app.status_label.master
            self.assertTrue(footer.winfo_ismapped())
            self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), self.root.winfo_height())
            self.assertLessEqual(self.app.nb.winfo_y() + self.app.nb.winfo_height(), footer.winfo_y())
            for button in (self.app.btn_save, self.app.btn_start, self.app.btn_stop):
                self.assertTrue(button.winfo_ismapped())
                top = button.winfo_rooty() - self.root.winfo_rooty()
                left = button.winfo_rootx() - self.root.winfo_rootx()
                self.assertGreaterEqual(top, 0)
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(top + button.winfo_height(), self.root.winfo_height())
                self.assertLessEqual(left + button.winfo_width(), self.root.winfo_width())
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.engine.commands, [])

    def test_open_group_picker_keeps_selection_and_visible_columns_across_dpi_changes(self):
        self.new_app()
        dialog, tree = self.open_group_picker()
        row = tree.bbox("-123")
        tree.event_generate("<ButtonRelease-1>", x=10, y=row[1] + row[3] // 2)
        self.root.update()
        self.assertEqual(tree.item("-123", "values")[0], "☑")
        for factor in (*SCALES[1:], 1.0):
            with self.subTest(scale=factor):
                self.change_scale(factor)
                self.assertIs(self.app._group_dialog, dialog)
                self.assert_fits_work_area(dialog)
                row = tree.bbox("-123")
                self.assertTrue(row)
                self.assertLessEqual(row[0] + row[2], tree.winfo_width())
                self.assertGreaterEqual(row[3], self.app.fonts["body"].metrics("linespace"))
                self.assertEqual(tree.item("-123", "values")[0], "☑")
        apply_button = next(widget for widget in descendants(dialog)
                            if isinstance(widget, ttk.Button) and widget.cget("text") == self.app.t("apply"))
        apply_button.invoke()
        self.root.update()
        self.assertEqual(self.app.selected["ai"], [{"id": -123, "name": "Reading group / 阅读"}])
        self.assertEqual(self.engine.commands, [])

    def test_language_dropdown_and_unsaved_drafts_survive_dpi_changes(self):
        self.new_app()
        self.app.vars["api_hash"].set("unsaved-hash")
        self.app.vars["ai_api_key"].set("unsaved-secret")
        self.app.vars["chat_interval_min"].set("unfinished-number")
        text = "  Keep this custom prompt. 保持原文。\n\nSecond line.\n"
        self.app.texts["ai_system_prompt"].delete("1.0", "end")
        self.app.texts["ai_system_prompt"].insert("1.0", text)
        self.app.selected["ai"] = [{"id": -789, "name": "Unsaved selection"}]
        self.app.nb.select(1)
        self.app.task_nb.select(1)
        mode = self.app.field_widgets["chat_mode"]
        mode.current(1)
        mode.event_generate("<<ComboboxSelected>>")
        self.root.update()
        for index, factor in enumerate((*SCALES, 1.0)):
            with self.subTest(scale=factor):
                self.change_scale(factor)
                language = next(widget for widget in descendants(self.root)
                                if isinstance(widget, ttk.Combobox)
                                and str(widget.cget("textvariable")) == str(self.app.language_var))
                self.root.tk.call("ttk::combobox::Post", str(language))
                self.root.update()
                popup = self.root.tk.call("ttk::combobox::PopdownWindow", str(language))
                listbox_font = self.root.tk.call(f"{popup}.f.l", "cget", "-font")
                line = int(self.root.tk.call("font", "metrics", listbox_font, "-linespace"))
                self.assertGreaterEqual(line, self.app.fonts["body"].metrics("linespace") * 0.9)
                self.root.tk.call("ttk::combobox::Unpost", str(language))
                language.set("简体中文" if index % 2 == 0 else "English")
                language.event_generate("<<ComboboxSelected>>")
                self.root.update()
                self.assertEqual(self.app.lang, "zh-CN" if index % 2 == 0 else "en")
                self.assertEqual(self.app.vars["api_hash"].get(), "unsaved-hash")
                self.assertEqual(self.app.vars["ai_api_key"].get(), "unsaved-secret")
                self.assertEqual(self.app.vars["chat_interval_min"].get(), "unfinished-number")
                self.assertEqual(self.app.vars["chat_mode"].get(), "ai")
                self.assertEqual(self.app.texts["ai_system_prompt"].get("1.0", "end-1c"), text)
                self.assertEqual(self.app.selected["ai"], [{"id": -789, "name": "Unsaved selection"}])
                self.assertEqual(self.app.nb.index(self.app.nb.select()), 1)
                self.assertEqual(self.app.task_nb.index(self.app.task_nb.select()), 1)
                persisted = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["api_hash"], "")
                self.assertEqual(persisted["ai_api_key"], "")
                self.assertEqual(self.callback_errors, [])
        self.assertTrue(self.app.dirty)
        self.assertEqual(self.engine.commands, [])


if __name__ == "__main__":
    unittest.main()
