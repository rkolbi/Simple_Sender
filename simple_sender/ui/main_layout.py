from tkinter import ttk

from simple_sender.ui.jog_panel import build_jog_panel
from simple_sender.ui.main_tabs import build_main_tabs
from simple_sender.ui.status_bar import build_status_bar


def build_main_layout(app):
    style = app.style
    hidden_style = app.HIDDEN_MPOS_BUTTON_STYLE
    palette = getattr(app, "theme_palette", None) or {}
    bg_color = (
        palette.get("bg")
        or app.cget("background")
        or style.lookup("TLabelframe", "background")
        or style.lookup("TFrame", "background")
        or "#f0f0f0"
    )
    style.configure(
        hidden_style,
        relief="flat",
        borderwidth=0,
        padding=0,
        background=bg_color,
        foreground=bg_color,
    )
    style.map(
        hidden_style,
        background=[("active", bg_color), ("disabled", bg_color), ("!disabled", bg_color)],
        foreground=[("active", bg_color), ("disabled", bg_color), ("!disabled", bg_color)],
    )
    if palette:
        style.configure(
            "SimpleSender.Blue.Horizontal.TProgressbar",
            troughcolor=palette.get("progress_trough", "#2a2d2e"),
            background=palette.get("accent", "#0e639c"),
            bordercolor=palette.get("border", "#3c3c3c"),
            lightcolor=palette.get("accent_hover", "#1177bb"),
            darkcolor=palette.get("accent_pressed", "#0b527f"),
        )
        style.map(
            "SimpleSender.Blue.Horizontal.TProgressbar",
            background=[("disabled", palette.get("border", "#3c3c3c")), ("!disabled", palette.get("accent", "#0e639c"))],
        )
    else:
        style.configure(
            "SimpleSender.Blue.Horizontal.TProgressbar",
            troughcolor="#e3f2fd",
            background="#1976d2",
            bordercolor="#90caf9",
            lightcolor="#64b5f6",
            darkcolor="#1565c0",
        )
        style.map(
            "SimpleSender.Blue.Horizontal.TProgressbar",
            background=[("disabled", "#90caf9"), ("!disabled", "#1976d2")],
        )
    body = ttk.Frame(app, padding=(8, 8))
    body.pack(side="top", fill="both", expand=True)

    build_jog_panel(app, body)
    build_main_tabs(app, body)
    build_status_bar(app, body)
