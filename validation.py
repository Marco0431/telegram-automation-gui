"""Validate enabled workflows without changing the saved configuration."""

from copy import deepcopy
import math
import re
from urllib.parse import urlsplit

from config import make_defaults


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(value["en"] for value in errors.values()))


def validate_settings(data, scope="all", for_start=False):
    """Return a normalized copy or field-addressable bilingual errors.

    ``all`` permits empty connection credentials while saving; ``connection``
    explicitly requires them. Disabled task drafts are not parsed or rewritten.
    ``ai`` validates only the shared model connection (for connection testing).
    ``checkin`` validates the manual operation even if scheduling is disabled.
    """
    if scope not in ("all", "connection", "ai", "checkin"):
        raise ValueError("Unknown validation scope: " + str(scope))
    if not isinstance(data, dict):
        raise TypeError("Settings must be a dictionary.")
    result = make_defaults(data.get("language", "en"))
    result.update(deepcopy(data))
    errors = {}

    def error(field, en, zh):
        errors.setdefault(field, {"en": en, "zh-CN": zh})

    def text(field, required=False, label=None):
        label = label or field
        en_label, _, zh_label = label.partition(" / ")
        zh_label = zh_label or en_label
        value = result.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            error(field, f"{en_label}: enter text.", f"{zh_label}：请填写文本。")
            return ""
        value = str(value).strip()
        result[field] = value
        if required and not value:
            error(field, f"{en_label} is required.", f"请填写 {zh_label}。")
        return value

    def number(field, minimum, maximum, label, integer=True):
        en_label, _, zh_label = label.partition(" / ")
        zh_label = zh_label or en_label
        value = result.get(field)
        try:
            if isinstance(value, bool):
                raise ValueError()
            parsed = float(value)
            if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
                raise ValueError()
            if integer and not parsed.is_integer():
                raise ValueError()
            result[field] = int(parsed) if integer else parsed
            return parsed
        except (TypeError, ValueError, OverflowError):
            error(field, f"{en_label}: enter {'a whole number' if integer else 'a number'} from {minimum} to {maximum}.",
                  f"{zh_label}：请输入 {minimum} 到 {maximum} 之间的{'整数' if integer else '数字'}。")
            return None

    def groups(field):
        values = result.get(field)
        if not isinstance(values, list) or not values:
            error(field, "Choose at least one target group for this task.", "请为此任务至少选择一个目标群组。")
            return
        normalized = []
        seen = set()
        for entry in values:
            if not isinstance(entry, dict):
                error(field, "A selected group is invalid. Remove it and choose the group again.", "已选群组数据无效，请移除后重新选择。")
                return
            try:
                raw = entry["id"]
                if isinstance(raw, bool) or not re.fullmatch(r"-?\d+", str(raw)):
                    raise ValueError()
                gid = int(raw)
                if gid == 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                error(field, "A selected group ID is invalid. Choose the group again.", "已选群组 ID 无效，请重新选择群组。")
                return
            if gid not in seen:
                normalized.append({"id": gid, "name": str(entry.get("name") or gid)})
                seen.add(gid)
        result[field] = normalized

    if result.get("language") not in ("en", "zh-CN"):
        error("language", "Choose English or Simplified Chinese.", "请选择英语或简体中文。")

    if scope in ("all", "connection") or for_start:
        required = scope == "connection" or for_start
        api_id = text("api_id", required, "API ID")
        text("api_hash", required, "API hash")
        if api_id:
            try:
                valid_api_id = api_id.isascii() and api_id.isdecimal() and int(api_id) > 0
            except ValueError:
                valid_api_id = False
            if not valid_api_id:
                error("api_id", "API ID must be a positive whole number.", "API ID 必须是正整数。")
        if result.get("proxy_enabled"):
            if result.get("proxy_type") not in ("http", "socks5"):
                error("proxy_type", "Choose HTTP or SOCKS5 for the proxy.", "代理类型请选择 HTTP 或 SOCKS5。")
            text("proxy_host", True, "Proxy host / 代理地址")
            number("proxy_port", 1, 65535, "Proxy port / 代理端口")

    checkin = scope == "checkin" or (scope == "all" and result.get("checkin_enabled"))
    chat = scope == "all" and result.get("chat_enabled")
    replies = scope == "all" and result.get("ai_enabled")
    ai = scope == "ai" or replies or (chat and result.get("chat_mode") == "ai")

    if checkin:
        clock = text("checkin_time")
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", clock)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            error("checkin_time", "Check-in time must be HH:MM, from 00:00 to 23:59.", "签到时间请使用 HH:MM 格式，范围 00:00 至 23:59。")
        else:
            result["checkin_time"] = f"{int(match[1]):02d}:{int(match[2]):02d}"
        commands = result.get("checkin_commands")
        if isinstance(commands, str):
            commands = re.split(r"[,，\n]", commands)
        if not isinstance(commands, list) or any(not isinstance(command, str) for command in commands):
            error("checkin_commands", "Enter at least one check-in command.", "请填写至少一条签到指令。")
        else:
            commands = [command.strip() for command in commands if command.strip()]
            if not commands:
                error("checkin_commands", "Enter at least one check-in command.", "请填写至少一条签到指令。")
            result["checkin_commands"] = commands
        groups("checkin_groups")

    if chat:
        groups("chat_groups")
        mode = result.get("chat_mode")
        if mode not in ("pool", "ai"):
            error("chat_mode", "Choose saved messages or AI-generated messages.", "请选择预设消息或 AI 生成消息。")
        lo = number("chat_interval_min", 1, 604800, "Minimum interval (seconds) / 最小间隔（秒）")
        hi = number("chat_interval_max", 1, 604800, "Maximum interval (seconds) / 最大间隔（秒）")
        if lo is not None and hi is not None and lo > hi:
            error("chat_interval_max", "Maximum interval must not be shorter than minimum interval.", "最大间隔不能小于最小间隔。")
        number("chat_hours_start", 0, 23, "Start hour / 开始小时")
        number("chat_hours_end", 0, 24, "End hour / 结束小时")
        number("chat_jitter_min", 0, 1440, "Extra delay (minutes) / 额外延迟（分钟）")
        if mode == "pool":
            messages = result.get("chat_messages")
            if isinstance(messages, str):
                messages = messages.splitlines()
            if not isinstance(messages, list) or any(not isinstance(message, str) for message in messages):
                error("chat_messages", "Enter at least one saved message.", "请填写至少一条预设消息。")
            else:
                result["chat_messages"] = [message.strip() for message in messages if message.strip()]
                if not result["chat_messages"]:
                    error("chat_messages", "Enter at least one saved message.", "请填写至少一条预设消息。")
        else:
            lo = number("chat_ai_min_len", 1, 4000, "Minimum reply length / 最小回复长度")
            hi = number("chat_ai_max_len", 1, 4000, "Maximum reply length / 最大回复长度")
            if lo is not None and hi is not None and lo > hi:
                error("chat_ai_max_len", "Maximum reply length must not be shorter than minimum reply length.", "最大回复长度不能小于最小回复长度。")

    if replies:
        groups("ai_reply_groups")
        if result.get("ai_reply_mode") not in ("mention", "all"):
            error("ai_reply_mode", "Choose mentions/replies or all messages.", "请选择提及/回复或所有消息。")
        number("ai_reply_interval", 1, 86400, "Reply interval (seconds) / 回复间隔（秒）")
        number("ai_max_len", 1, 4000, "Maximum reply length / 最大回复长度")

    if ai:
        address = text("ai_base_url", True, "AI API URL")
        try:
            parsed = urlsplit(address)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(character.isspace() for character in address):
                raise ValueError()
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError()
        except ValueError:
            error("ai_base_url", "Enter an HTTP(S) API base URL without embedded credentials, query parameters or fragments.", "请输入 HTTP(S) API 基础地址，不包含嵌入凭据、查询参数或片段。")
        text("ai_api_key", True, "AI API key")
        text("ai_model", True, "Model / 模型")

    if for_start and not any(result.get(key) for key in ("checkin_enabled", "chat_enabled", "ai_enabled")):
        error("tasks", "Enable at least one task before starting.", "开始运行前请至少启用一项任务。")
    if errors:
        raise ValidationError(errors)
    return result
