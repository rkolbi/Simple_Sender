import tkinter as tk
from tkinter import ttk

from simple_sender.ui.led_panel import refresh_led_backgrounds
from simple_sender.ui.widgets import StopSignButton


def refresh_stop_button_backgrounds(app):
    for btn in (getattr(app, "btn_jog_cancel", None), getattr(app, "btn_all_stop", None)):
        if isinstance(btn, StopSignButton):
            btn.refresh_background()


def _apply_icon_button_theme(app, palette: dict):
    style = app.style
    style.configure(
        app.icon_button_style,
        background=palette["button_bg"],
        foreground=palette["fg"],
        bordercolor=palette["border"],
        lightcolor=palette["button_bg"],
        darkcolor=palette["button_bg"],
    )
    style.map(
        app.icon_button_style,
        background=[
            ("pressed", palette["button_pressed"]),
            ("active", palette["button_hover"]),
            ("disabled", palette["bg"]),
        ],
        foreground=[("disabled", palette["muted_fg"])],
    )


def apply_theme(app, theme: str):
    try:
        if theme in app.available_themes:
            app.style.theme_use(theme)
            palette = None
            try:
                palette = app.theme_palettes.get(theme)
            except Exception:
                palette = None
            if palette:
                app.theme_palette = palette
                try:
                    app.configure(background=palette["bg"])
                except Exception:
                    pass
                try:
                    _apply_icon_button_theme(app, palette)
                except Exception:
                    pass
            else:
                app.theme_palette = {}
            refresh_stop_button_backgrounds(app)
            refresh_led_backgrounds(app)
    except tk.TclError:
        pass
