import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from config import DEFAULTS, default_ai_prompt, default_chat_prompt
from engine import BotEngine, _parse_hm, _within_hours
from engine_i18n import MESSAGES, translate


class MemoryConfig:
    def __init__(self, **values):
        self.data = deepcopy(DEFAULTS)
        self.data.update(language="en", ai_enabled=True,
                         ai_reply_groups=[{"id": -123, "name": "Test"}],
                         ai_system_prompt="", ai_api_key="test-key")
        self.data.update(values)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def snapshot(self):
        return deepcopy(self.data)


def fake_client(authorized=True):
    return SimpleNamespace(
        is_connected=Mock(return_value=True),
        is_user_authorized=AsyncMock(return_value=authorized),
        send_message=AsyncMock(), disconnect=AsyncMock(),
        get_entity=AsyncMock(side_effect=lambda value: value),
        get_me=AsyncMock(return_value=SimpleNamespace(id=7, first_name="Demo", username=None)),
        add_event_handler=Mock(), remove_event_handler=Mock(),
    )


def message(*, mentioned=True, reply_to=None):
    return SimpleNamespace(out=False, text="Hello", chat_id=-123, id=12,
                           mentioned=mentioned, is_reply=reply_to is not None,
                           get_reply_message=AsyncMock(return_value=reply_to))


class SchedulingTests(unittest.TestCase):
    def test_active_hours_support_day_overnight_and_all_day(self):
        self.assertTrue(_within_hours(9, 9, 23))
        self.assertFalse(_within_hours(23, 9, 23))
        self.assertTrue(_within_hours(23, 22, 7))
        self.assertTrue(_within_hours(6, 22, 7))
        self.assertFalse(_within_hours(7, 22, 7))
        self.assertFalse(_within_hours(12, 22, 7))
        self.assertTrue(all(_within_hours(hour, 0, 0) for hour in range(24)))

    def test_invalid_checkin_time_is_not_silently_normalized(self):
        self.assertEqual(_parse_hm("09:30"), (9, 30))
        for value in ("25:00", "09:70", "09", "noon"):
            with self.assertRaises(ValueError):
                _parse_hm(value)

    def test_translation_placeholders_match(self):
        import string
        formatter = string.Formatter()
        for key, (chinese, english) in MESSAGES.items():
            names = lambda text: {field for _, field, _, _ in formatter.parse(text) if field}
            self.assertEqual(names(chinese), names(english), key)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = MemoryConfig()
        self.engine = BotEngine(self.cfg)
        self.client = self.engine.client = fake_client()
        self.engine._self_id = 7
        self.engine._started = True
        self.engine._ai_last = -10000
        self.engine._build_prompt = AsyncMock(return_value="context")

    async def asyncTearDown(self):
        await self.engine._stop()
        task = self.engine._test_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def events(self):
        result = []
        while not self.engine.out.empty():
            result.append(self.engine.out.get_nowait())
        return result

    async def test_ai_test_uses_unsaved_snapshot_without_telegram_status(self):
        self.engine._started = False
        settings = self.cfg.snapshot()
        settings.update(ai_model="unsaved-model", ai_api_key="unsaved-key")
        self.engine._ask_ai = AsyncMock(return_value="OK")
        before = self.cfg.snapshot()
        await self.engine._test_ai(settings)
        events = self.events()
        self.assertEqual([event["kind"] for event in events], ["ai_test", "ai_test"])
        self.assertEqual([event["state"] for event in events], ["testing", "success"])
        call = self.engine._ask_ai.call_args
        self.assertEqual(call.kwargs["settings"]["ai_model"], "unsaved-model")
        self.assertEqual(call.args, ("Reply only: OK",))
        self.assertEqual(call.kwargs["system_prompt"], "You are performing a connection test.")
        self.assertEqual(self.cfg.snapshot(), before)
        self.client.send_message.assert_not_awaited()

    async def test_failed_ai_test_does_not_claim_telegram_is_ready(self):
        self.engine._ask_ai = AsyncMock(side_effect=RuntimeError("offline"))
        await self.engine._test_ai()
        events = self.events()
        self.assertEqual(events[-1]["kind"], "ai_test")
        self.assertEqual(events[-1]["state"], "error")
        self.assertFalse(any(event["kind"] == "status" for event in events))

    async def test_test_request_does_not_block_stop_command(self):
        started = asyncio.Event()
        async def slow_request(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        self.engine._ask_ai = slow_request
        await asyncio.wait_for(self.engine._dispatch({"type": "test_ai"}), 1)
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(self.engine._dispatch({"type": "stop"}), 1)
        self.assertFalse(self.engine._started)
        self.assertFalse(self.engine._test_task.done())

    async def test_start_requires_signed_in_session(self):
        self.engine._started = False
        self.client.is_user_authorized.return_value = False
        await self.engine._start()
        self.assertFalse(self.engine._started)
        self.assertEqual(self.events()[-1]["state"], "idle")

    async def test_disconnect_only_emits_final_idle_not_a_ready_intermediate(self):
        await self.engine._disconnect()
        statuses = [event["state"] for event in self.events() if event["kind"] == "status"]
        self.assertEqual(statuses, ["idle"])
        self.client.disconnect.assert_awaited_once()

    async def test_start_requires_an_enabled_task_with_targets(self):
        self.engine._started = False
        self.cfg.data.update(checkin_enabled=False, chat_enabled=False, ai_reply_groups=[])
        await self.engine._start()
        self.assertFalse(self.engine._started)
        self.assertEqual(self.events()[-1]["message_key"], "no_tasks")

    async def test_reply_to_someone_else_does_not_trigger_mention_mode(self):
        self.engine._ask_ai = AsyncMock(return_value="A reply")
        incoming = message(mentioned=False, reply_to=SimpleNamespace(out=False, sender_id=99))
        await self.engine._on_message(SimpleNamespace(message=incoming))
        self.engine._ask_ai.assert_not_awaited()
        self.client.send_message.assert_not_awaited()

    async def test_reply_to_self_triggers_mention_mode(self):
        self.engine._ask_ai = AsyncMock(return_value="A reply")
        incoming = message(mentioned=False, reply_to=SimpleNamespace(out=True, sender_id=7))
        await self.engine._on_message(SimpleNamespace(message=incoming))
        self.client.send_message.assert_awaited_once_with(-123, "A reply", reply_to=12)

    async def test_concurrent_messages_only_start_one_ai_request(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def request(*args, **kwargs):
            started.set()
            await release.wait()
            return "A reply"
        self.engine._ask_ai = AsyncMock(side_effect=request)
        first = asyncio.create_task(self.engine._on_message(SimpleNamespace(message=message())))
        await asyncio.wait_for(started.wait(), 1)
        await self.engine._on_message(SimpleNamespace(message=message()))
        self.engine._ask_ai.assert_awaited_once()
        release.set()
        await first
        self.client.send_message.assert_awaited_once()

    async def test_stop_prevents_late_ai_send_even_if_request_swallows_cancellation(self):
        started = asyncio.Event()
        async def request(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "Late reply"
        self.engine._ask_ai = request
        task = asyncio.create_task(self.engine._on_message(SimpleNamespace(message=message())))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(self.engine._stop(), 1)
        await task
        self.client.send_message.assert_not_awaited()
        self.assertFalse(self.engine._ai_busy)
        self.assertEqual(self.engine._reply_tasks, set())

    async def test_manual_checkin_is_cancellable_before_sending(self):
        self.cfg.data.update(checkin_groups=[{"id": -123, "name": "Test"}], checkin_commands=["/checkin"])
        started = asyncio.Event()
        async def resolve(*args):
            started.set()
            await asyncio.Event().wait()
        self.client.get_entity.side_effect = resolve
        await self.engine._dispatch({"type": "checkin_now"})
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(self.engine._stop(), 1)
        self.assertTrue(self.engine._checkin_task.done())
        self.client.send_message.assert_not_awaited()

    async def test_login_automatically_loads_groups_and_handler(self):
        self.engine._list_dialogs = AsyncMock()
        await self.engine._after_login()
        self.engine._list_dialogs.assert_awaited_once()
        self.client.add_event_handler.assert_called_once()
        self.assertEqual(self.engine._self_id, 7)

    async def test_shutdown_cleans_test_request_and_telegram_client(self):
        started = asyncio.Event()
        async def slow_test(*args):
            started.set()
            await asyncio.Event().wait()
        self.engine._test_task = asyncio.create_task(slow_test())
        await started.wait()
        self.engine.send({"type": "shutdown"})
        await asyncio.wait_for(self.engine._main(), 1)
        self.assertTrue(self.engine._test_task.cancelled())
        self.client.disconnect.assert_awaited_once()
        self.assertIsNone(self.engine.client)

    async def test_shutdown_interrupts_pending_connection_operation(self):
        started = asyncio.Event()
        async def blocked_connect():
            started.set()
            await asyncio.Event().wait()
        self.engine._connect = blocked_connect
        self.engine._loop = asyncio.get_running_loop()
        self.engine.send({"type": "connect"})
        main = asyncio.create_task(self.engine._main())
        await asyncio.wait_for(started.wait(), 1)
        self.engine.send({"type": "shutdown"})
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(main, 1)
        self.client.disconnect.assert_awaited_once()
        self.assertIsNone(self.engine.client)

    async def test_async_http_has_timeout_closes_and_preserves_custom_prompt(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))])
        request = AsyncMock(return_value=response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
        context = Mock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        settings = self.cfg.snapshot()
        settings["ai_system_prompt"] = "Custom instruction"
        with patch("openai.AsyncOpenAI", return_value=context) as factory:
            self.assertEqual(await self.engine._ask_ai("Hi", settings=settings), "hello")
        self.assertEqual(factory.call_args.kwargs["timeout"], 30.0)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
        context.__aexit__.assert_awaited_once()
        self.assertEqual(request.call_args.kwargs["messages"][0]["content"], "Custom instruction")

    async def test_default_prompt_and_status_follow_language(self):
        self.engine._message("status", "stopped", state="ready")
        event = self.events()[-1]
        self.assertEqual(event["text"], "Tasks stopped.")
        self.assertEqual(translate("zh-CN", event["message_key"], **event["message_params"]), "任务已停止。")
        self.assertNotEqual(default_ai_prompt("en"), default_ai_prompt("zh-CN"))
        self.engine._history = AsyncMock(return_value="A conversation")
        self.engine._ask_ai = AsyncMock(return_value="A short message")
        self.cfg.data["chat_ai_prompt"] = ""
        await self.engine._gen_ai_chat({"id": -123}, [])
        self.assertEqual(self.engine._ask_ai.call_args.kwargs["system_prompt"], default_chat_prompt("en"))


if __name__ == "__main__":
    unittest.main()
