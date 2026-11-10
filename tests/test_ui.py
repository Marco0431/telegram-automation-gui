"""Tk integration tests with isolated settings and an entirely fake engine.

Run with a desktop/Tk-capable Python. Tests never log in, call APIs, or send messages.
"""

from copy import deepcopy
import json
from pathlib import Path
import queue
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from config import Config, LEGACY_AI_PROMPT, default_ai_prompt, default_chat_prompt, make_defaults
from ui import App


class FakeEngine:
    def __init__(self):
        self.out = queue.Queue()
        self.commands = []
        self.client = None
        self._thread = None

    def start_thread(self):
        raise AssertionError("UI tests must not start an engine thread")

    def send(self, command):
        self.commands.append(deepcopy(command))


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix=".test-ui-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.path.write_text(json.dumps(make_defaults("en")), encoding="utf-8")
        self.cfg = Config(self.path)
        self.engine = FakeEngine()
        self.root = tk.Tk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.addCleanup(self.destroy_root)
        self.confirm = patch("ui.messagebox.askyesno", return_value=False).start()
        self.addCleanup(patch.stopall)
        self.app = App(self.root, config=self.cfg, engine=self.engine,
                       start_engine=False, start_tray=False)
        self.root.update()

    def destroy_root(self):
        if self.root.winfo_exists():
            if getattr(self, "app", None) is not None:
                self.app.closing = True
                if self.app._poll_id:
                    self.root.after_cancel(self.app._poll_id)
            self.root.destroy()

    def text(self, key, value):
        widget = self.app.texts[key]
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        self.root.update()

    def switch(self, language):
        self.app.language_var.set("English" if language == "en" else "简体中文")
        self.app._change_language()
        self.root.update()
        self.assertEqual(self.callback_errors, [])

    def persisted(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def ready(self):
        self.app.vars["api_id"].set("123")
        self.app.vars["api_hash"].set("fake-hash")
        self.app._handle_event({"kind": "status", "state": "ready", "text": "Connected (fake)"})

    def test_startup_has_no_commands_and_language_is_visible(self):
        self.assertEqual(self.engine.commands, [])
        self.assertEqual(self.app.language_var.get(), "English")
        self.assertEqual([self.app.nb.tab(tab, "text") for tab in self.app.nb.tabs()],
                         ["Connection", "Tasks", "AI connection", "Activity"])
        self.assertFalse(self.app.running)

    def test_footer_stays_visible_after_language_and_tab_changes_at_minimum_size(self):
        # Map an invisible window so Tk performs real geometry allocation.
        self.root.attributes("-alpha", 0)
        self.root.geometry("900x680+0+0")
        self.root.deiconify()
        self.root.update()
        self.app.nb.select(1)
        for language in ("zh-CN", "en"):
            self.switch(language)
            for tab in (2, 0, 1, 3):
                with self.subTest(language=language, tab=tab):
                    self.app.nb.select(tab)
                    self.root.update()
                    footer = self.app.status_label.master
                    self.assertTrue(footer.winfo_ismapped())
                    self.assertLessEqual(footer.winfo_y() + footer.winfo_height(),
                                         self.root.winfo_height())
                    self.assertLessEqual(self.app.nb.winfo_y() + self.app.nb.winfo_height(),
                                         footer.winfo_y())
                    for widget in (self.app.status_label, self.app.btn_save,
                                   self.app.btn_start, self.app.btn_stop):
                        self.assertTrue(widget.winfo_ismapped())
                        top = widget.winfo_rooty() - self.root.winfo_rooty()
                        self.assertGreaterEqual(top, 0)
                        self.assertLessEqual(top + widget.winfo_height(), self.root.winfo_height())
        self.assertEqual(self.callback_errors, [])

    def test_group_picker_opens_centered_over_parent(self):
        self.root.attributes("-alpha", 0)
        self.root.geometry("1080x820+100+80")
        self.root.deiconify()
        self.root.update()
        self.app.choose_groups("checkin")
        self.root.update()
        dialog = self.app._group_dialog
        for position, size in (("winfo_rootx", "winfo_width"), ("winfo_rooty", "winfo_height")):
            parent_center = getattr(self.root, position)() + getattr(self.root, size)() / 2
            dialog_center = getattr(dialog, position)() + getattr(dialog, size)() / 2
            # Window-manager borders differ between Windows and Linux/Xvfb.
            self.assertLessEqual(abs(parent_center - dialog_center), 30)
        self.assertEqual(self.engine.commands, [])

    def test_language_switch_keeps_drafts_and_only_persists_language_defaults(self):
        before = self.cfg.snapshot()
        self.app.vars["api_id"].set("unsaved-id")
        self.app.vars["ai_api_key"].set("unsaved-secret")
        self.app.vars["proxy_password"].set("unsaved-proxy-secret")
        self.app.vars["chat_interval_min"].set("not a number yet")
        self.app.vars["ai_max_len"].set("unfinished")
        self.text("chat_messages", "First message\nSecond message")
        self.text("ai_system_prompt", "  A custom unsaved prompt.  ")
        self.app.selected["ai"] = [{"id": -321, "name": "unsaved group"}]
        self.app.nb.select(1)
        self.app.task_nb.select(2)
        self.switch("zh-CN")
        self.assertEqual(self.app.vars["api_id"].get(), "unsaved-id")
        self.assertEqual(self.app.vars["ai_api_key"].get(), "unsaved-secret")
        self.assertEqual(self.app.vars["proxy_password"].get(), "unsaved-proxy-secret")
        self.assertEqual(self.app.vars["chat_interval_min"].get(), "not a number yet")
        self.assertEqual(self.app.vars["ai_max_len"].get(), "unfinished")
        self.assertEqual(self.app.texts["ai_system_prompt"].get("1.0", "end-1c"), "  A custom unsaved prompt.  ")
        self.assertEqual(self.app.selected["ai"], [{"id": -321, "name": "unsaved group"}])
        self.assertEqual(self.app.nb.index(self.app.nb.select()), 1)
        self.assertEqual(self.app.task_nb.index(self.app.task_nb.select()), 2)
        expected = deepcopy(before)
        expected.update(language="zh-CN", ai_system_prompt=default_ai_prompt("zh-CN"))
        self.assertEqual(self.persisted(), expected)
        self.assertTrue(self.app.dirty)
        self.assertEqual(self.engine.commands, [])

    def test_language_switch_preserves_exact_multiline_drafts_and_numeric_input(self):
        messages = "  first message  \n\nsecond message\n"
        commands = " /checkin \n\n签到\n"
        self.text("chat_messages", messages)
        self.text("checkin_commands", commands)
        self.app.vars["chat_interval_min"].set("12.345678901")
        self.switch("zh-CN")
        self.assertEqual(self.app.texts["chat_messages"].get("1.0", "end-1c"), messages)
        self.assertEqual(self.app.texts["checkin_commands"].get("1.0", "end-1c"), commands)
        self.assertEqual(self.app.vars["chat_interval_min"].get(), "12.345678901")

    def test_clean_language_switch_does_not_mark_unrelated_settings_unsaved(self):
        self.assertFalse(self.app.dirty)
        self.switch("zh-CN")
        self.assertFalse(self.app.dirty)
        self.switch("en")
        self.assertFalse(self.app.dirty)

    def test_legacy_id_only_groups_load_into_ui_without_losing_selection(self):
        self.path.write_text(json.dumps({"checkin_groups": [-1, "-2"], "ai_groups": ["-3"]}), encoding="utf-8")
        self.cfg.load()
        self.app.lang = self.cfg.get("language")
        self.app._build_ui(self.cfg.snapshot())
        self.root.update()
        self.assertEqual(self.app.selected["checkin"], [{"id": -1, "name": "-1"}, {"id": -2, "name": "-2"}])
        self.assertEqual(self.app.selected["ai"], [{"id": -3, "name": "-3"}])
        self.assertEqual(self.callback_errors, [])

    def test_builtin_and_legacy_prompts_follow_language_but_custom_prompts_do_not(self):
        self.cfg.update({"ai_system_prompt": LEGACY_AI_PROMPT,
                         "chat_ai_prompt": default_chat_prompt("en")})
        self.text("ai_system_prompt", LEGACY_AI_PROMPT)
        self.text("chat_ai_prompt", default_chat_prompt("en"))
        self.switch("zh-CN")
        self.assertEqual(self.cfg.get("ai_system_prompt"), default_ai_prompt("zh-CN"))
        self.assertEqual(self.app.texts["ai_system_prompt"].get("1.0", "end-1c"), default_ai_prompt("zh-CN"))
        self.assertEqual(self.app.texts["chat_ai_prompt"].get("1.0", "end-1c"), default_chat_prompt("zh-CN"))
        custom = "Keep my mixed language system prompt exactly. 保持原样。"
        self.cfg.update({"ai_system_prompt": custom, "chat_ai_prompt": custom})
        self.text("ai_system_prompt", custom)
        self.text("chat_ai_prompt", custom)
        self.switch("en")
        self.assertEqual(self.cfg.get("ai_system_prompt"), custom)
        self.assertEqual(self.cfg.get("chat_ai_prompt"), custom)
        self.assertEqual(self.app.texts["ai_system_prompt"].get("1.0", "end-1c"), custom)
        self.assertEqual(self.app.texts["chat_ai_prompt"].get("1.0", "end-1c"), custom)

    def test_save_is_one_transaction_and_preserves_canonical_ai_group_key(self):
        self.app.vars["ai_api_key"].set("example-only")
        self.app.vars["background_run"].set(True)
        self.app.selected["ai"] = [{"id": -555, "name": "Selected AI group"}]
        with patch.object(self.cfg, "_write", wraps=self.cfg._write) as writer:
            self.assertTrue(self.app.save_settings())
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.cfg.get("ai_reply_groups"), [{"id": -555, "name": "Selected AI group"}])
        self.assertNotIn("ai_groups", self.persisted())
        self.assertTrue(self.cfg.get("background_run"))
        self.assertFalse(self.app.dirty)
        self.assertEqual(self.engine.commands, [])

    def test_invalid_start_does_not_send_save_or_set_pending(self):
        self.ready()
        self.app.vars["checkin_enabled"].set(True)
        self.app.vars["checkin_time"].set("25:99")
        before = self.path.read_bytes()
        self.app.on_start()
        self.assertEqual(self.engine.commands, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.app.pending)
        self.assertFalse(self.app.running)
        self.assertIn("HH:MM", self.app.notice_label.cget("text"))

    def test_valid_start_saves_once_and_waits_for_engine_acknowledgement(self):
        self.ready()
        self.app.vars["checkin_enabled"].set(True)
        self.app.selected["checkin"] = [{"id": -321, "name": "test group"}]
        with patch.object(self.cfg, "_write", wraps=self.cfg._write) as writer:
            self.app.on_start()
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.engine.commands, [{"type": "start"}])
        self.assertTrue(self.app.pending)
        self.assertFalse(self.app.running)
        self.app._handle_event({"kind": "status", "state": "running", "text": "Running (fake)"})
        self.assertFalse(self.app.pending)
        self.assertTrue(self.app.running)

    def test_ai_test_uses_unsaved_values_without_saving_or_changing_connection(self):
        self.app.vars["ai_api_key"].set("fake-test-key")
        self.app.vars["ai_model"].set("unsaved-model")
        self.app.vars["ai_base_url"].set("https://api.example.test/v1")
        before = self.path.read_bytes()
        self.app.on_test_ai()
        self.assertEqual(len(self.engine.commands), 1)
        command = self.engine.commands[0]
        self.assertEqual(command["type"], "test_ai")
        self.assertEqual(command["settings"]["ai_api_key"], "fake-test-key")
        self.assertEqual(command["settings"]["ai_model"], "unsaved-model")
        self.assertEqual(self.app.connection_state, "idle")
        self.app._handle_event({"kind": "ai_test", "state": "success", "text": "AI test succeeded (fake)"})
        self.assertEqual(self.app.connection_state, "idle")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(str(self.app.btn_start.cget("state")), "disabled")

    def test_disabled_nonfinite_drafts_can_be_saved_as_text(self):
        self.app.vars["chat_interval_min"].set("nan")
        self.app.vars["chat_interval_max"].set("inf")
        self.assertTrue(self.app.save_settings())
        self.assertEqual(self.persisted()["chat_interval_min"], "nan")
        self.assertEqual(self.persisted()["chat_interval_max"], "inf")

    def test_overnight_schedule_is_saveable(self):
        self.app.vars["chat_enabled"].set(True)
        self.app.vars["chat_hours_start"].set("22")
        self.app.vars["chat_hours_end"].set("6")
        self.app.selected["chat"] = [{"id": -3, "name": "test group"}]
        self.text("chat_messages", "Scheduled example")
        self.assertTrue(self.app.save_settings(), self.app.notice_label.cget("text"))
        self.assertEqual(self.cfg.get("chat_hours_start"), 22)
        self.assertEqual(self.cfg.get("chat_hours_end"), 6)

    def test_logs_keep_only_latest_500_events(self):
        for number in range(510):
            self.app._handle_event({"kind": "log", "msg": f"record-{number:03d}"})
        self.assertEqual(len(self.app.log_events), 500)
        visible = self.app.txt_log.get("1.0", "end-1c").splitlines()
        self.assertEqual(len(visible), 500)
        self.assertEqual(visible[0], "record-010")
        self.assertEqual(visible[-1], "record-509")

    def test_language_write_failure_keeps_current_ui_and_original_file(self):
        self.app.vars["api_hash"].set("unsaved-value")
        before = self.path.read_bytes()
        with patch.object(self.cfg, "_write", side_effect=OSError("simulated disk failure")):
            self.app.language_var.set("简体中文")
            self.app._change_language()
        self.assertEqual(self.app.lang, "en")
        self.assertEqual(self.app.language_var.get(), "English")
        self.assertEqual(self.app.vars["api_hash"].get(), "unsaved-value")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("simulated disk failure", self.app.notice_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
