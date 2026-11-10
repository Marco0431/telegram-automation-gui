"""Launch Telegram Automation. Importing the module has no file or network effects."""
import logging
import os
import tkinter as tk
from tkinter import messagebox

from config import _base_dir, system_language
from dpi import enable_high_dpi
from ui import App
from ui_i18n import tr


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.FileHandler(os.path.join(_base_dir(), "app.log"), encoding="utf-8")])
    try:
        # This must run before Tk creates any native window.
        enable_high_dpi()
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception as exc:
        logging.getLogger(__name__).exception("Application failed")
        lang = system_language()
        messagebox.showerror(tr(lang, "fatal"), tr(lang, "fatal_detail", error=str(exc)))
        raise


if __name__ == "__main__":
    main()
