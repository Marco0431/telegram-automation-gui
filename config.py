"""Local settings, legacy migration and atomic persistence."""

from copy import deepcopy
import json
import locale
import os
import re
import sys
import tempfile
import threading


def _base_dir():
    """Use the executable directory for packaged builds and source directory otherwise."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_base_dir(), "config.json")
LANGUAGES = ("zh-CN", "en")
LEGACY_AI_PROMPT = "你是一个活跃的网友，正在一个普通聊天群里和水友聊天。用简短、自然、口语化的中文回复，一次不要超过一两句，不要像客服。"
AI_PROMPTS = {
    "zh-CN": "你是 Telegram 群里的 AI 助手。结合上下文，用简短、自然的中文回应当前消息，通常一到两句。不要编造事实、假装真人或无故提及其他成员；信息不足时直说。",
    "en": "You are an AI assistant in a Telegram group. Respond to the current message in concise, natural English, usually one or two sentences. Use the conversation as context. Do not invent facts, pretend to be human, or mention other members unnecessarily. Say when context is missing.",
}
CHAT_PROMPTS = {
    "zh-CN": "根据 Telegram 群里最近的聊天，用一句简短、相关的中文参与讨论。不要编造个人经历、假装真人、无故 @ 他人或重复此前的消息。没有足够上下文时，不要编造话题。",
    "en": "Based on the recent Telegram group conversation, contribute one brief, relevant sentence in English. Do not invent personal experiences, pretend to be human, mention other members unnecessarily, or repeat an earlier message. Do not invent a topic when context is insufficient.",
}


def default_ai_prompt(language):
    return AI_PROMPTS.get(language, AI_PROMPTS["en"])


def default_chat_prompt(language):
    return CHAT_PROMPTS.get(language, CHAT_PROMPTS["en"])


def system_language():
    try:
        name = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        name = ""
    return "zh-CN" if name.lower().startswith(("zh", "chinese")) else "en"


DEFAULTS = {
    "language": "en", "background_run": False,
    "api_id": "", "api_hash": "",
    "proxy_enabled": False, "proxy_type": "http", "proxy_host": "127.0.0.1",
    "proxy_port": 7890, "proxy_username": "", "proxy_password": "",
    "checkin_groups": [], "checkin_commands": ["/checkin"],
    "checkin_time": "09:00", "checkin_enabled": False,
    "chat_groups": [], "chat_mode": "pool", "chat_ai_prompt": "",
    "chat_jitter_min": 5, "chat_ai_min_len": 5, "chat_ai_max_len": 120,
    "chat_messages": [], "chat_interval_min": 1800, "chat_interval_max": 5400,
    "chat_hours_start": 9, "chat_hours_end": 23, "chat_enabled": False,
    "ai_enabled": False, "ai_base_url": "https://api.deepseek.com/v1",
    "ai_api_key": "", "ai_model": "deepseek-chat",
    "ai_system_prompt": AI_PROMPTS["en"], "ai_reply_mode": "mention",
    "ai_reply_interval": 60, "ai_max_len": 240, "ai_reply_groups": [],
}

LEGACY_MESSAGES = [
    "出来冒个泡证明我还在", "今天也是在群里潜水的一天", "路过此地顺便水一条发言",
    "刚忙完上来看看有什么新消息", "有没有人聊聊天带我一个", "日常签到混个脸熟先",
    "周末有人一起打游戏吗带我一个", "今天天气不错适合出门走走",
]


def make_defaults(language=None, legacy=False):
    language = language or system_language()
    data = deepcopy(DEFAULTS)
    data["language"] = language if language in LANGUAGES else "en"
    data["ai_system_prompt"] = default_ai_prompt(data["language"])
    if legacy:
        # Preserve old behavior even when older files omit optional fields.
        data.update(checkin_commands=["签到"], checkin_enabled=True,
                    chat_messages=deepcopy(LEGACY_MESSAGES), chat_ai_max_len=40,
                    ai_max_len=100, ai_system_prompt=LEGACY_AI_PROMPT)
    elif data["language"] == "zh-CN":
        data.update(checkin_commands=["签到"], chat_ai_max_len=40, ai_max_len=100)
    return data


def _migrate_groups(groups, field):
    """Accept historic ID-only lists and the current ID/name dictionaries."""
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise ValueError(f"{field} must be a list of group IDs or group objects.")
    normalized = []
    for entry in groups:
        group = deepcopy(entry) if isinstance(entry, dict) else {"id": entry}
        raw_id = group.get("id")
        value = str(raw_id).strip()
        if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)) or not re.fullmatch(r"-?\d+", value):
            raise ValueError(f"{field} contains an invalid group ID; repair the original configuration file.")
        try:
            group_id = int(value)
        except ValueError as exc:
            raise ValueError(f"{field} contains an invalid group ID.") from exc
        if group_id == 0:
            raise ValueError(f"{field} contains group ID 0; choose a real group ID.")
        group["id"] = group_id
        group["name"] = str(group.get("name") or group_id)
        normalized.append(group)
    return normalized


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = os.path.abspath(os.fspath(path))
        self._lock = threading.RLock()
        self.data = {}
        self.load_error = None
        self.load()

    def load(self):
        with self._lock:
            exists = os.path.exists(self.path)
            self.load_error = None
            data = {}
            if exists:
                try:
                    with open(self.path, "r", encoding="utf-8-sig") as stream:
                        data = json.load(stream)
                    if not isinstance(data, dict):
                        raise ValueError("The configuration must be a JSON object.")
                except (OSError, ValueError, UnicodeError) as exc:
                    self.load_error = str(exc)
                    data = {}
            legacy = exists and "language" not in data
            language = data.get("language", "zh-CN" if legacy else system_language())
            if language not in LANGUAGES:
                language = "en"
            merged = make_defaults(language, legacy=legacy)
            merged.update(deepcopy(data))
            merged["language"] = language
            if not merged.get("ai_reply_groups") and data.get("ai_groups"):
                merged["ai_reply_groups"] = deepcopy(data["ai_groups"])
            merged.pop("ai_groups", None)
            for field in ("checkin_groups", "chat_groups", "ai_reply_groups"):
                try:
                    merged[field] = _migrate_groups(merged.get(field), field)
                except ValueError as exc:
                    # Render a safe disabled UI, but never overwrite unrecognized data.
                    self.load_error = str(exc) if not self.load_error else self.load_error + "\n" + str(exc)
                    merged[field] = []
            self.data = merged

    def snapshot(self):
        with self._lock:
            return deepcopy(self.data)

    def _write(self, data):
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=parent,
                                             prefix=".config-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def update(self, values):
        """Persist one transaction; failures leave memory and the old file intact."""
        if not isinstance(values, dict):
            raise TypeError("Settings must be a dictionary.")
        with self._lock:
            if self.load_error:
                raise ValueError("The configuration could not be read. Repair or back up the original file before saving.")
            candidate = self.snapshot()
            candidate.update(deepcopy(values))
            self._write(candidate)
            self.data = candidate

    def recover(self, values):
        """Explicit recovery only, after caller has arranged preservation/consent."""
        if not isinstance(values, dict):
            raise TypeError("Settings must be a dictionary.")
        with self._lock:
            candidate = deepcopy(values)
            self._write(candidate)
            self.data = candidate
            self.load_error = None

    def save(self):
        self.update({})

    def get(self, key, default=None):
        with self._lock:
            return deepcopy(self.data.get(key, default))

    def set(self, key, value):
        self.update({key: value})
