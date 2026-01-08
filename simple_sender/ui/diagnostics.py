import tkinter as tk
from tkinter import ttk

from simple_sender.ui.popup_utils import center_window

CHECKLIST_ITEMS = [
    "Connect/disconnect: port list refreshes, status shows connected, $G and $$ populate settings.",
    "Units: modal units match controller; $13 reporting indicator updates; unit toggle locked while streaming.",
    "Load G-code: file name, size, estimates, and bounds render in the correct units.",
    "Streaming: start/pause/resume/stop behaves correctly; buffer fill and progress update smoothly.",
    "Completion: popup shows run stats; progress bar resets after acknowledgment.",
    "Overrides: feed/spindle overrides send real-time commands and update UI sliders.",
    "Jogging: on-screen jog works; jog cancel halts motion; joystick hold stops on release.",
    "Safety: joystick safety hold gates actions; blocked actions emit status/log text.",
    "Alarms: alarm/lock messages display; unlock and recovery actions behave as expected.",
]


def open_release_checklist(app):
    existing = getattr(app, "_release_checklist_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass
    win = tk.Toplevel(app)
    app._release_checklist_window = win
    win.title("Release checklist")
    win.minsize(560, 380)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    title = ttk.Label(container, text="Release checklist", font=("TkDefaultFont", 12, "bold"))
    title.pack(anchor="w")
    ttk.Label(
        container,
        text="Use this quick pass before release to confirm the critical GRBL workflows.",
        wraplength=520,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    text = tk.Text(container, wrap="word", height=12)
    text.pack(fill="both", expand=True)
    text.insert("end", "\n".join(f"- {item}" for item in CHECKLIST_ITEMS))
    text.configure(state="disabled")
    center_window(win, app)

    def _on_close():
        app._release_checklist_window = None
        win.destroy()

    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)
