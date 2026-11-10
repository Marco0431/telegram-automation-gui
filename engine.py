"""Telegram tasks run in one background asyncio loop, separate from Tk."""
import asyncio
from copy import deepcopy
import logging
import os
import queue
import random
import threading
import time
from datetime import datetime, timedelta

from telethon import TelegramClient, events
from telethon.errors import (
    PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError,
)

from config import _base_dir, default_ai_prompt, default_chat_prompt
from engine_i18n import translate

log = logging.getLogger("tgbot")
SESSION_PATH = os.path.join(_base_dir(), "userbot.session")


def _parse_hm(text):
    hh, mm = (int(part) for part in text.split(":"))
    if not 0 <= hh <= 23 or not 0 <= mm <= 59:
        raise ValueError("Invalid HH:MM")
    return hh, mm


def _within_hours(hour, start, end):
    """Equal endpoints mean all day; reversed ranges cross midnight."""
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end


class BotEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.out = queue.Queue()
        self._cmd = queue.Queue()
        self._loop = None
        self._thread = None
        self._main_task = None
        self.client = None
        self._auth_phone = None
        self._self_id = None
        self._started = False
        self._tasks = []
        self._reply_tasks = set()
        self._test_task = None
        self._checkin_task = None
        self._generation = 0
        self._ai_last = 0.0
        self._ai_busy = False
        self._checkin_busy = False
        self._ai_handler = None

    def _snapshot(self):
        return self.cfg.snapshot()

    def _text(self, key, **params):
        return translate(self.cfg.get("language", "zh-CN"), key, **params)

    def _message(self, kind, key, *, state=None, **params):
        event = {"kind": kind, "message_key": key, "message_params": params}
        event["msg" if kind == "log" else "text"] = self._text(key, **params)
        if state is not None:
            event["state"] = state
        self.out.put(event)

    def _emit(self, kind, **kwargs):
        self.out.put({"kind": kind, **kwargs})

    def start_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="bot-engine")
        self._thread.start()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except asyncio.CancelledError:
            pass  # A shutdown request interrupts a pending connection/authentication call.
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def send(self, cmd):
        self._cmd.put(deepcopy(cmd))
        if cmd.get("type") == "shutdown" and self._loop and self._main_task:
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)
            except RuntimeError:
                pass  # The loop has already finished its cleanup.

    async def _main(self):
        self._main_task = asyncio.current_task()
        self._message("status", "idle", state="idle")
        try:
            while True:
                try:
                    cmd = self._cmd.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue
                if cmd.get("type") == "shutdown":
                    break
                try:
                    await self._dispatch(cmd)
                except Exception as exc:
                    log.exception("Engine command failed")
                    self._message("log", "command_failed", error=str(exc))
                    if cmd.get("type") in ("connect", "start"):
                        state = "ready" if await self._authorized() else "idle"
                        self._message("status", "command_failed", state=state, error=str(exc))
        finally:
            if self._test_task and not self._test_task.done():
                self._test_task.cancel()
                await asyncio.gather(self._test_task, return_exceptions=True)
            await self._disconnect()
            self._main_task = None

    async def _dispatch(self, cmd):
        kind = cmd["type"]
        if kind == "connect":
            await self._connect()
        elif kind == "send_code":
            await self._send_code(cmd["phone"])
        elif kind == "submit_code":
            await self._submit_code(cmd["code"])
        elif kind == "submit_2fa":
            await self._submit_2fa(cmd["password"])
        elif kind == "disconnect":
            await self._disconnect()
        elif kind == "list_dialogs":
            await self._list_dialogs()
        elif kind == "start":
            await self._start()
        elif kind == "stop":
            await self._stop()
        elif kind == "checkin_now":
            if self._checkin_task and not self._checkin_task.done():
                self._message("log", "checkin_busy")
            else:
                self._checkin_task = asyncio.create_task(self._do_checkin())
        elif kind == "test_ai":
            if self._test_task and not self._test_task.done():
                self._message("log", "ai_test_busy")
            else:
                settings = deepcopy(cmd.get("settings") or self._snapshot())
                self._test_task = asyncio.create_task(self._test_ai(settings))

    async def _authorized(self):
        client = self.client
        if client is None or not client.is_connected():
            return False
        try:
            return await client.is_user_authorized()
        except Exception:
            return False

    async def _connect(self):
        if self.client is not None:
            await self._disconnect(emit_status=False)
        api_id = str(self.cfg.get("api_id", "")).strip()
        api_hash = str(self.cfg.get("api_hash", "")).strip()
        if not api_id or not api_hash:
            self._message("status", "credentials_missing", state="connect_failed")
            return
        try:
            api_id = int(api_id)
            if api_id <= 0:
                raise ValueError
        except ValueError:
            self._message("status", "api_id_invalid", state="connect_failed")
            return
        self._message("status", "connecting", state="connecting")
        try:
            self.client = TelegramClient(SESSION_PATH, api_id, api_hash, proxy=self._build_proxy())
            await asyncio.wait_for(self.client.connect(), timeout=30)
            if await self.client.is_user_authorized():
                await self._after_login()
            else:
                self._message("status", "need_phone", state="need_phone")
        except Exception as exc:
            if self.client is not None:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            self.client = None
            self._message("status", "connect_failed", state="connect_failed", error=str(exc))
            self._message("log", "connect_failed", error=str(exc))

    def _build_proxy(self):
        if not self.cfg.get("proxy_enabled"):
            return None
        import socks
        port = int(self.cfg.get("proxy_port", 7890))
        if not 1 <= port <= 65535:
            raise ValueError("Proxy port must be between 1 and 65535")
        proxy_type = socks.SOCKS5 if self.cfg.get("proxy_type") == "socks5" else socks.HTTP
        return (proxy_type, self.cfg.get("proxy_host", "127.0.0.1"), port, True,
                self.cfg.get("proxy_username") or None, self.cfg.get("proxy_password") or None)

    async def _send_code(self, phone):
        phone = phone.strip().replace(" ", "")
        if not phone:
            self._message("status", "phone_empty", state="need_phone")
            return
        self._auth_phone = phone
        try:
            await self.client.send_code_request(phone)
            self._message("status", "code_sent", state="need_code")
        except Exception as exc:
            self._message("status", "code_failed", state="need_phone", error=str(exc))

    async def _submit_code(self, code):
        try:
            await self.client.sign_in(self._auth_phone, code.strip())
            await self._after_login()
        except SessionPasswordNeededError:
            self._message("status", "need_2fa", state="need_2fa")
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            self._message("status", "invalid_code", state="need_code")
        except Exception as exc:
            self._message("status", "login_failed", state="need_code", error=str(exc))

    async def _submit_2fa(self, password):
        try:
            await self.client.sign_in(password=password)
            await self._after_login()
        except Exception as exc:
            self._message("status", "password_failed", state="need_2fa", error=str(exc))

    async def _after_login(self):
        me = await self.client.get_me()
        self._self_id = me.id
        name = me.first_name or me.username or "?"
        self._message("status", "logged_in", state="ready", name=name)
        self._message("log", "logged_in", name=name)
        self._register_ai_handler()
        await self._list_dialogs()

    async def _disconnect(self, emit_status=True):
        await self._stop(emit_status=False)
        client, self.client = self.client, None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                log.exception("Telegram disconnect failed")
        self._self_id = None
        self._ai_handler = None
        if emit_status:
            self._message("status", "disconnected", state="idle")

    async def _list_dialogs(self):
        if not await self._authorized():
            self._message("log", "login_first")
            return
        groups = []
        try:
            async for dialog in self.client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    kind = "group" if dialog.is_group else "channel"
                    groups.append({"id": dialog.id, "name": dialog.name or self._text("unnamed"),
                                   "kind": kind})
        except Exception as exc:
            self._message("log", "groups_failed", error=str(exc))
            return
        self._emit("dialogs", groups=groups)
        self._message("log", "groups_loaded", count=len(groups))

    async def _start(self):
        if not await self._authorized():
            self._message("status", "login_first", state="idle")
            return
        if self._started:
            self._message("status", "already_running", state="running")
            return
        settings = self._snapshot()
        if not any(settings.get(enabled) and settings.get(groups) for enabled, groups in (
                ("checkin_enabled", "checkin_groups"), ("chat_enabled", "chat_groups"),
                ("ai_enabled", "ai_reply_groups"))):
            self._message("status", "no_tasks", state="ready")
            return
        self._generation += 1
        self._started = True
        self._tasks = []
        if settings.get("checkin_enabled"):
            self._tasks.append(asyncio.create_task(self._checkin_loop()))
        if settings.get("chat_enabled") and settings.get("chat_groups"):
            self._tasks.append(asyncio.create_task(self._chat_loop()))
        if settings.get("ai_enabled"):
            self._message("log", "ai_enabled")
        self._message("status", "running", state="running")

    async def _stop(self, emit_status=True):
        was_running = self._started
        self._started = False
        self._generation += 1
        tasks = set(self._tasks) | self._reply_tasks
        if self._checkin_task and not self._checkin_task.done():
            tasks.add(self._checkin_task)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        self._reply_tasks.clear()
        self._ai_busy = False
        self._checkin_busy = False
        if emit_status and (was_running or self.client is not None):
            state = "ready" if await self._authorized() else "idle"
            self._message("status", "stopped", state=state)

    def _may_send(self, client, generation, require_running=True):
        return (self.client is client and self._generation == generation
                and client is not None and client.is_connected()
                and (self._started or not require_running))

    async def _checkin_loop(self):
        while self._started:
            hh, mm = _parse_hm(self.cfg.get("checkin_time", "09:00"))
            now = datetime.now()
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            self._message("log", "next_checkin", when=target.strftime("%m-%d %H:%M"))
            await asyncio.sleep((target - now).total_seconds() + random.uniform(0, 300))
            if self._started:
                await self._do_checkin(require_running=True)

    async def _do_checkin(self, require_running=False):
        if self._checkin_busy:
            self._message("log", "checkin_busy")
            return
        if not await self._authorized():
            self._message("log", "login_first")
            return
        if self._checkin_busy:
            self._message("log", "checkin_busy")
            return
        settings = self._snapshot()
        groups = settings.get("checkin_groups", [])
        commands = [c.strip() for c in settings.get("checkin_commands", []) if c.strip()]
        if not groups or not commands:
            self._message("log", "checkin_empty")
            return
        self._checkin_busy = True
        client, generation = self.client, self._generation
        try:
            for group in groups:
                try:
                    entity = await client.get_entity(int(group["id"]))
                    for command in commands:
                        if not self._may_send(client, generation, require_running):
                            return
                        await client.send_message(entity, command)
                        self._message("log", "checkin_sent", name=group["name"], command=command)
                        await asyncio.sleep(random.uniform(2, 5))
                except Exception as exc:
                    self._message("log", "checkin_failed", name=group["name"], error=str(exc))
                await asyncio.sleep(random.uniform(2, 6))
        finally:
            self._checkin_busy = False

    async def _chat_loop(self):
        client, generation = self.client, self._generation
        while self._started:
            settings = self._snapshot()
            lo = int(settings.get("chat_interval_min", 1800))
            hi = int(settings.get("chat_interval_max", 5400))
            jitter = max(0, int(settings.get("chat_jitter_min", 5))) * 60
            await asyncio.sleep(random.uniform(lo, hi) + random.uniform(0, jitter))
            if not self._may_send(client, generation):
                return
            settings = self._snapshot()
            if not _within_hours(datetime.now().hour, int(settings.get("chat_hours_start", 9)),
                                 int(settings.get("chat_hours_end", 23))):
                continue
            messages = [m.strip() for m in settings.get("chat_messages", []) if m.strip()]
            for group in settings.get("chat_groups", []):
                if not self._may_send(client, generation):
                    return
                if settings.get("chat_mode") == "ai":
                    try:
                        text = await self._gen_ai_chat(group, messages)
                    except Exception as exc:
                        self._message("log", "chat_fallback", name=group["name"], error=str(exc))
                        text = random.choice(messages) if messages else None
                else:
                    text = random.choice(messages) if messages else None
                if not text or not self._may_send(client, generation):
                    continue
                try:
                    entity = await client.get_entity(int(group["id"]))
                    if not self._may_send(client, generation):
                        return
                    await client.send_message(entity, text)
                    self._message("log", "chat_sent", name=group["name"])
                except Exception as exc:
                    self._message("log", "chat_failed", name=group["name"], error=str(exc))
                await asyncio.sleep(random.uniform(10, 40))

    async def _history(self, chat_id, limit):
        history = []
        async for message in self.client.iter_messages(chat_id, limit=limit):
            if not message.text:
                continue
            sender = await message.get_sender()
            name = " ".join(filter(None, (getattr(sender, "first_name", ""),
                                          getattr(sender, "last_name", ""))))
            prefix = self._text("self") if message.out else name or self._text("someone")
            history.append(f"{prefix}: {message.text}")
        return "\n".join(reversed(history))

    async def _gen_ai_chat(self, group, fallback_messages):
        settings = self._snapshot()
        minimum = int(settings.get("chat_ai_min_len", 5))
        maximum = int(settings.get("chat_ai_max_len", 40))
        context = await self._history(int(group["id"]), 12)
        if not context:
            return random.choice(fallback_messages) if fallback_messages else None
        prompt = self._text("chat_prompt", context=context, minimum=minimum, maximum=maximum)
        system = settings.get("chat_ai_prompt") or default_chat_prompt(settings.get("language", "zh-CN"))
        reply = await self._ask_ai(prompt, system_prompt=system, max_len=maximum, settings=settings)
        reply = reply.strip().strip('“”"\'').strip()
        if len(reply) < minimum:
            return random.choice(fallback_messages) if fallback_messages else None
        return reply[:maximum]

    def _register_ai_handler(self):
        if self.client is None:
            return
        if self._ai_handler is not None:
            self.client.remove_event_handler(self._ai_handler)
        self._ai_handler = self._on_message
        self.client.add_event_handler(self._ai_handler, events.NewMessage())

    async def _on_message(self, event):
        settings = self._snapshot()
        if not self._started or not settings.get("ai_enabled") or self._ai_busy:
            return
        message = event.message
        if message.out or not message.text or not message.text.strip():
            return
        if message.chat_id not in {int(g["id"]) for g in settings.get("ai_reply_groups", [])}:
            return
        client, generation = self.client, self._generation
        interval = int(settings.get("ai_reply_interval", 60))
        if time.monotonic() - self._ai_last < interval:
            return
        task = asyncio.current_task()
        self._reply_tasks.add(task)
        self._ai_busy = True
        try:
            if settings.get("ai_reply_mode", "mention") == "mention" and not message.mentioned:
                if not message.is_reply:
                    return
                replied = await message.get_reply_message()
                if replied is None or not (replied.out or (
                        self._self_id is not None and replied.sender_id == self._self_id)):
                    return
            if not self._may_send(client, generation):
                return
            self._ai_last = time.monotonic()
            try:
                prompt = await self._build_prompt(message)
                reply = await self._ask_ai(prompt, settings=settings)
            except Exception as exc:
                self._message("log", "ai_generation_failed", error=str(exc))
                return
            if not reply or not self._may_send(client, generation):
                return
            try:
                await client.send_message(message.chat_id, reply, reply_to=message.id)
                self._message("log", "ai_sent", chat_id=message.chat_id)
            except Exception as exc:
                self._message("log", "ai_send_failed", error=str(exc))
        finally:
            self._ai_busy = False
            self._reply_tasks.discard(task)

    async def _build_prompt(self, message):
        try:
            context = await self._history(message.chat_id, 10)
        except Exception:
            context = ""
        return self._text("reply_prompt", context=context, message=message.text)

    async def _ask_ai(self, prompt, system_prompt=None, max_len=None, temperature=0.9, settings=None):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError(self._text("openai_missing")) from None
        settings = settings if settings is not None else self._snapshot()
        api_key = str(settings.get("ai_api_key", "")).strip()
        if not api_key:
            raise RuntimeError(self._text("ai_key_missing"))
        if system_prompt is None:
            system_prompt = settings.get("ai_system_prompt") or default_ai_prompt(settings.get("language", "zh-CN"))
        maximum = int(max_len if max_len is not None else settings.get("ai_max_len", 100))
        async with AsyncOpenAI(base_url=settings.get("ai_base_url"), api_key=api_key,
                               timeout=30.0, max_retries=0) as client:
            response = await client.chat.completions.create(
                model=settings.get("ai_model", "deepseek-chat"),
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": prompt}],
                max_tokens=max(32, min(maximum * 2, 4096)), temperature=temperature,
            )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError(self._text("ai_empty"))
        return text[:maximum]

    async def _test_ai(self, settings=None):
        settings = deepcopy(settings if settings is not None else self._snapshot())
        self._message("ai_test", "ai_testing", state="testing")
        try:
            language = settings.get("language", "zh-CN")
            reply = await self._ask_ai(translate(language, "test_prompt"),
                                       system_prompt=translate(language, "test_system"),
                                       max_len=20, temperature=0, settings=settings)
            self._message("ai_test", "ai_test_success", state="success", reply=reply)
        except asyncio.CancelledError:
            self._message("ai_test", "ai_test_cancelled", state="error")
            raise
        except Exception as exc:
            self._message("ai_test", "ai_test_failed", state="error", error=str(exc))
