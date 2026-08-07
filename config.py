"""配置的读取与保存。所有持久化配置存放在 config.json。"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    # 登录
    "api_id": "",
    "api_hash": "",
    # 代理
    "proxy_enabled": False,
    "proxy_type": "http",        # http / socks5
    "proxy_host": "127.0.0.1",
    "proxy_port": 7890,
    "proxy_username": "",
    "proxy_password": "",
    # 签到
    "checkin_groups": [],          # 群 id 列表
    "checkin_commands": ["签到"],
    "checkin_time": "09:00",       # 24小时制 HH:MM
    "checkin_enabled": True,
    # 水群
    "chat_groups": [],             # 群 id 列表
    "chat_mode": "pool",           # pool=固定句子池, ai=AI 智能水群
    "chat_ai_prompt": "",          # AI 水群的 System Prompt,留空用默认
    "chat_jitter_min": 5,          # 触发误差时间(分钟): 在设定间隔之上额外随机加0~N分钟,更像真人
    "chat_ai_min_len": 5,          # AI 水群回复最小字数
    "chat_ai_max_len": 40,         # AI 水群回复最大字数
    "chat_messages": [
        "出来冒个泡证明我还在",
        "今天也是在群里潜水的一天",
        "路过此地顺便水一条发言",
        "刚忙完上来看看有什么新消息",
        "有没有人聊聊天带我一个",
        "日常签到混个脸熟先",
        "周末有人一起打游戏吗带我一个",
        "今天天气不错适合出门走走",
    ],
    "chat_interval_min": 1800,     # 秒
    "chat_interval_max": 5400,     # 秒
    "chat_hours_start": 9,         # 活跃起始小时
    "chat_hours_end": 23,          # 活跃结束小时
    "chat_enabled": False,
    # AI 回复
    "ai_enabled": False,
    "ai_base_url": "https://api.deepseek.com/v1",
    "ai_api_key": "",
    "ai_model": "deepseek-chat",
    "ai_system_prompt": "你是一个活跃的网友，正在一个普通聊天群里和水友聊天。用简短、自然、口语化的中文回复，一次不要超过一两句，不要像客服。",
    "ai_reply_mode": "mention",    # mention=只在被@时回复, all=群里每条消息都回复(配合频率限制)
    "ai_reply_interval": 60,       # 两次AI回复之间的最小间隔(秒), 防止刷屏
    "ai_max_len": 100,             # AI回复最大长度
    "ai_reply_groups": [],         # 启用AI回复的群
}


class Config:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        data = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        self.data = merged

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()
