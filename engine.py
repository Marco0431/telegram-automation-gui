"""核心引擎:Telethon 客户端 + 签到 / 水群 / AI 回复。

运行在自己的后台线程与 asyncio 事件循环中。
与 GUI 之间通过两个线程安全队列通信:
  - self._cmd   (GUI -> 引擎): 命令
  - self.out    (引擎 -> GUI): 事件
"""
import asyncio
import logging
import os
import queue
import random
import sys
import threading
import time
from datetime import datetime, timedelta

from telethon import TelegramClient, events
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

log = logging.getLogger("tgbot")


def _base_dir():
    """程序所在目录: 源码运行时是项目目录, 打包成 exe 后是 exe 所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SESSION_PATH = os.path.join(_base_dir(), "userbot.session")

log = logging.getLogger("tgbot")


def _parse_hm(text):
    hh, mm = text.split(":")
    return int(hh), int(mm)


class BotEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.out = queue.Queue()          # 引擎 -> GUI
        self._cmd = queue.Queue()         # GUI -> 引擎
        self._loop = None
        self._thread = None
        self.client = None
        self._auth_phone = None
        self._started = False
        self._tasks = []
        self._ai_last = 0.0

    # ---------- 线程 / 生命周期 ----------
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
        finally:
            self._loop.close()

    def send(self, cmd: dict):
        self._cmd.put(cmd)

    def _emit(self, kind: str, **kw):
        self.out.put({"kind": kind, **kw})

    # ---------- 主循环: 消费 GUI 命令 ----------
    async def _main(self):
        self._emit("status", state="idle", text="引擎已启动，请在「登录」页连接")
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
            except Exception as e:
                log.exception("命令执行失败")
                self._emit("log", msg=f"[错误] 命令执行失败: {e}")

    async def _dispatch(self, cmd):
        t = cmd["type"]
        if t == "connect":
            await self._connect()
        elif t == "send_code":
            await self._send_code(cmd["phone"])
        elif t == "submit_code":
            await self._submit_code(cmd["code"])
        elif t == "submit_2fa":
            await self._submit_2fa(cmd["password"])
        elif t == "disconnect":
            await self._disconnect()
        elif t == "list_dialogs":
            await self._list_dialogs()
        elif t == "start":
            await self._start()
        elif t == "stop":
            await self._stop()
        elif t == "checkin_now":
            await self._do_checkin()
        elif t == "test_ai":
            await self._test_ai()

    # ---------- 登录 ----------
    async def _connect(self):
        if self.client is not None:
            await self._disconnect()
        api_id = self.cfg.get("api_id", "").strip()
        api_hash = self.cfg.get("api_hash", "").strip()
        if not api_id or not api_hash:
            self._emit("log", msg="[错误] 请先填写 api_id 和 api_hash")
            return
        try:
            api_id = int(api_id)
        except ValueError:
            self._emit("log", msg="[错误] api_id 必须是数字")
            return
        self._emit("status", state="connecting", text="正在连接 Telegram …")
        proxy = self._build_proxy()
        try:
            self.client = TelegramClient(SESSION_PATH, api_id, api_hash, proxy=proxy)
            await self.client.connect()
        except Exception as e:
            self._emit("status", state="connect_failed",
                       text=f"连接失败，请检查网络/代理设置: {e}")
            self._emit("log", msg=f"[错误] 连接失败(请确认代理已开启、端口正确): {e}")
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
            return
        if await self.client.is_user_authorized():
            await self._after_login()
        else:
            self._emit("status", state="need_phone", text="未登录，请输入手机号")

    def _build_proxy(self):
        """按配置构造 Telethon 需要的 proxy 元组，未启用则返回 None"""
        if not self.cfg.get("proxy_enabled"):
            return None
        ptype = self.cfg.get("proxy_type", "http")
        host = self.cfg.get("proxy_host", "127.0.0.1")
        try:
            port = int(self.cfg.get("proxy_port", 7890))
        except (TypeError, ValueError):
            port = 7890
        user = self.cfg.get("proxy_username", "") or None
        pwd = self.cfg.get("proxy_password", "") or None
        import socks
        if ptype == "socks5":
            return (socks.SOCKS5, host, port, True, user, pwd)
        return (socks.HTTP, host, port, True, user, pwd)

    async def _send_code(self, phone):
        phone = phone.strip().replace(" ", "")
        if not phone:
            self._emit("log", msg="[错误] 手机号不能为空")
            return
        self._auth_phone = phone
        try:
            await self.client.send_code_request(phone)
            self._emit("status", state="need_code", text=f"验证码已发送到 {phone}")
            self._emit("log", msg=f"验证码已发送到 {phone}")
        except Exception as e:
            self._emit("status", state="need_phone", text=f"发送验证码失败: {e}")
            self._emit("log", msg=f"[错误] 发送验证码失败: {e}")

    async def _submit_code(self, code):
        code = code.strip()
        try:
            await self.client.sign_in(self._auth_phone, code)
            await self._after_login()
        except SessionPasswordNeededError:
            self._emit("status", state="need_2fa", text="该账号开启了二步验证，请输入密码")
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            self._emit("status", state="need_code", text="验证码错误或已过期，请重试")
            self._emit("log", msg="[错误] 验证码错误或已过期")
        except Exception as e:
            self._emit("status", state="need_code", text=f"登录失败: {e}")
            self._emit("log", msg=f"[错误] 登录失败: {e}")

    async def _submit_2fa(self, password):
        try:
            await self.client.sign_in(password=password)
            await self._after_login()
        except Exception as e:
            self._emit("status", state="need_2fa", text=f"二步验证密码错误: {e}")
            self._emit("log", msg=f"[错误] 二步验证失败: {e}")

    async def _after_login(self):
        me = await self.client.get_me()
        name = me.first_name or me.username or "?"
        self._emit("status", state="ready", text=f"已登录: {name}（可在「群组」页刷新列表）")
        self._emit("log", msg=f"登录成功: {name} (id={me.id})")
        self._register_ai_handler()

    async def _disconnect(self):
        await self._stop()
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self._emit("status", state="idle", text="已断开连接")
        self._emit("log", msg="已断开连接")

    # ---------- 群列表 ----------
    async def _list_dialogs(self):
        if self.client is None:
            self._emit("log", msg="[错误] 请先连接登录")
            return
        groups = []
        async for d in self.client.iter_dialogs():
            if d.is_group or d.is_channel:
                kind = "频道" if d.is_channel else "群"
                groups.append({"id": d.id, "name": d.name or "(无名称)", "kind": kind})
        self._emit("dialogs", groups=groups)
        self._emit("log", msg=f"共发现 {len(groups)} 个群/频道")

    # ---------- 启动 / 停止 ----------
    async def _start(self):
        if self.client is None or not self.client.is_connected():
            self._emit("log", msg="[错误] 请先连接登录")
            return
        if self._started:
            self._emit("log", msg="已在运行中")
            return
        self._started = True
        self._tasks = []
        if self.cfg.get("checkin_enabled"):
            self._tasks.append(asyncio.create_task(self._checkin_loop()))
        if self.cfg.get("chat_enabled") and self.cfg.get("chat_groups"):
            self._tasks.append(asyncio.create_task(self._chat_loop()))
        if self.cfg.get("ai_enabled"):
            self._emit("log", msg="AI 回复已开启")
        self._emit("status", state="running", text="运行中")
        self._emit("log", msg=">>> 已开始运行")

    async def _stop(self):
        if not self._started:
            return
        self._started = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        self._emit("status", state="ready", text="已停止")
        self._emit("log", msg=">>> 已停止")

    # ---------- 签到 ----------
    async def _checkin_loop(self):
        while self._started:
            try:
                hh, mm = _parse_hm(self.cfg.get("checkin_time", "09:00"))
            except Exception:
                hh, mm = 9, 0
            now = datetime.now()
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            delay = (target - now).total_seconds() + random.uniform(0, 300)
            self._emit("log", msg=f"下次签到时间: {target.strftime('%m-%d %H:%M')}")
            await asyncio.sleep(delay)
            if not self._started:
                return
            await self._do_checkin()

    async def _do_checkin(self):
        groups = self.cfg.get("checkin_groups", [])
        commands = self.cfg.get("checkin_commands", ["签到"])
        if not groups:
            self._emit("log", msg="[签到] 未配置签到群，跳过")
            return
        for g in groups:
            try:
                entity = await self.client.get_entity(int(g["id"]))
                for cmd in commands:
                    await self.client.send_message(entity, cmd.strip())
                    self._emit("log", msg=f"[签到] {g['name']}: 发送「{cmd}」")
                    await asyncio.sleep(random.uniform(2, 5))
            except Exception as e:
                self._emit("log", msg=f"[签到] {g['name']} 失败: {e}")
            await asyncio.sleep(random.uniform(2, 6))

    # ---------- 水群 ----------
    async def _chat_loop(self):
        while self._started:
            lo = int(self.cfg.get("chat_interval_min", 1800))
            hi = int(self.cfg.get("chat_interval_max", 5400))
            if lo > hi:
                lo, hi = hi, lo
            # 误差时间: 在设定间隔基础上再随机加 0~jitter 分钟, 触发时刻更像真人
            try:
                jitter = int(self.cfg.get("chat_jitter_min", 5)) * 60
            except (TypeError, ValueError):
                jitter = 300
            await asyncio.sleep(random.uniform(lo, hi + jitter))
            if not self._started:
                return
            hour = datetime.now().hour
            start = int(self.cfg.get("chat_hours_start", 9))
            end = int(self.cfg.get("chat_hours_end", 23))
            if not (start <= hour < end):
                continue
            groups = self.cfg.get("chat_groups", [])
            msgs = [m for m in (self.cfg.get("chat_messages") or []) if m.strip()]
            if not groups:
                continue
            mode = self.cfg.get("chat_mode", "pool")
            for g in groups:
                if not self._started:
                    return
                text = None
                if mode == "ai":
                    try:
                        text = await self._gen_ai_chat(g, msgs)
                    except Exception as e:
                        self._emit("log", msg=f"[水群-AI] {g['name']} 生成失败({e})，改用句子池")
                        text = random.choice(msgs).strip() if msgs else None
                else:
                    if msgs:
                        text = random.choice(msgs).strip()
                if not text:
                    continue
                try:
                    entity = await self.client.get_entity(int(g["id"]))
                    await self.client.send_message(entity, text)
                    self._emit("log", msg=f"[水群] {g['name']}: {text}")
                except Exception as e:
                    self._emit("log", msg=f"[水群] {g['name']} 失败: {e}")
                # 群与群之间的随机间隔: 10~40 秒, 更像真人逐个群聊天
                await asyncio.sleep(random.uniform(10, 40))

    async def _gen_ai_chat(self, g, fallback_msgs):
        """AI 水群: 读取该群最近聊天记录，让 AI 以群友身份自然接一句"""
        api_key = self.cfg.get("ai_api_key", "").strip()
        if not api_key:
            raise RuntimeError("未配置 AI API Key")
        try:
            min_len = max(1, int(self.cfg.get("chat_ai_min_len", 5)))
        except (TypeError, ValueError):
            min_len = 5
        try:
            max_len = max(min_len, int(self.cfg.get("chat_ai_max_len", 40)))
        except (TypeError, ValueError):
            max_len = 40
        history = []
        try:
            entity = await self.client.get_entity(int(g["id"]))
            async for m in self.client.iter_messages(entity, limit=12):
                sender = await m.get_sender()
                name = ""
                if sender:
                    name = (sender.first_name or "") + (sender.last_name or "")
                prefix = "我" if m.out else (name or "某人")
                history.append(f"{prefix}: {m.text or ''}")
            history.reverse()
        except Exception:
            raise
        if not history:
            return random.choice(fallback_msgs).strip() if fallback_msgs else None
        system = self.cfg.get("chat_ai_prompt") or (
            "你是一个普通网友，正在一个热闹的聊天群里。"
            "请根据群里的聊天记录自然地参与聊天，说一句口语化、接地气的中文，"
            "像真人一样随意，不要@任何人，不要用'大家好''我觉得'之类的开头。"
        )
        context = "\n".join(history)
        prompt = f"{system}\n\n群里最近的聊天：\n{context}\n\n现在该你了，请说一句参与聊天的话（字数控制在 {min_len}~{max_len} 字之间，不要多）："
        reply = await self._ask_ai(prompt, max_len=max_len)
        # 去掉可能出现的引号/前缀
        reply = reply.strip().strip('“”"\'').strip()
        if len(reply) < min_len:
            return random.choice(fallback_msgs).strip() if fallback_msgs else None
        if len(reply) > max_len:
            reply = reply[:max_len]
        return reply or (random.choice(fallback_msgs).strip() if fallback_msgs else None)

    # ---------- AI 回复 ----------
    def _register_ai_handler(self):
        if self.client is None:
            return

        @self.client.on(events.NewMessage)
        async def on_message(event):
            if not self._started or not self.cfg.get("ai_enabled"):
                return
            msg = event.message
            if msg.out or not msg.text or len(msg.text.strip()) < 1:
                return
            chat_id = msg.chat_id
            groups = self.cfg.get("ai_reply_groups", [])
            gids = [int(g["id"]) for g in groups]
            if chat_id not in gids:
                return
            now = time.time()
            interval = int(self.cfg.get("ai_reply_interval", 60))
            if now - self._ai_last < interval:
                return
            mode = self.cfg.get("ai_reply_mode", "mention")
            if mode == "mention" and not (msg.mentioned or msg.is_reply):
                return
            try:
                prompt = await self._build_prompt(msg)
                reply = await self._ask_ai(prompt)
            except Exception as e:
                self._emit("log", msg=f"[AI] 生成回复失败: {e}")
                return
            self._ai_last = time.time()
            try:
                await self.client.send_message(chat_id, reply, reply_to=msg.id)
                self._emit("log", msg=f"[AI] 回复 {msg.chat_id}: {reply[:40]}")
            except Exception as e:
                self._emit("log", msg=f"[AI] 发送失败: {e}")

    async def _build_prompt(self, msg):
        history = []
        try:
            async for m in self.client.iter_messages(msg.chat_id, limit=10):
                sender = await m.get_sender()
                name = ""
                if sender:
                    name = (sender.first_name or "") + (sender.last_name or "")
                prefix = "我" if m.out else (name or "某人")
                history.append(f"{prefix}: {m.text or ''}")
            history.reverse()
        except Exception:
            pass
        context = "\n".join(history)
        sys_prompt = self.cfg.get("ai_system_prompt") or "你是一个活跃的网友。"
        return f"{sys_prompt}\n\n以下是群里最近的聊天记录：\n{context}\n\n请以你的身份自然接一句话，简短口语化，不超过一两句。"

    async def _ask_ai(self, prompt, system_prompt=None, max_len=None, temperature=0.9):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("未安装 openai 库，请执行 pip install openai")
        cfg = self.cfg
        api_key = cfg.get("ai_api_key", "").strip()
        if not api_key:
            raise RuntimeError("未填写 AI API Key")
        client = OpenAI(base_url=cfg.get("ai_base_url"), api_key=api_key)
        if system_prompt is None:
            system_prompt = cfg.get("ai_system_prompt")
        if max_len is None:
            max_len = int(cfg.get("ai_max_len", 100))
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=cfg.get("ai_model", "deepseek-chat"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=int(max_len),
            temperature=temperature,
        )
        text = (resp.choices[0].message.content or "").strip()
        if len(text) > int(max_len):
            text = text[:int(max_len)]
        return text

    # ---------- AI 可用性检测 ----------
    async def _test_ai(self):
        """用配置的 Base URL / Key / 模型 发一条最短请求, 验证 AI 是否可用"""
        api_key = self.cfg.get("ai_api_key", "").strip()
        if not api_key:
            self._emit("status", state="ready", text="AI 检测: 未填写 API Key")
            self._emit("log", msg="[AI 检测] 未填写 API Key，请先在 ⑤ AI 页填写")
            return
        self._emit("log", msg="[AI 检测] 正在测试连接，请稍候…")
        try:
            reply = await self._ask_ai(
                "只回复两个字：正常",
                system_prompt="你是连接测试工具。",
                max_len=20,
                temperature=0,
            )
            self._emit("log", msg=f"[AI 检测] ✅ 连接成功，模型返回: {reply}")
            self._emit("status", state="ready", text="AI 检测: ✅ 可用")
        except Exception as e:
            self._emit("log", msg=f"[AI 检测] ❌ 连接失败: {e}")
            self._emit("status", state="ready", text="AI 检测: ❌ 失败，见日志")
