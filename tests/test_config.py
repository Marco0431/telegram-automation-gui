"""Persistence and validation regressions; no Telegram or model requests."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import Config, DEFAULTS, LEGACY_AI_PROMPT, default_ai_prompt, make_defaults
from validation import ValidationError, validate_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix=".test-config-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def write(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_new_language_follows_locale_and_starts_disabled(self):
        for locale_name, language in (("en_US", "en"), ("zh_CN", "zh-CN"), ("Chinese (Simplified)_China", "zh-CN")):
            with self.subTest(locale_name=locale_name), patch("config.locale.getlocale", return_value=(locale_name, "UTF-8")):
                cfg = Config(self.path)
                self.assertEqual(cfg.get("language"), language)
                self.assertEqual(cfg.get("ai_system_prompt"), default_ai_prompt(language))
                self.assertFalse(cfg.get("checkin_enabled"))
                self.assertFalse(cfg.get("chat_enabled"))
                self.assertFalse(cfg.get("ai_enabled"))
                self.assertFalse(self.path.exists())

    def test_legacy_config_keeps_language_and_user_content(self):
        self.write({"api_id": "123", "checkin_commands": ["自定义签到"],
                    "chat_messages": ["my custom sentence"], "ai_system_prompt": "custom prompt"})
        with patch("config.locale.getlocale", return_value=("en_US", "UTF-8")):
            cfg = Config(self.path)
        self.assertEqual(cfg.get("language"), "zh-CN")
        self.assertEqual(cfg.get("checkin_commands"), ["自定义签到"])
        self.assertEqual(cfg.get("chat_messages"), ["my custom sentence"])
        self.assertEqual(cfg.get("ai_system_prompt"), "custom prompt")
        self.assertTrue(cfg.get("checkin_enabled"))
        self.assertEqual(cfg.get("ai_max_len"), 100)

    def test_legacy_prompt_and_groups_migration(self):
        legacy = [{"id": -42, "name": "old group"}]
        self.write({"ai_groups": legacy, "ai_reply_groups": []})
        cfg = Config(self.path)
        self.assertEqual(cfg.get("ai_reply_groups"), legacy)
        self.assertNotIn("ai_groups", cfg.snapshot())
        self.assertEqual(cfg.get("ai_system_prompt"), LEGACY_AI_PROMPT)
        canonical = [{"id": -43, "name": "new group"}]
        self.write({"ai_groups": legacy, "ai_reply_groups": canonical})
        self.assertEqual(Config(self.path).get("ai_reply_groups"), canonical)

    def test_id_only_group_lists_migrate_without_rewriting_original_file(self):
        self.write({"checkin_groups": [-1, "-2"], "chat_groups": [" -3 "],
                    "ai_groups": [-4, {"id": "-5", "name": "Named group"}]})
        original = self.path.read_bytes()
        cfg = Config(self.path)
        self.assertIsNone(cfg.load_error)
        self.assertEqual(cfg.get("checkin_groups"), [{"id": -1, "name": "-1"}, {"id": -2, "name": "-2"}])
        self.assertEqual(cfg.get("chat_groups"), [{"id": -3, "name": "-3"}])
        self.assertEqual(cfg.get("ai_reply_groups"), [{"id": -4, "name": "-4"}, {"id": -5, "name": "Named group"}])
        self.assertEqual(self.path.read_bytes(), original)

    def test_unknown_group_shapes_block_saving_instead_of_losing_data(self):
        for value in ([{"name": "missing ID"}], [False], [0], ["not an ID"], {"id": -1}):
            with self.subTest(value=value):
                self.write({"checkin_groups": value})
                original = self.path.read_bytes()
                cfg = Config(self.path)
                self.assertIn("checkin_groups", cfg.load_error)
                self.assertEqual(cfg.get("checkin_groups"), [])
                with self.assertRaises(ValueError):
                    cfg.save()
                self.assertEqual(self.path.read_bytes(), original)

    def test_atomic_update_writes_once_and_reloads(self):
        cfg = Config(self.path)
        with patch("config.os.replace", wraps=__import__("os").replace) as replace:
            cfg.update({"api_id": "123", "api_hash": "example", "background_run": True})
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(Config(self.path).snapshot(), cfg.snapshot())

    def test_failed_replace_keeps_memory_file_and_cleans_temporary(self):
        cfg = Config(self.path)
        cfg.update({"api_id": "123"})
        saved = self.path.read_bytes()
        before = cfg.snapshot()
        with patch("config.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                cfg.update({"api_id": "999"})
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(cfg.snapshot(), before)
        self.assertEqual(list(self.path.parent.glob(".config-*.tmp")), [])

    def test_invalid_json_blocks_save_without_overwriting(self):
        for invalid in ("{broken", "[1, 2]", '"string"'):
            with self.subTest(invalid=invalid):
                self.path.write_text(invalid, encoding="utf-8")
                cfg = Config(self.path)
                self.assertTrue(cfg.load_error)
                with self.assertRaises(ValueError):
                    cfg.update({"language": "en"})
                self.assertEqual(self.path.read_text(encoding="utf-8"), invalid)

    def test_mutable_defaults_snapshots_and_get_results_are_independent(self):
        before = deepcopy(DEFAULTS)
        first = Config(self.path)
        second = Config(self.path)
        snapshot = first.snapshot()
        snapshot["checkin_commands"].append("changed")
        returned = first.get("checkin_commands")
        returned.append("changed too")
        self.assertEqual(first.snapshot(), second.snapshot())
        self.assertEqual(DEFAULTS, before)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = make_defaults("en")

    def error_fields(self, data, **kwargs):
        with self.assertRaises(ValidationError) as caught:
            validate_settings(data, **kwargs)
        for value in caught.exception.errors.values():
            self.assertTrue(value["en"])
            self.assertTrue(value["zh-CN"])
        return caught.exception.errors

    def test_disabled_drafts_are_preserved_and_not_validated(self):
        self.data.update(checkin_time="unfinished", chat_interval_min="not yet",
                         ai_max_len="", ai_base_url="incomplete")
        before = deepcopy(self.data)
        self.assertEqual(validate_settings(self.data), before)
        self.assertEqual(self.data, before)

    def test_connection_scope_requires_credentials_but_ignores_tasks(self):
        self.data.update(api_id=" 123 ", api_hash=" hash ", checkin_enabled=True, checkin_time="bad")
        normalized = validate_settings(self.data, scope="connection")
        self.assertEqual(normalized["api_id"], "123")
        self.assertEqual(normalized["api_hash"], "hash")
        self.data["api_id"] = "-1"
        self.assertIn("api_id", self.error_fields(self.data, scope="connection"))
        self.data["api_id"] = ""
        self.assertIn("api_id", self.error_fields(self.data, scope="connection"))

    def test_proxy_only_validated_if_enabled(self):
        self.data.update(proxy_port="unfinished")
        validate_settings(self.data)
        self.data["proxy_enabled"] = True
        self.assertIn("proxy_port", self.error_fields(self.data))
        self.data["proxy_port"] = "65536"
        self.assertIn("proxy_port", self.error_fields(self.data))
        self.data["proxy_port"] = "7890"
        self.assertEqual(validate_settings(self.data)["proxy_port"], 7890)

    def test_checkin_normalization_deduplicates_groups_without_mutating(self):
        self.data.update(checkin_enabled=True, checkin_time="9:05",
                         checkin_commands=" /checkin， hello\nbye ",
                         checkin_groups=[{"id": "-123", "name": "one"}, {"id": -123, "name": "duplicate"}])
        before = deepcopy(self.data)
        result = validate_settings(self.data)
        self.assertEqual(result["checkin_time"], "09:05")
        self.assertEqual(result["checkin_commands"], ["/checkin", "hello", "bye"])
        self.assertEqual(result["checkin_groups"], [{"id": -123, "name": "one"}])
        self.assertEqual(self.data, before)

    def test_invalid_checkin_fields_are_reported_together(self):
        self.data.update(checkin_enabled=True, checkin_time="24:01", checkin_commands=[])
        errors = self.error_fields(self.data)
        self.assertTrue({"checkin_time", "checkin_commands", "checkin_groups"} <= errors.keys())

    def test_manual_checkin_validates_even_when_task_disabled(self):
        self.assertIn("checkin_groups", self.error_fields(self.data, scope="checkin"))

    def test_scheduled_messages_validate_ranges_and_content(self):
        self.data.update(chat_enabled=True, chat_groups=[{"id": -1, "name": "test"}],
                         chat_interval_min=300, chat_interval_max=120,
                         chat_hours_start=23, chat_hours_end=9, chat_messages=[])
        errors = self.error_fields(self.data)
        self.assertTrue({"chat_interval_max", "chat_messages"} <= errors.keys())
        self.data.update(chat_interval_max=600, chat_hours_start=0, chat_hours_end=24, chat_messages=["message"])
        self.assertEqual(validate_settings(self.data)["chat_hours_end"], 24)

    def test_scheduled_hours_allow_overnight_and_equal_for_all_day(self):
        self.data.update(chat_enabled=True, chat_groups=[{"id": -1, "name": "test"}], chat_messages=["message"])
        for start, end in ((22, 6), (0, 0), (9, 9), (0, 24)):
            with self.subTest(start=start, end=end):
                self.data.update(chat_hours_start=start, chat_hours_end=end)
                result = validate_settings(self.data)
                self.assertEqual(result["chat_hours_start"], start)
                self.assertEqual(result["chat_hours_end"], end)

    def test_ai_test_needs_only_current_connection_not_task_groups(self):
        self.data.update(ai_base_url=" http://127.0.0.1:8000/v1 ", ai_api_key=" example ", ai_model=" model ",
                         ai_enabled=True, ai_reply_groups=[], checkin_time="bad", proxy_port="bad", proxy_enabled=True)
        result = validate_settings(self.data, scope="ai")
        self.assertEqual(result["ai_model"], "model")
        self.assertEqual(result["ai_base_url"], "http://127.0.0.1:8000/v1")
        for url in ("ftp://example.com", "https://user:pass@example.com", "https://example.com?key=secret", "https://exam ple.com"):
            self.data["ai_base_url"] = url
            self.assertIn("ai_base_url", self.error_fields(self.data, scope="ai"))

    def test_start_requires_enabled_task_and_connection(self):
        errors = self.error_fields(self.data, for_start=True)
        self.assertTrue({"tasks", "api_id", "api_hash"} <= errors.keys())
        self.data.update(api_id="123", api_hash="hash", checkin_enabled=True,
                         checkin_groups=[{"id": -1, "name": "test"}])
        validate_settings(self.data, for_start=True)

    def test_bad_numeric_values_never_silently_fall_back(self):
        self.data.update(ai_enabled=True, ai_reply_groups=[{"id": -1, "name": "test"}], ai_api_key="key")
        for value in (float("nan"), float("inf"), True, -1, "text", "1.5"):
            self.data["ai_max_len"] = value
            self.assertIn("ai_max_len", self.error_fields(self.data))


if __name__ == "__main__":
    unittest.main()
