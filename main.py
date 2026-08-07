"""tgbot-gui: Telegram 签到 + 水群 + AI 回复 的图形界面入口。"""
import logging
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from config import Config
from engine import BotEngine

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr) if sys.stderr else logging.NullHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)


def _show_fatal(e):
    """pythonw 启动时没有控制台, 崩溃信息写到 app.log, 并弹窗提示"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("FATAL: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
    except Exception:
        pass
    try:
        messagebox.showerror("程序错误", f"发生错误，详情见 app.log\n\n{type(e).__name__}: {e}")
    except Exception:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = Config()
        self.engine = BotEngine(self.cfg)

        self.dialogs = []          # 群列表: [{id, name, kind}]
        self.checkin_sel = []      # [(id, name)]
        self.chat_sel = []
        self.ai_sel = []

        root.title("Telegram 签到 / 水群 / AI")
        root.geometry("860x640")
        root.minsize(760, 560)

        self._build_ui()
        self._load_config_into_ui()
        self.engine.start_thread()
        self.root.after(100, self._poll_events)

        # 关闭窗口时: 如果勾选了后台运行, 则隐藏窗口而非退出
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 后台托盘图标 (pystray 需要单独线程)
        self._start_tray()

    # ==================== UI 构建 ====================
    def _build_ui(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        self.tab_login = ttk.Frame(self.nb)
        self.tab_groups = ttk.Frame(self.nb)
        self.tab_checkin = ttk.Frame(self.nb)
        self.tab_chat = ttk.Frame(self.nb)
        self.tab_ai = ttk.Frame(self.nb)
        for i, (tab, title) in enumerate([
            (self.tab_login, "① 登录"), (self.tab_groups, "② 群组"),
            (self.tab_checkin, "③ 签到"), (self.tab_chat, "④ 水群"),
            (self.tab_ai, "⑤ AI"),
        ]):
            self.nb.add(tab, text=title)

        self._build_login_tab()
        self._build_groups_tab()
        self._build_checkin_tab()
        self._build_chat_tab()
        self._build_ai_tab()
        self._build_bottom_bar()
        self._build_log_bar()

    def _build_login_tab(self):
        f = self.tab_login
        pad = dict(padx=10, pady=6)

        ttk.Label(f, text="API 凭证（在 https://my.telegram.org → API development tools 获取）",
                  foreground="#666").grid(row=0, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(f, text="api_id:").grid(row=1, column=0, sticky="e", **pad)
        self.var_api_id = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_api_id, width=20).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(f, text="api_hash:").grid(row=1, column=2, sticky="e", **pad)
        self.var_api_hash = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_api_hash, width=32).grid(row=1, column=3, sticky="w", **pad)

        # 代理设置区
        proxy_frame = ttk.LabelFrame(f, text="代理设置（连不上 Telegram 时必填，例如 Clash 默认端口 7890）")
        proxy_frame.grid(row=2, column=0, columnspan=4, sticky="we", padx=10, pady=4)
        self.var_proxy_en = tk.BooleanVar()
        ttk.Checkbutton(proxy_frame, text="启用代理", variable=self.var_proxy_en).grid(
            row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Label(proxy_frame, text="类型:").grid(row=0, column=1, sticky="e")
        self.var_proxy_type = tk.StringVar()
        ttk.Combobox(proxy_frame, textvariable=self.var_proxy_type,
                     values=["http", "socks5"], width=8, state="readonly").grid(row=0, column=2, sticky="w")
        ttk.Label(proxy_frame, text="地址:").grid(row=0, column=3, sticky="e")
        self.var_proxy_host = tk.StringVar()
        ttk.Entry(proxy_frame, textvariable=self.var_proxy_host, width=16).grid(row=0, column=4, sticky="w")
        ttk.Label(proxy_frame, text="端口:").grid(row=0, column=5, sticky="e")
        self.var_proxy_port = tk.StringVar()
        ttk.Entry(proxy_frame, textvariable=self.var_proxy_port, width=7).grid(row=0, column=6, sticky="w")
        ttk.Label(proxy_frame, text="（用户/密码可选）").grid(row=1, column=1, columnspan=6, sticky="w", padx=6)
        ttk.Label(proxy_frame, text="用户:").grid(row=2, column=1, sticky="e")
        self.var_proxy_user = tk.StringVar()
        ttk.Entry(proxy_frame, textvariable=self.var_proxy_user, width=16).grid(row=2, column=2, sticky="w")
        ttk.Label(proxy_frame, text="密码:").grid(row=2, column=3, sticky="e")
        self.var_proxy_pass = tk.StringVar()
        ttk.Entry(proxy_frame, textvariable=self.var_proxy_pass, width=16, show="*").grid(row=2, column=4, sticky="w")

        btn_row = ttk.Frame(f)
        btn_row.grid(row=3, column=0, columnspan=4, sticky="w", **pad)
        self.btn_connect = ttk.Button(btn_row, text="连接 Telegram", command=self.on_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = ttk.Button(btn_row, text="断开", command=self.on_disconnect, state="disabled")
        self.btn_disconnect.pack(side="left", padx=6)

        self.status = ttk.Label(f, text="未连接", foreground="#888", font=("", 10))
        self.status.grid(row=4, column=0, columnspan=4, sticky="w", **pad)

        # 登录流程动态区域
        self.auth_frame = ttk.Frame(f)
        self.auth_frame.grid(row=5, column=0, columnspan=4, sticky="we", **pad)

        # 保存按钮
        ttk.Button(f, text="保存登录配置", command=self.save_login).grid(
            row=6, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(f, text="提示：强烈建议使用小号运行，自动化个人号有封号风险。",
                  foreground="#b00").grid(row=7, column=0, columnspan=4, sticky="w", **pad)

    def _build_groups_tab(self):
        f = self.tab_groups
        pad = dict(padx=10, pady=4)

        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=2, sticky="we", **pad)
        ttk.Button(top, text="刷新群列表", command=self.on_refresh).pack(side="left")
        self.lbl_group_count = ttk.Label(top, text="（连接后点刷新）")
        self.lbl_group_count.pack(side="left", padx=10)

        # 左侧: 全部群
        left = ttk.LabelFrame(f, text="全部群 / 频道")
        left.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        self.tree = ttk.Treeview(left, columns=("name", "id", "kind"), show="headings", height=14)
        self.tree.heading("name", text="名称")
        self.tree.heading("id", text="ID")
        self.tree.heading("kind", text="类型")
        self.tree.column("name", width=240)
        self.tree.column("id", width=110)
        self.tree.column("kind", width=50)
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<Double-Button-1>", lambda e: self._move_selected_to("checkin"))

        # 右侧: 三个已选区
        right = ttk.Frame(f)
        right.grid(row=1, column=1, sticky="nsew", padx=(0, 10), pady=4)

        self._build_sel_frame(right, "签到群", "checkin")
        self._build_sel_frame(right, "水群", "chat")
        self._build_sel_frame(right, "AI 回复群", "ai")

        f.columnconfigure(0, weight=3)
        f.columnconfigure(1, weight=2)
        f.rowconfigure(1, weight=1)

        # 底部操作按钮
        ops = ttk.Frame(f)
        ops.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(ops, text="选中群 → 加入签到", command=lambda: self._move_selected_to("checkin")).pack(side="left")
        ttk.Button(ops, text="→ 加入水群", command=lambda: self._move_selected_to("chat")).pack(side="left", padx=6)
        ttk.Button(ops, text="→ 加入 AI 回复", command=lambda: self._move_selected_to("ai")).pack(side="left")

    def _build_sel_frame(self, parent, title, key):
        fr = ttk.LabelFrame(parent, text=title)
        fr.pack(fill="both", expand=True, pady=3)
        lb = tk.Listbox(fr, height=5, selectmode="extended")
        lb.pack(fill="both", expand=True, padx=4, pady=2)
        btn = ttk.Button(fr, text=f"移除选中的{title}",
                         command=lambda k=key: self._remove_from(k))
        btn.pack(pady=(0, 4))
        setattr(self, f"lb_{key}", lb)

    def _build_checkin_tab(self):
        f = self.tab_checkin
        pad = dict(padx=10, pady=6)

        self.var_checkin_en = tk.BooleanVar()
        ttk.Checkbutton(f, text="启用每日自动签到", variable=self.var_checkin_en).grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(f, text="每日签到时间 (HH:MM):").grid(row=1, column=0, sticky="e", **pad)
        self.var_checkin_time = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_checkin_time, width=10).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(f, text="签到指令（多个用逗号隔开）:").grid(row=2, column=0, sticky="e", **pad)
        self.var_checkin_cmds = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_checkin_cmds, width=40).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(f, text="已选签到群:").grid(row=3, column=0, sticky="ne", **pad)
        self.checkin_view = tk.Listbox(f, height=5)
        self.checkin_view.grid(row=3, column=1, sticky="we", **pad)

        self.btn_checkin_now = ttk.Button(f, text="立即签到一次", command=self.on_checkin_now)
        self.btn_checkin_now.grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(f, text="保存签到设置", command=self.save_checkin).grid(
            row=5, column=0, columnspan=2, sticky="w", **pad)

    def _build_chat_tab(self):
        f = self.tab_chat
        pad = dict(padx=10, pady=6)

        self.var_chat_en = tk.BooleanVar()
        ttk.Checkbutton(f, text="启用自动水群", variable=self.var_chat_en).grid(
            row=0, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(f, text="发言间隔(分钟): 最少").grid(row=1, column=0, sticky="e", **pad)
        self.var_chat_lo = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_chat_lo, width=8).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(f, text="最多").grid(row=1, column=2, sticky="e", **pad)
        self.var_chat_hi = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_chat_hi, width=8).grid(row=1, column=3, sticky="w", **pad)

        ttk.Label(f, text="误差时间(分钟):").grid(row=1, column=4, sticky="e", **pad)
        self.var_chat_jitter = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_chat_jitter, width=6).grid(row=1, column=5, sticky="w", **pad)
        ttk.Label(f, text="间隔外再随机加 0~N 分钟，更像真人").grid(row=1, column=6, sticky="w", **pad)

        ttk.Label(f, text="活跃时段: ").grid(row=2, column=0, sticky="e", **pad)
        self.var_chat_hs = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_chat_hs, width=5).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(f, text="时 到").grid(row=2, column=2, sticky="e", **pad)
        self.var_chat_he = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_chat_he, width=5).grid(row=2, column=3, sticky="w", **pad)
        ttk.Label(f, text="时").grid(row=2, column=4, sticky="w", **pad)

        # 发言方式选择
        ttk.Label(f, text="发言方式:").grid(row=3, column=0, sticky="e", **pad)
        self.var_chat_mode = tk.StringVar()
        mode_box = ttk.Combobox(f, textvariable=self.var_chat_mode,
                                values=["pool", "ai"], width=10, state="readonly")
        mode_box.grid(row=3, column=1, sticky="w", **pad)
        mode_box.bind("<<ComboboxSelected>>", lambda e: self._toggle_chat_mode())
        self.lbl_chat_mode_hint = ttk.Label(f, text="")
        self.lbl_chat_mode_hint.grid(row=3, column=2, columnspan=5, sticky="w", **pad)

        # 固定句子池区域
        self.chat_pool_frame = ttk.Frame(f)
        self.chat_pool_frame.grid(row=4, column=0, columnspan=7, sticky="we", **pad)
        ttk.Label(self.chat_pool_frame, text="发言池（每行一句，随机抽取）:").grid(row=0, column=0, sticky="ne", **pad)
        self.txt_messages = tk.Text(self.chat_pool_frame, height=8, width=60)
        self.txt_messages.grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        # AI 水群区域
        self.chat_ai_frame = ttk.Frame(f)
        self.chat_ai_frame.grid(row=5, column=0, columnspan=7, sticky="we", **pad)
        ttk.Label(self.chat_ai_frame,
                  text="AI 水群：到点后读取该群最近聊天记录，让 AI 以群友身份自然接一句。需在 ⑤ AI 页填好 API Key。").grid(
            row=0, column=0, columnspan=6, sticky="w", **pad)
        ttk.Label(self.chat_ai_frame, text="AI 人设（可留空用默认）:").grid(row=1, column=0, sticky="ne", **pad)
        self.txt_chat_ai_prompt = tk.Text(self.chat_ai_frame, height=3, width=60)
        self.txt_chat_ai_prompt.grid(row=1, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(self.chat_ai_frame, text="回复字数:").grid(row=2, column=0, sticky="e", **pad)
        self.var_chat_ai_min = tk.StringVar()
        ttk.Entry(self.chat_ai_frame, textvariable=self.var_chat_ai_min, width=5).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(self.chat_ai_frame, text="~").grid(row=2, column=2, sticky="w", **pad)
        self.var_chat_ai_max = tk.StringVar()
        ttk.Entry(self.chat_ai_frame, textvariable=self.var_chat_ai_max, width=5).grid(row=2, column=3, sticky="w", **pad)
        ttk.Label(self.chat_ai_frame, text="字").grid(row=2, column=4, sticky="w", **pad)

        ttk.Label(f, text="已选水群:").grid(row=6, column=0, sticky="ne", **pad)
        self.chat_view = tk.Listbox(f, height=4)
        self.chat_view.grid(row=6, column=1, columnspan=6, sticky="we", **pad)

        ttk.Button(f, text="保存水群设置", command=self.save_chat).grid(
            row=7, column=0, columnspan=7, sticky="w", **pad)
        self._toggle_chat_mode()

    def _toggle_chat_mode(self):
        """根据发言方式切换显示句子池/AI设置区域"""
        mode = self.var_chat_mode.get()
        if mode == "ai":
            self.chat_pool_frame.grid_remove()
            self.chat_ai_frame.grid()
            self.lbl_chat_mode_hint.config(
                text="AI 智能水群：到点按设定间隔触发，AI 看群上下文自然接话",
                foreground="#090")
        else:
            self.chat_pool_frame.grid()
            self.chat_ai_frame.grid_remove()
            self.lbl_chat_mode_hint.config(
                text="固定句子池：每隔设定间隔随机抽取一句发送",
                foreground="#666")

    def _build_ai_tab(self):
        f = self.tab_ai
        pad = dict(padx=10, pady=5)

        self.var_ai_en = tk.BooleanVar()
        ttk.Checkbutton(f, text="启用 AI 自动回复", variable=self.var_ai_en).grid(
            row=0, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(f, text="Base URL:").grid(row=1, column=0, sticky="e", **pad)
        self.var_ai_base = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_ai_base, width=44).grid(row=1, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(f, text="API Key:").grid(row=2, column=0, sticky="e", **pad)
        self.var_ai_key = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_ai_key, width=44, show="*").grid(row=2, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(f, text="模型:").grid(row=3, column=0, sticky="e", **pad)
        self.var_ai_model = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_ai_model, width=20).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(f, text="回复模式:").grid(row=3, column=2, sticky="e", **pad)
        self.var_ai_mode = tk.StringVar()
        ttk.Combobox(f, textvariable=self.var_ai_mode, values=["mention", "all"], width=10,
                     state="readonly").grid(row=3, column=3, sticky="w", **pad)
        ttk.Label(f, text="mention=仅被@/被回复时 | all=群里所有消息都尝试回复",
                  foreground="#666").grid(row=4, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(f, text="最小回复间隔(秒):").grid(row=5, column=0, sticky="e", **pad)
        self.var_ai_interval = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_ai_interval, width=10).grid(row=5, column=1, sticky="w", **pad)

        ttk.Label(f, text="回复最大长度:").grid(row=5, column=2, sticky="e", **pad)
        self.var_ai_maxlen = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_ai_maxlen, width=10).grid(row=5, column=3, sticky="w", **pad)

        ttk.Label(f, text="System Prompt:").grid(row=6, column=0, sticky="ne", **pad)
        self.txt_ai_prompt = tk.Text(f, height=5, width=60)
        self.txt_ai_prompt.grid(row=6, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(f, text="已选 AI 回复群:").grid(row=7, column=0, sticky="ne", **pad)
        self.ai_view = tk.Listbox(f, height=4)
        self.ai_view.grid(row=7, column=1, columnspan=3, sticky="we", **pad)

        row8 = ttk.Frame(f)
        row8.grid(row=8, column=0, columnspan=4, sticky="w", **pad)
        ttk.Button(row8, text="保存 AI 设置", command=self.save_ai).pack(side="left")
        ttk.Button(row8, text="🔍 检测 AI 是否可用", command=self.on_test_ai).pack(side="left", padx=8)

    def _build_log_bar(self):
        """日志区:固定在窗口最底部,与主界面融为一体"""
        frame = ttk.Frame(self.root)
        frame.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Label(frame, text="─ 运行日志 ─", foreground="#999").pack(anchor="w")
        self.txt_log = tk.Text(frame, state="disabled", wrap="word", height=4,
                               relief="flat", borderwidth=0)
        sb = ttk.Scrollbar(frame, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=2)

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=8)
        self.btn_start = ttk.Button(bar, text="▶ 开始运行", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="■ 停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        self.var_bg_run = tk.BooleanVar()
        ttk.Checkbutton(bar, text="最小化/关闭时后台运行", variable=self.var_bg_run).pack(side="left", padx=12)
        self.btn_restore = ttk.Button(bar, text="恢复窗口", command=self._restore_window, state="disabled")
        self.btn_restore.pack(side="right", padx=6)

    # ==================== 配置读写 ====================
    def _load_config_into_ui(self):
        c = self.cfg
        self.var_api_id.set(c.get("api_id", ""))
        self.var_api_hash.set(c.get("api_hash", ""))
        self.var_proxy_en.set(c.get("proxy_enabled", False))
        self.var_proxy_type.set(c.get("proxy_type", "http"))
        self.var_proxy_host.set(c.get("proxy_host", "127.0.0.1"))
        self.var_proxy_port.set(str(c.get("proxy_port", 7890)))
        self.var_proxy_user.set(c.get("proxy_username", ""))
        self.var_proxy_pass.set(c.get("proxy_password", ""))
        self.var_checkin_en.set(c.get("checkin_enabled", True))
        self.var_checkin_time.set(c.get("checkin_time", "09:00"))
        self.var_checkin_cmds.set(",".join(c.get("checkin_commands", ["签到"])))
        self.var_chat_en.set(c.get("chat_enabled", False))
        self.var_chat_lo.set(str(int(c.get("chat_interval_min", 1800)) // 60))
        self.var_chat_hi.set(str(int(c.get("chat_interval_max", 5400)) // 60))
        self.var_chat_hs.set(str(c.get("chat_hours_start", 9)))
        self.var_chat_he.set(str(c.get("chat_hours_end", 23)))
        self.var_chat_mode.set(c.get("chat_mode", "pool"))
        self.var_chat_jitter.set(str(c.get("chat_jitter_min", 5)))
        self.var_chat_ai_min.set(str(c.get("chat_ai_min_len", 5)))
        self.var_chat_ai_max.set(str(c.get("chat_ai_max_len", 40)))
        self.txt_messages.insert("1.0", "\n".join(c.get("chat_messages", [])))
        self.txt_chat_ai_prompt.insert("1.0", c.get("chat_ai_prompt", ""))
        self.var_ai_en.set(c.get("ai_enabled", False))
        self.var_ai_base.set(c.get("ai_base_url", ""))
        self.var_ai_key.set(c.get("ai_api_key", ""))
        self.var_ai_model.set(c.get("ai_model", ""))
        self.var_ai_mode.set(c.get("ai_reply_mode", "mention"))
        self.var_ai_interval.set(str(c.get("ai_reply_interval", 60)))
        self.var_ai_maxlen.set(str(c.get("ai_max_len", 100)))
        self.txt_ai_prompt.insert("1.0", c.get("ai_system_prompt", ""))
        for key in ("checkin", "chat", "ai"):
            self._load_selected(key)

    def _load_selected(self, key):
        lst = self.cfg.get(f"{key}_groups", [])
        target = getattr(self, f"{key}_sel")
        target.clear()
        for g in lst:
            target.append((g["id"], g["name"]))
        self._refresh_listboxes()

    def _save_selected_to_cfg(self, key):
        target = getattr(self, f"{key}_sel")
        self.cfg.set(f"{key}_groups", [{"id": gid, "name": name} for gid, name in target])

    def save_login(self):
        self.cfg.set("api_id", self.var_api_id.get().strip())
        self.cfg.set("api_hash", self.var_api_hash.get().strip())
        self.cfg.set("proxy_enabled", bool(self.var_proxy_en.get()))
        self.cfg.set("proxy_type", self.var_proxy_type.get())
        self.cfg.set("proxy_host", self.var_proxy_host.get().strip() or "127.0.0.1")
        try:
            self.cfg.set("proxy_port", int(self.var_proxy_port.get()))
        except ValueError:
            self.cfg.set("proxy_port", 7890)
        self.cfg.set("proxy_username", self.var_proxy_user.get().strip())
        self.cfg.set("proxy_password", self.var_proxy_pass.get().strip())

    def save_checkin(self):
        self.cfg.set("checkin_enabled", bool(self.var_checkin_en.get()))
        self.cfg.set("checkin_time", self.var_checkin_time.get().strip() or "09:00")
        cmds = [c.strip() for c in self.var_checkin_cmds.get().split(",") if c.strip()]
        self.cfg.set("checkin_commands", cmds or ["签到"])
        self._save_selected_to_cfg("checkin")
        self._refresh_listboxes()
        messagebox.showinfo("已保存", "签到设置已保存")

    def save_chat(self):
        self.cfg.set("chat_enabled", bool(self.var_chat_en.get()))
        try:
            lo = max(1, int(float(self.var_chat_lo.get()) * 60))
            hi = max(1, int(float(self.var_chat_hi.get()) * 60))
        except ValueError:
            lo, hi = 1800, 5400
        self.cfg.set("chat_interval_min", lo)
        self.cfg.set("chat_interval_max", hi)
        try:
            hs = int(self.var_chat_hs.get())
            he = int(self.var_chat_he.get())
        except ValueError:
            hs, he = 9, 23
        self.cfg.set("chat_hours_start", hs)
        self.cfg.set("chat_hours_end", he)
        self.cfg.set("chat_mode", self.var_chat_mode.get() or "pool")
        try:
            self.cfg.set("chat_jitter_min", max(0, int(float(self.var_chat_jitter.get()))))
        except ValueError:
            self.cfg.set("chat_jitter_min", 5)
        try:
            self.cfg.set("chat_ai_min_len", max(1, int(self.var_chat_ai_min.get())))
        except ValueError:
            self.cfg.set("chat_ai_min_len", 5)
        try:
            self.cfg.set("chat_ai_max_len", max(1, int(self.var_chat_ai_max.get())))
        except ValueError:
            self.cfg.set("chat_ai_max_len", 40)
        msgs = [m.strip() for m in self.txt_messages.get("1.0", "end").splitlines() if m.strip()]
        self.cfg.set("chat_messages", msgs)
        self.cfg.set("chat_ai_prompt", self.txt_chat_ai_prompt.get("1.0", "end").strip())
        self._save_selected_to_cfg("chat")
        self._refresh_listboxes()
        messagebox.showinfo("已保存", "水群设置已保存")

    def save_ai(self):
        self.cfg.set("ai_enabled", bool(self.var_ai_en.get()))
        self.cfg.set("ai_base_url", self.var_ai_base.get().strip())
        self.cfg.set("ai_api_key", self.var_ai_key.get().strip())
        self.cfg.set("ai_model", self.var_ai_model.get().strip())
        self.cfg.set("ai_reply_mode", self.var_ai_mode.get())
        try:
            self.cfg.set("ai_reply_interval", max(10, int(self.var_ai_interval.get())))
        except ValueError:
            pass
        try:
            self.cfg.set("ai_max_len", max(20, int(self.var_ai_maxlen.get())))
        except ValueError:
            pass
        self.cfg.set("ai_system_prompt", self.txt_ai_prompt.get("1.0", "end").strip())
        self._save_selected_to_cfg("ai")
        self._refresh_listboxes()
        messagebox.showinfo("已保存", "AI 设置已保存")

    def on_test_ai(self):
        """先保存配置, 再让引擎测试 AI 连接"""
        self.save_ai()
        self.engine.send({"type": "test_ai"})
        self._append_log("[AI 检测] 已发送测试请求，结果见下方运行日志…")

    # ==================== 群选择逻辑 ====================
    def on_refresh(self):
        self.engine.send({"type": "list_dialogs"})

    def _move_selected_to(self, key):
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            values = self.tree.item(item, "values")
            name, gid = values[0], int(values[1])
            target = getattr(self, f"{key}_sel")
            if (gid, name) not in target:
                target.append((gid, name))
        self._refresh_listboxes()

    def _remove_from(self, key):
        lb = getattr(self, f"lb_{key}")
        sel = lb.curselection()
        target = getattr(self, f"{key}_sel")
        for idx in reversed(sel):
            if 0 <= idx < len(target):
                target.pop(idx)
        self._refresh_listboxes()

    def _refresh_listboxes(self):
        for key in ("checkin", "chat", "ai"):
            lb = getattr(self, f"lb_{key}")
            target = getattr(self, f"{key}_sel")
            lb.delete(0, "end")
            for gid, name in target:
                lb.insert("end", f"{name} ({gid})")
        # 同步到 tab 里的只读视图
        for src, dst in (("checkin", self.checkin_view), ("chat", self.chat_view), ("ai", self.ai_view)):
            dst.delete(0, "end")
            for gid, name in getattr(self, f"{src}_sel"):
                dst.insert("end", f"{name} ({gid})")

    # ==================== 操作 ====================
    def on_connect(self):
        self.save_login()
        self.engine.send({"type": "connect"})

    def on_disconnect(self):
        self.engine.send({"type": "disconnect"})

    def on_checkin_now(self):
        self.engine.send({"type": "checkin_now"})

    def on_start(self):
        self.save_login()
        self.save_checkin()
        self.save_chat()
        self.save_ai()
        self.engine.send({"type": "start"})
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

    def on_stop(self):
        self.engine.send({"type": "stop"})
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    # ==================== 后台运行 ====================
    def on_close(self):
        """点窗口右上角 X 时触发"""
        if self.var_bg_run.get():
            self._hide_to_background()
        else:
            self.on_stop()
            self.root.destroy()

    def _hide_to_background(self):
        """隐藏窗口, 程序继续在后台运行"""
        self.root.withdraw()          # 隐藏主窗口
        self.btn_restore.config(state="normal")
        self._show_tray_balloon("已最小化到后台运行")

    def _restore_window(self):
        self.root.deiconify()         # 重新显示主窗口
        self.root.lift()
        self.root.focus_force()
        self.btn_restore.config(state="disabled")

    def _show_tray_balloon(self, text):
        """在系统托盘弹出气泡提示"""
        try:
            if self.tray_icon is not None:
                self.tray_icon.notify(text, "tgbot-gui")
        except Exception:
            pass

    def _start_tray(self):
        """在后台线程启动托盘图标"""
        try:
            import pystray
        except ImportError:
            self._append_log("提示: 未安装 pystray/PIL, 后台运行时无托盘图标, 可点「恢复窗口」找回")
            return
        try:
            self.tray_icon = pystray.Icon(
                "tgbot",
                self._make_icon(),
                "TG 签到/水群",
                menu=pystray.Menu(
                    pystray.MenuItem("显示窗口", lambda: self.root.after(0, self._restore_window)),
                    pystray.MenuItem("退出程序", self._quit_from_tray),
                ),
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            self._append_log(f"提示: 托盘图标启动失败({e})")

    def _make_icon(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (64, 64), "#0088cc")
        d = ImageDraw.Draw(img)
        d.text((14, 20), "TG", fill="white")
        return img

    def _quit_from_tray(self):
        self.on_stop()
        self.root.quit()

    # ==================== 事件轮询 ====================
    def _poll_events(self):
        try:
            while True:
                ev = self.engine.out.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, ev):
        kind = ev.get("kind")
        if kind == "status":
            self.status.config(text=ev.get("text", ""))
            state = ev.get("state")
            self._switch_auth_ui(state)
            self.btn_disconnect.config(state="normal" if self.engine.client else "disabled")
        elif kind == "log":
            self._append_log(ev.get("msg", ""))
        elif kind == "dialogs":
            self.dialogs = ev["groups"]
            self.lbl_group_count.config(text=f"共 {len(self.dialogs)} 个群")
            for i in self.tree.get_children():
                self.tree.delete(i)
            for g in self.dialogs:
                self.tree.insert("", "end", values=(g["name"], g["id"], g["kind"]))
        elif kind == "auth_ui":
            pass  # 预留

    def _append_log(self, msg):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    # 登录流程动态 UI
    def _switch_auth_ui(self, state):
        for w in self.auth_frame.winfo_children():
            w.destroy()

        def make_row(desc, var, btn_text, on_click, show="*"):
            ttk.Label(self.auth_frame, text=desc).grid(row=0, column=0, sticky="e", padx=4, pady=4)
            ent = ttk.Entry(self.auth_frame, textvariable=var, width=30, show=show)
            ent.grid(row=0, column=1, sticky="w", padx=4, pady=4)
            ttk.Button(self.auth_frame, text=btn_text, command=on_click).grid(row=0, column=2, padx=4)
            ent.bind("<Return>", lambda e: on_click())
            return ent

        if state == "need_phone":
            self.var_phone = tk.StringVar()
            make_row("手机号(带区号):", self.var_phone, "发送验证码",
                     lambda: self.engine.send({"type": "send_code", "phone": self.var_phone.get()}))
        elif state == "need_code":
            self.var_code = tk.StringVar()
            make_row("验证码:", self.var_code, "提交",
                     lambda: self.engine.send({"type": "submit_code", "code": self.var_code.get()}))
        elif state == "need_2fa":
            self.var_2fa = tk.StringVar()
            make_row("二步验证密码:", self.var_2fa, "提交",
                     lambda: self.engine.send({"type": "submit_2fa", "password": self.var_2fa.get()}))
        elif state == "connect_failed":
            ttk.Label(self.auth_frame, text="连接失败，请确认上面代理设置正确且代理软件已开启，然后重试。",
                      foreground="#b00").grid(row=0, column=0, sticky="w", padx=4, pady=4)
            ttk.Button(self.auth_frame, text="重新连接", command=self.on_connect).grid(
                row=0, column=1, padx=6)
        elif state == "ready" or state == "running":
            ttk.Label(self.auth_frame, text="✓ 已登录").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        # 其他状态: 空


def main():
    try:
        root = tk.Tk()
        app = App(root)
        root.mainloop()
    except Exception as e:
        _show_fatal(e)
        raise


if __name__ == "__main__":
    main()
