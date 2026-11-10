"""Bilingual, task-oriented Tk interface. No network activity until requested."""
import logging
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from config import Config, LEGACY_AI_PROMPT, default_ai_prompt, default_chat_prompt
from engine import BotEngine
from engine_i18n import translate as engine_translate
from ui_i18n import tr
from validation import ValidationError, validate_settings

GROUP_KEYS = {"checkin": "checkin_groups", "chat": "chat_groups", "ai": "ai_reply_groups"}
LANGUAGES = {"English": "en", "简体中文": "zh-CN"}
CONNECTION_KEYS = ("api_id", "api_hash", "proxy_enabled", "proxy_type", "proxy_host",
                   "proxy_port", "proxy_username", "proxy_password")


class ScrollPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background="#f3f6fa")
        scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=18)
        item = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(item, width=e.width))
        self.body.columnconfigure(0, weight=1)
        self._wheel_id = self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._cleanup)

    def _cleanup(self, event):
        if event.widget is self and self._wheel_id:
            self.winfo_toplevel().unbind("<MouseWheel>", self._wheel_id)
            self._wheel_id = None

    def _wheel(self, event):
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
            return
        while widget is not None:
            if widget is self:
                self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                break
            widget = getattr(widget, "master", None)


class App:
    def __init__(self, root, config=None, engine=None, *, start_engine=True, start_tray=True):
        self.root = root
        self.cfg = config if config is not None else Config()
        self.engine = engine if engine is not None else BotEngine(self.cfg)
        self.lang = self.cfg.get("language", "en")
        self.dialogs, self.log_events = [], []
        self.status_event = self.ai_test_event = None
        self.connection_state = "idle"
        self.running = self.pending = self.dirty = self.closing = False
        self.tray_icon = self._group_dialog = self._poll_id = None
        self._auth_values = {}
        self.auth_pending = False
        self._batching_logs = False
        self._logs_dirty = False
        self._loading = True
        root.geometry("1080x820")
        root.minsize(900, 680)
        self._style()
        self._build_ui(self.cfg.snapshot())
        if self.cfg.load_error:
            self._notice(self.t("load_error", error=self.cfg.load_error), True)
        if start_engine:
            self.engine.start_thread()
        self._poll_id = root.after(100, self._poll_events)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Control-s>", lambda e: self.save_settings())
        if start_tray:
            self._start_tray()

    def t(self, key, **values):
        return tr(self.lang, key, **values)

    def _style(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(background="#f3f6fa")
        style.configure(".", font=("Segoe UI", 10), background="#f3f6fa", foreground="#203044")
        style.configure("TButton", padding=(12, 7))
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10))
        style.configure("TLabelframe", background="white", bordercolor="#d8e1ec")
        style.configure("TLabelframe.Label", background="white", font=("Segoe UI", 11, "bold"))
        style.configure("Card.TFrame", background="white")
        style.configure("Card.TLabel", background="white")
        style.configure("Hint.TLabel", background="white", foreground="#617387")
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"), foreground="#163d66")
        style.configure("Accent.TButton", background="#1675bd", foreground="white")
        style.map("Accent.TButton", background=[("active", "#12649f"), ("disabled", "#bbc8d5")])
        style.configure("Treeview", rowheight=30, background="white", fieldbackground="white")

    def _build_ui(self, data, active=0, task_active=0):
        self._loading = True
        for child in self.root.winfo_children():
            child.destroy()
        self.vars, self.texts, self.field_widgets, self.group_labels = {}, {}, {}, {}
        self.selected = {k: [dict(g) for g in (data.get(v) or [])] for k, v in GROUP_KEYS.items()}
        self.root.title(self.t("app"))
        header = ttk.Frame(self.root, padding=(22, 18, 22, 10))
        header.pack(fill="x")
        heading = ttk.Frame(header)
        heading.pack(side="left", fill="x", expand=True)
        ttk.Label(heading, text=self.t("app"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(heading, text=self.t("tagline"), foreground="#617387").pack(anchor="w", pady=(5, 0))
        language = ttk.Frame(header)
        language.pack(side="right")
        ttk.Label(language, text="Language / 语言").pack(anchor="w", pady=(0, 4))
        self.language_var = tk.StringVar(value="简体中文" if self.lang == "zh-CN" else "English")
        box = ttk.Combobox(language, values=list(LANGUAGES), textvariable=self.language_var, state="readonly", width=15)
        box.pack()
        box.bind("<<ComboboxSelected>>", self._change_language)
        footer = ttk.Frame(self.root, padding=(18, 10))
        footer.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(footer, foreground="#52687e", wraplength=1000)
        self.status_label.pack(anchor="w", pady=(0, 5))
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        self.btn_save = ttk.Button(actions, text=self.t("save"), command=self.save_settings)
        self.btn_save.pack(side="left")
        self.btn_start = ttk.Button(actions, text=self.t("start"), style="Accent.TButton", command=self.on_start)
        self.btn_start.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(actions, text=self.t("stop"), command=self.on_stop)
        self.btn_stop.pack(side="left")
        self.vars["background_run"] = tk.BooleanVar(value=bool(data.get("background_run", False)))
        ttk.Checkbutton(actions, text=self.t("background_run"), variable=self.vars["background_run"]).pack(side="right")
        self.notice_label = ttk.Label(footer, wraplength=1000, foreground="#52687e")
        self.notice_label.pack(anchor="w", pady=(8, 0))
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=18, pady=6)
        self.pages = {}
        for key in ("connection", "tasks", "ai_connection", "activity"):
            self.pages[key] = ttk.Frame(self.nb)
            self.nb.add(self.pages[key], text=self.t(key))
        self._connection_page(data)
        self._tasks_page(data)
        self._ai_connection_page(data)
        self._activity_page()
        self.nb.select(active)
        self.task_nb.select(task_active)
        for var in self.vars.values():
            var.trace_add("write", self._mark_dirty)
        for text in self.texts.values():
            text.edit_modified(False)
            text.bind("<<Modified>>", self._text_changed)
        self._loading = False
        self._refresh_groups()
        self._render_status()
        self._render_ai_test()
        self._render_logs()
        self._sync_controls()
        if self.dirty:
            self._notice(self.t("unsaved"))

    def _card(self, parent, title, row):
        frame = ttk.LabelFrame(parent, text=self.t(title), padding=16)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        frame.columnconfigure(1, weight=1)
        return frame

    def _hint(self, parent, key, row):
        ttk.Label(parent, text=self.t(key), style="Hint.TLabel", wraplength=740,
                  justify="left").grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 12))

    def _entry(self, parent, data, key, row, *, secret=False, value=None):
        ttk.Label(parent, text=self.t(key), style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
        self.vars[key] = tk.StringVar(value=str(data.get(key, "") if value is None else value))
        entry = ttk.Entry(parent, textvariable=self.vars[key], width=32, show="•" if secret else "")
        entry.grid(row=row, column=1, sticky="ew", pady=7)
        self.field_widgets[key] = entry
        if secret:
            show = tk.BooleanVar(value=False)
            ttk.Checkbutton(parent, text=self.t("show_secret"), variable=show,
                            command=lambda: entry.configure(show="" if show.get() else "•")).grid(row=row, column=2, padx=(8, 0))
        return entry

    def _text(self, parent, data, key, row, *, lines=4, is_list=False):
        ttk.Label(parent, text=self.t(key), style="Card.TLabel").grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 5))
        box = tk.Text(parent, height=lines, width=45, wrap="word", relief="solid", borderwidth=1,
                      font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#d8e1ec")
        box.grid(row=row + 1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        value = data.get(key, "")
        box.insert("1.0", "\n".join(value) if is_list and isinstance(value, list) else str(value or ""))
        self.texts[key] = self.field_widgets[key] = box

    def _choice(self, parent, data, key, choices, row):
        ttk.Label(parent, text=self.t(key), style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
        self.vars[key] = tk.StringVar(value=data.get(key, choices[0][0]))
        labels = {value: self.t(label) for value, label in choices}
        shown = tk.StringVar(value=labels.get(self.vars[key].get(), labels[choices[0][0]]))
        box = ttk.Combobox(parent, values=list(labels.values()), textvariable=shown, state="readonly", width=36)
        box.grid(row=row, column=1, columnspan=2, sticky="ew", pady=7)
        def changed(event):
            self.vars[key].set(next(value for value, label in labels.items() if label == shown.get()))
            if key == "chat_mode":
                self._toggle_chat_mode()
        box.bind("<<ComboboxSelected>>", changed)
        self.field_widgets[key] = box

    def _connection_page(self, data):
        page = ScrollPage(self.pages["connection"])
        page.pack(fill="both", expand=True)
        card = self._card(page.body, "account", 0)
        self._hint(card, "credentials_help", 0)
        self._entry(card, data, "api_id", 1)
        self._entry(card, data, "api_hash", 2, secret=True)
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 4))
        self.btn_connect = ttk.Button(row, text=self.t("connect"), style="Accent.TButton", command=self.on_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = ttk.Button(row, text=self.t("disconnect"), command=self.on_disconnect)
        self.btn_disconnect.pack(side="left", padx=8)
        self.auth_frame = ttk.Frame(card, style="Card.TFrame")
        self.auth_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._auth_ui()
        proxy = self._card(page.body, "proxy_enabled", 1)
        self.vars["proxy_enabled"] = tk.BooleanVar(value=bool(data.get("proxy_enabled")))
        ttk.Checkbutton(proxy, text=self.t("proxy_enabled"), variable=self.vars["proxy_enabled"],
                        command=self._toggle_proxy).grid(row=0, column=0, columnspan=3, sticky="w")
        self._hint(proxy, "proxy_help", 1)
        self.proxy_fields = ttk.Frame(proxy, style="Card.TFrame")
        self.proxy_fields.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.proxy_fields.columnconfigure(1, weight=1)
        self._choice(self.proxy_fields, data, "proxy_type", [("http", "HTTP"), ("socks5", "SOCKS5")], 0)
        for row, key in enumerate(("proxy_host", "proxy_port", "proxy_username", "proxy_password"), 1):
            self._entry(self.proxy_fields, data, key, row, secret=key == "proxy_password")
        self._toggle_proxy()

    def _toggle_proxy(self):
        self.proxy_fields.grid() if self.vars["proxy_enabled"].get() else self.proxy_fields.grid_remove()

    def _tasks_page(self, data):
        ttk.Label(self.pages["tasks"], text=self.t("background_help"), foreground="#617387").pack(side="bottom", fill="x", padx=18, pady=8)
        self.task_nb = ttk.Notebook(self.pages["tasks"])
        self.task_nb.pack(fill="both", expand=True, padx=8, pady=8)
        for kind in GROUP_KEYS:
            page = ScrollPage(self.task_nb)
            self.task_nb.add(page, text=self.t(kind))
            key = kind + "_enabled" if kind != "ai" else "ai_enabled"
            self.vars[key] = tk.BooleanVar(value=bool(data.get(key)))
            ttk.Checkbutton(page.body, text=self.t(key), variable=self.vars[key]).grid(row=0, column=0, sticky="w", pady=(0, 14))
            group = self._card(page.body, "groups", 1)
            self.group_labels[kind] = ttk.Label(group, style="Card.TLabel", wraplength=600)
            self.group_labels[kind].grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
            button = ttk.Button(group, text=self.t("choose_groups"), command=lambda k=kind: self.choose_groups(k))
            button.grid(row=0, column=2, sticky="e", padx=(12, 0))
            self.field_widgets[GROUP_KEYS[kind]] = button
            self._hint(group, "groups_help", 1)
            card = self._card(page.body, kind, 2)
            if kind == "checkin":
                self._entry(card, data, "checkin_time", 0)
                self._text(card, data, "checkin_commands", 1, lines=3, is_list=True)
                self._hint(card, "checkin_help", 3)
                self.btn_checkin = ttk.Button(card, text=self.t("checkin_now"), command=self.on_checkin_now)
                self.btn_checkin.grid(row=4, column=0, columnspan=3, sticky="w")
            elif kind == "chat":
                self._chat_fields(card, page.body, data)
            else:
                self._hint(card, "ai_shared", 0)
                ttk.Button(card, text=self.t("open_ai"), command=lambda: self.nb.select(2)).grid(row=1, column=0, sticky="w", pady=(0, 8))
                self._choice(card, data, "ai_reply_mode", [("mention", "mention"), ("all", "all")], 2)
                self._entry(card, data, "ai_reply_interval", 3)
                self._entry(card, data, "ai_max_len", 4)
                self._text(card, data, "ai_system_prompt", 5, lines=4)

    def _chat_fields(self, card, parent, data):
        for row, key in enumerate(("chat_interval_min", "chat_interval_max")):
            self._entry(card, data, key, row, value=self._minutes(data.get(key)))
        self._entry(card, data, "chat_hours_start", 2)
        self._entry(card, data, "chat_hours_end", 3)
        self._hint(card, "hours_help", 4)
        self._choice(card, data, "chat_mode", [("pool", "pool"), ("ai", "ai_generated")], 5)
        self.pool_frame = ttk.Frame(card, style="Card.TFrame")
        self.pool_frame.grid(row=6, column=0, columnspan=3, sticky="ew")
        self.pool_frame.columnconfigure(1, weight=1)
        self._text(self.pool_frame, data, "chat_messages", 0, lines=5, is_list=True)
        self.chat_ai_frame = ttk.Frame(card, style="Card.TFrame")
        self.chat_ai_frame.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.chat_ai_frame.columnconfigure(1, weight=1)
        self._hint(self.chat_ai_frame, "ai_shared", 0)
        self._text(self.chat_ai_frame, data, "chat_ai_prompt", 1, lines=3)
        ttk.Button(self.chat_ai_frame, text=self.t("open_ai"), command=lambda: self.nb.select(2)).grid(row=3, column=0, sticky="w")
        self._toggle_chat_mode()
        advanced = ttk.Frame(parent)
        advanced.grid(row=3, column=0, sticky="ew")
        advanced.columnconfigure(0, weight=1)
        opened = tk.BooleanVar(value=False)
        details = ttk.Frame(advanced)
        details.columnconfigure(1, weight=1)
        ttk.Checkbutton(advanced, text=self.t("advanced"), variable=opened,
                        command=lambda: details.grid(row=1, column=0, sticky="ew") if opened.get() else details.grid_remove()).grid(row=0, column=0, sticky="w", pady=(0, 10))
        for row, key in enumerate(("chat_jitter_min", "chat_ai_min_len", "chat_ai_max_len")):
            self._entry(details, data, key, row)

    def _toggle_chat_mode(self):
        ai = self.vars["chat_mode"].get() == "ai"
        self.pool_frame.grid_remove() if ai else self.pool_frame.grid()
        self.chat_ai_frame.grid() if ai else self.chat_ai_frame.grid_remove()

    def _ai_connection_page(self, data):
        page = ScrollPage(self.pages["ai_connection"])
        page.pack(fill="both", expand=True)
        card = self._card(page.body, "ai_connection", 0)
        self._hint(card, "provider_help", 0)
        for row, key in enumerate(("ai_base_url", "ai_api_key", "ai_model"), 1):
            self._entry(card, data, key, row, secret=key == "ai_api_key")
        self._hint(card, "test_help", 4)
        self.btn_test = ttk.Button(card, text=self.t("test_ai"), command=self.on_test_ai)
        self.btn_test.grid(row=5, column=0, sticky="w")
        self.ai_test_label = ttk.Label(card, style="Hint.TLabel", wraplength=740)
        self.ai_test_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _activity_page(self):
        toolbar = ttk.Frame(self.pages["activity"], padding=14)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text=self.t("log_help"), wraplength=650).pack(side="left")
        ttk.Button(toolbar, text=self.t("clear_log"), command=self._clear_log).pack(side="right")
        area = ttk.Frame(self.pages["activity"], padding=(14, 0, 14, 14))
        area.pack(fill="both", expand=True)
        self.txt_log = tk.Text(area, state="disabled", wrap="word", background="#182738", foreground="#e4edf7",
                               font=("Consolas", 10), relief="flat", padx=12, pady=12)
        bar = ttk.Scrollbar(area, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True)

    @staticmethod
    def _minutes(value):
        try:
            return f"{float(value) / 60:.15g}"
        except (TypeError, ValueError):
            return str(value or "")

    def _collect(self):
        data = self.cfg.snapshot()
        data.update({key: var.get() for key, var in self.vars.items()})
        for key, widget in self.texts.items():
            text = widget.get("1.0", "end-1c")
            data[key] = [line.strip() for line in text.splitlines() if line.strip()] if key in ("chat_messages", "checkin_commands") else text
        for key in ("chat_interval_min", "chat_interval_max"):
            try:
                seconds = float(data[key]) * 60
                if math.isfinite(seconds):
                    data[key] = seconds
            except (ValueError, TypeError):
                pass
        for kind, key in GROUP_KEYS.items():
            data[key] = [dict(group) for group in self.selected[kind]]
        data["language"] = self.lang
        return data

    def _mark_dirty(self, *args):
        if not self._loading:
            self.dirty = True
            self._notice(self.t("unsaved"))

    def _text_changed(self, event):
        if event.widget.edit_modified():
            self._mark_dirty()
            event.widget.edit_modified(False)

    def _change_language(self, event=None):
        new = LANGUAGES[self.language_var.get()]
        if new == self.lang:
            return
        raw_vars = {key: var.get() for key, var in self.vars.items()}
        raw_text = {key: widget.get("1.0", "end-1c") for key, widget in self.texts.items()}
        was_dirty = self.dirty
        draft, patch = self._collect(), {"language": new}
        for key, default in (("ai_system_prompt", default_ai_prompt), ("chat_ai_prompt", default_chat_prompt)):
            builtin = {default("en"), default("zh-CN")}
            if key == "ai_system_prompt":
                builtin.add(LEGACY_AI_PROMPT)
            if draft.get(key) in builtin:
                draft[key] = default(new)
                raw_text[key] = default(new)
            if self.cfg.get(key) in builtin:
                patch[key] = default(new)
        if not self._persist(patch):
            self.language_var.set("简体中文" if self.lang == "zh-CN" else "English")
            return
        active, task = self.nb.index(self.nb.select()), self.task_nb.index(self.task_nb.select())
        self.lang = draft["language"] = new
        self._build_ui(draft, active, task)
        # Language is a display choice, not an implicit save/normalization of drafts.
        self._loading = True
        for key, value in raw_vars.items():
            self.vars[key].set(value)
        for key, value in raw_text.items():
            self.texts[key].delete("1.0", "end")
            self.texts[key].insert("1.0", value)
            self.texts[key].edit_modified(False)
        self._loading = False
        self.dirty = was_dirty
        if not was_dirty:
            self._notice("")
        if self.tray_icon:
            self.tray_icon.title = self.t("app")
            self.tray_icon.update_menu()

    def _notice(self, text, error=False):
        self.notice_label.configure(text=text, foreground="#b33939" if error else "#52687e")

    def _validated(self, scope="all", for_start=False):
        try:
            return validate_settings(self._collect(), scope=scope, for_start=for_start)
        except ValidationError as exc:
            self._notice("\n".join(message[self.lang] for message in exc.errors.values()), True)
            first = next(iter(exc.errors), None)
            if first in CONNECTION_KEYS:
                self.nb.select(0)
            elif first in ("ai_base_url", "ai_api_key", "ai_model"):
                self.nb.select(2)
            elif first and any(first.startswith(k + "_") for k in GROUP_KEYS):
                self.nb.select(1)
                self.task_nb.select(next(i for i, k in enumerate(GROUP_KEYS) if first.startswith(k + "_")))
            if first in self.field_widgets:
                self.field_widgets[first].focus_set()
            return None

    def _persist(self, values):
        try:
            self.cfg.update(values)
            return True
        except (OSError, ValueError) as exc:
            self._notice(self.t("save_error", error=str(exc)), True)
            return False

    def save_settings(self, for_start=False):
        if self.running or self.pending:
            self._notice(self.t("running_edit"), True)
            return False
        data = self._validated(for_start=for_start)
        if data is None or not self._persist(data):
            return False
        self.dirty = False
        self._notice(self.t("saved"))
        return True

    def on_connect(self):
        data = self._validated("connection")
        if data is not None and self._persist({key: data[key] for key in CONNECTION_KEYS}):
            self.connection_state = "connecting"
            self._sync_controls()
            self.engine.send({"type": "connect"})

    def on_disconnect(self):
        self.pending = True
        self._sync_controls()
        self.engine.send({"type": "disconnect"})

    def on_start(self):
        if self.connection_state != "ready":
            self._notice(self.t("connect_first"), True)
            self.nb.select(0)
        elif self.save_settings(for_start=True):
            self.pending = True
            self._notice(self.t("starting"))
            self._sync_controls()
            self.engine.send({"type": "start"})

    def on_stop(self):
        self.pending = True
        self._notice(self.t("stopping"))
        self._sync_controls()
        self.engine.send({"type": "stop"})

    def on_checkin_now(self):
        if self.connection_state != "ready" or self.pending:
            self._notice(self.t("connect_first"), True)
            return
        data = self._validated("checkin")
        if data is None or not messagebox.askyesno(self.t("checkin"), self.t("confirm_checkin", count=len(data["checkin_groups"])), parent=self.root):
            return
        if self._persist({k: v for k, v in data.items() if k.startswith("checkin_")}):
            self.engine.send({"type": "checkin_now"})

    def on_test_ai(self):
        data = self._validated("ai")
        if data is not None:
            self.ai_test_event = {"state": "testing", "text": "…"}
            self._render_ai_test()
            self.engine.send({"type": "test_ai", "settings": data})

    def _refresh_groups(self):
        for kind, label in self.group_labels.items():
            groups = self.selected[kind]
            names = ", ".join(str(g.get("name", g["id"])) for g in groups[:3])
            label.configure(text=self.t("groups_count", count=len(groups), names=names) if groups else self.t("groups_none"))

    def choose_groups(self, kind):
        self.root.update_idletasks()
        dialog = self._group_dialog = tk.Toplevel(self.root)
        dialog.title(self.t("choose_groups"))
        x = max(0, self.root.winfo_x() + (self.root.winfo_width() - 760) // 2)
        y = max(0, self.root.winfo_y() + (self.root.winfo_height() - 540) // 2)
        dialog.geometry(f"760x540+{x}+{y}")
        dialog.minsize(620, 420)
        dialog.transient(self.root)
        dialog.grab_set()
        area = ttk.Frame(dialog, padding=16)
        area.pack(fill="both", expand=True)
        ttk.Label(area, text=self.t("groups_help"), wraplength=700).pack(anchor="w", pady=(0, 10))
        bar = ttk.Frame(area)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text=self.t("search")).pack(side="left")
        search = tk.StringVar()
        ttk.Entry(bar, textvariable=search).pack(side="left", fill="x", expand=True, padx=10)
        ttk.Button(bar, text=self.t("refresh"), command=self.on_refresh).pack(side="right")
        tree_frame = ttk.Frame(area)
        tree_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(tree_frame, columns=("selected", "name", "id", "kind"), show="headings", selectmode="browse")
        for key, label, width in (("selected", "selected", 70), ("name", "group_name", 260), ("id", "ID", 140), ("kind", "group_kind", 90)):
            tree.heading(key, text=self.t(label))
            tree.column(key, width=width, minwidth=50, stretch=key == "name")
        scroll = ttk.Scrollbar(tree_frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        selected = {str(g["id"]): dict(g) for g in self.selected[kind]}
        known = {}
        def render(*args):
            known.update({str(g["id"]): dict(g) for g in self.dialogs})
            for gid, group in selected.items():
                known.setdefault(gid, group)
            query = search.get().casefold().strip()
            tree.delete(*tree.get_children())
            for gid, group in known.items():
                name = str(group.get("name", gid))
                if not query or query in name.casefold() or query in gid:
                    tree.insert("", "end", iid=gid, values=("☑" if gid in selected else "☐", name, gid, self.t(group.get("kind", "group"))))
        def toggle(event=None):
            gid = tree.identify_row(event.y) if event is not None else tree.focus()
            if gid:
                if gid in selected:
                    selected.pop(gid)
                else:
                    selected[gid] = {"id": int(gid), "name": known[gid].get("name", gid)}
                render()
                tree.focus(gid)
        tree.bind("<ButtonRelease-1>", toggle)
        tree.bind("<space>", lambda e: toggle())
        search.trace_add("write", render)
        def select_visible(enabled):
            for gid in tree.get_children():
                if enabled:
                    selected[gid] = {"id": int(gid), "name": known[gid].get("name", gid)}
                else:
                    selected.pop(gid, None)
            render()
        def close():
            self._group_dialog = self._group_render = None
            dialog.destroy()
        def apply():
            self.selected[kind] = [{"id": int(gid), "name": known.get(gid, group).get("name", gid)} for gid, group in selected.items()]
            self._refresh_groups()
            self._mark_dirty()
            close()
        buttons = ttk.Frame(area)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text=self.t("select_visible"), command=lambda: select_visible(True)).pack(side="left")
        ttk.Button(buttons, text=self.t("clear_visible"), command=lambda: select_visible(False)).pack(side="left", padx=6)
        ttk.Button(buttons, text=self.t("apply"), style="Accent.TButton", command=apply).pack(side="right")
        ttk.Button(buttons, text=self.t("cancel"), command=close).pack(side="right", padx=6)
        dialog.protocol("WM_DELETE_WINDOW", close)
        self._group_render = render
        render()

    def on_refresh(self):
        if self.connection_state in ("ready", "running"):
            self.engine.send({"type": "list_dialogs"})
        else:
            self._notice(self.t("connect_first"), True)

    def _event_text(self, event):
        if event.get("message_key"):
            return engine_translate(self.lang, event["message_key"], **event.get("message_params", {}))
        return event.get("text", event.get("msg", ""))

    def _handle_event(self, event):
        kind = event.get("kind")
        if kind == "status":
            previous = self.connection_state
            self.status_event = event
            self.connection_state = event.get("state", "idle")
            self.running = self.connection_state == "running"
            self.pending = False
            self.auth_pending = False
            if previous != self.connection_state:
                self._auth_values.clear()
            self._auth_ui()
            self._render_status()
            self._sync_controls()
            self._record_event(event)
        elif kind == "log":
            self._record_event(event)
        elif kind == "dialogs":
            self.dialogs = event.get("groups", [])
            if self._group_dialog and self._group_dialog.winfo_exists():
                self._group_render()
        elif kind == "ai_test":
            self.ai_test_event = event
            self._render_ai_test()
            self._record_event(event)

    def _record_event(self, event):
        self.log_events.append(event)
        self.log_events = self.log_events[-500:]
        self._logs_dirty = True
        if not self._batching_logs:
            self._render_logs()

    def _poll_events(self):
        if self.closing:
            return
        self._batching_logs = True
        try:
            for _ in range(100):
                try:
                    self._handle_event(self.engine.out.get_nowait())
                except queue.Empty:
                    break
        finally:
            self._batching_logs = False
        if self._logs_dirty:
            self._render_logs()
        self._poll_id = self.root.after(100, self._poll_events)

    def _render_status(self):
        self.status_label.configure(text=self._event_text(self.status_event) if self.status_event else self.t("idle"))

    def _render_ai_test(self):
        event = self.ai_test_event
        self.ai_test_label.configure(text=self._event_text(event) if event else "",
                                     foreground="#b33939" if event and event.get("state") == "error" else "#52687e")
        self.btn_test.configure(state="disabled" if event and event.get("state") == "testing" else "normal")

    def _render_logs(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.insert("end", "\n".join(self._event_text(e) for e in self.log_events))
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")
        self._logs_dirty = False

    def _clear_log(self):
        self.log_events.clear()
        self._render_logs()

    def _sync_controls(self):
        busy, broken = self.running or self.pending, bool(self.cfg.load_error)
        self.btn_save.configure(state="disabled" if busy or broken else "normal")
        self.btn_start.configure(state="normal" if self.connection_state == "ready" and not busy and not broken else "disabled")
        self.btn_stop.configure(state="normal" if self.running and not self.pending else "disabled")
        self.btn_connect.configure(state="disabled" if busy or self.connection_state == "connecting" or broken else "normal")
        self.btn_disconnect.configure(state="normal" if self.connection_state not in ("idle", "connect_failed") and not self.pending else "disabled")
        self.btn_checkin.configure(state="normal" if self.connection_state == "ready" and not busy and not broken else "disabled")

    def _auth_ui(self):
        for child in self.auth_frame.winfo_children():
            child.destroy()
        stages = {"need_phone": ("phone", "send_code", "phone", "send_code"),
                  "need_code": ("code", "submit", "code", "submit_code"),
                  "need_2fa": ("password", "submit", "password", "submit_2fa")}
        if self.connection_state not in stages:
            return
        label, action, field, command = stages[self.connection_state]
        ttk.Label(self.auth_frame, text=self.t(label), style="Card.TLabel").pack(anchor="w", pady=(0, 6))
        var = tk.StringVar(value=self._auth_values.get(field, ""))
        var.trace_add("write", lambda *args: self._auth_values.update({field: var.get()}))
        entry = ttk.Entry(self.auth_frame, textvariable=var, width=36, show="•" if field == "password" else "")
        entry.pack(side="left", fill="x", expand=True)
        def submit():
            if self.auth_pending:
                return
            self.auth_pending = True
            self.auth_submit.configure(state="disabled")
            self.engine.send({"type": command, field: var.get()})
        self.auth_submit = ttk.Button(self.auth_frame, text=self.t(action), command=submit,
                                      state="disabled" if self.auth_pending else "normal")
        self.auth_submit.pack(side="left", padx=8)
        entry.bind("<Return>", lambda e: submit())

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            icon = Image.new("RGB", (64, 64), "#1675bd")
            ImageDraw.Draw(icon).text((16, 22), "TG", fill="white")
            self.tray_icon = pystray.Icon("telegram-automation", icon, self.t("app"), menu=pystray.Menu(
                pystray.MenuItem(lambda item: self.t("show_window"), lambda: self.root.after(0, self._restore_window)),
                pystray.MenuItem(lambda item: self.t("quit"), lambda: self.root.after(0, self._request_quit)),
            ))
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None
            logging.getLogger(__name__).exception("Tray unavailable")

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()

    def on_close(self):
        if self.cfg.get("background_run", False):
            if self.tray_icon and self.tray_icon.visible:
                self.root.withdraw()
            else:
                self._notice(self.t("tray_missing"), True)
        else:
            self._request_quit()

    def _request_quit(self):
        if self.closing or (self.dirty and not messagebox.askyesno(self.t("quit"), self.t("close_unsaved"), parent=self.root)):
            return
        self.closing = True
        if self._poll_id:
            self.root.after_cancel(self._poll_id)
        self._notice(self.t("closing"))
        self.engine.send({"type": "shutdown"})
        self._close_deadline = time.monotonic() + 6
        self._finish_close()

    def _finish_close(self):
        thread = getattr(self.engine, "_thread", None)
        if thread is not None and thread.is_alive() and time.monotonic() < self._close_deadline:
            self.root.after(100, self._finish_close)
            return
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
