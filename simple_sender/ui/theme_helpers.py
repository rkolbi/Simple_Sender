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


def _apply_home_button_theme(app, palette: dict):
    style = app.style
    style_name = getattr(app, "home_button_style", "")
    if not style_name:
        return
    button_bg = palette.get("button_bg", "#f0f0f0") if isinstance(palette, dict) else "#f0f0f0"
    accent = "#5b3b89"
    fg = accent
    border = palette.get("border", button_bg) if isinstance(palette, dict) else button_bg
    hover = palette.get("button_hover", button_bg) if isinstance(palette, dict) else button_bg
    pressed = palette.get("button_pressed", button_bg) if isinstance(palette, dict) else button_bg
    disabled_bg = palette.get("bg", "#f0f0f0") if isinstance(palette, dict) else "#f0f0f0"
    disabled_fg = palette.get("muted_fg", "#808080") if isinstance(palette, dict) else "#808080"
    style.configure(
        style_name,
        background=button_bg,
        foreground=fg,
        bordercolor=border,
        lightcolor=button_bg,
        darkcolor=button_bg,
    )
    style.map(
        style_name,
        background=[
            ("pressed", pressed),
            ("active", hover),
            ("disabled", disabled_bg),
        ],
        foreground=[
            ("pressed", fg),
            ("active", fg),
            ("disabled", disabled_fg),
        ],
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
            try:
                _apply_home_button_theme(app, palette or {})
            except Exception:
                pass
            try:
                app.style.configure("TNotebook.Tab", font=app.tab_font)
            except Exception:
                pass
            try:
                app.style.configure("TNotebook.Tab", padding=(10, 4))
            except Exception:
                pass
            refresh_stop_button_backgrounds(app)
            refresh_led_backgrounds(app)
    except tk.TclError:
        pass
