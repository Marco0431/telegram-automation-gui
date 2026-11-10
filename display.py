"""Scale Tk's pixel-based layout without rebuilding widgets or losing edits."""
from tkinter import ttk
from weakref import WeakKeyDictionary

from dpi import logical_scale, sync_tk_scaling, work_area


class DisplayScale:
    def __init__(self, root):
        self.root = root
        self.factor = sync_tk_scaling(root)
        self._metrics = WeakKeyDictionary()

    def px(self, value):
        if isinstance(value, (list, tuple)):
            return tuple(self.px(item) for item in value)
        return round(value * self.factor)

    def font_size(self, points):
        # Explicit pixel heights keep existing named fonts responsive to DPI
        # changes, including on Tk 8.6 where tk scaling alone is insufficient.
        return -max(1, round(points * 96 / 72 * self.factor))

    def refresh(self):
        factor = logical_scale(self.root)
        if abs(factor - self.factor) < 0.01:
            return False
        self.factor = sync_tk_scaling(self.root)
        return True

    def fit(self, width, height):
        left, top, right, bottom = work_area(self.root)
        # Leave room for the title bar, borders and the taskbar work-area inset.
        available = (max(1, right - left - self.px(24)),
                     max(1, bottom - top - self.px(64)))
        return (max(1, min(self.px(width), available[0])),
                max(1, min(self.px(height), available[1])))

    def _values(self, value):
        if isinstance(value, (str, int, float)):
            value = self.root.tk.splitlist(str(value))
        return tuple(self.root.winfo_pixels(str(item)) for item in value)

    def apply(self, parent):
        """Cache unscaled pixel metrics once; always scale from that baseline.

        Width/height are intentionally excluded: entries and text areas measure
        them in characters/lines, which already follow the font size.
        """
        for widget in parent.winfo_children():
            self._apply_widget(widget)
            self.apply(widget)

    def _apply_widget(self, widget):
        if widget not in self._metrics:
            options = {}
            for name in ("padding", "padx", "pady", "wraplength", "borderwidth", "highlightthickness"):
                if name in widget.keys():
                    values = self._values(widget.cget(name))
                    if values:
                        options[name] = values
            manager = widget.winfo_manager()
            info = widget.pack_info() if manager == "pack" else widget.grid_info() if manager == "grid" else {}
            layout = {name: self._values(info[name]) for name in ("padx", "pady", "ipadx", "ipady") if name in info}
            columns = {}
            if isinstance(widget, ttk.Treeview):
                columns = {column: {key: widget.column(column, key) for key in ("width", "minwidth")}
                           for column in widget["columns"]}
            self._metrics[widget] = options, manager, layout, columns
        options, manager, layout, columns = self._metrics[widget]
        for name, values in options.items():
            scaled = self.px(values)
            widget.configure(**{name: scaled if name == "padding" else scaled[0]})
        if layout and widget.winfo_manager() == manager:
            values = {name: self.px(value) for name, value in layout.items()}
            if manager == "pack":
                widget.pack_configure(**values)
            elif manager == "grid":
                widget.grid_configure(**values)
        for column, values in columns.items():
            widget.column(column, **{name: self.px(value) for name, value in values.items()})
