import tkinter as tk
from tkinter import ttk

from simple_sender.ui.widgets import _resolve_widget_bg


def build_led_panel(app, parent):
    frame = ttk.Frame(parent)
    frame.pack(side="right", padx=(8, 0))
    app._led_indicators = {}
    app._led_containers = []
    app._led_bg = _resolve_widget_bg(parent)
    labels = [
        ("endstop", "Endstops"),
        ("probe", "Probe"),
        ("hold", "Hold"),
    ]
    for key, text in labels:
        container = tk.Frame(frame, bg=app._led_bg)
        container.pack(side="left", padx=(0, 8))
        canvas = tk.Canvas(
            container,
            width=18,
            height=18,
            highlightthickness=0,
            bd=0,
            bg=app._led_bg,
        )
        canvas.pack(side="left")
        oval = canvas.create_oval(2, 2, 16, 16, fill="#b0b0b0", outline="#555")
        ttk.Label(container, text=text).pack(side="left", padx=(4, 0))
        app._led_indicators[key] = (canvas, oval)
        app._led_containers.append(container)
    app._led_states = {key: False for key in app._led_indicators}
    app._update_led_panel(False, False, False)


def set_led_state(app, key, on):
    entry = app._led_indicators.get(key)
    if not entry:
        return
    canvas, oval = entry
    color = "#00c853" if on else "#b0b0b0"
    canvas.itemconfig(oval, fill=color)
    app._led_states[key] = on


def update_led_panel(app, endstop: bool, probe: bool, hold: bool):
    set_led_state(app, "endstop", endstop)
    set_led_state(app, "probe", probe)
    set_led_state(app, "hold", hold)


def refresh_led_backgrounds(app):
    bg = _resolve_widget_bg(app)
    app._led_bg = bg
    for canvas, _ in getattr(app, "_led_indicators", {}).values():
        try:
            canvas.config(bg=bg)
        except Exception:
            pass
    for container in getattr(app, "_led_containers", []):
        try:
            container.config(bg=bg)
        except Exception:
            pass
