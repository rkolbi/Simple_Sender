"""Startup connection progress popup."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from simple_sender import __version__
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window


def _dialog_alive(dialog: Any) -> bool:
    try:
        return bool(dialog is not None and dialog.winfo_exists())
    except Exception:
        return False


def close_startup_connection_dialog(app: Any) -> None:
    dialog = getattr(app, "_startup_connection_dialog", None)
    app._startup_connection_dialog = None
    app._startup_connection_message_var = None
    if not _dialog_alive(dialog):
        return
    try:
        dialog.destroy()
    except Exception:
        pass


def show_startup_connection_dialog(app: Any, *, port: str | None = None) -> None:
    existing = getattr(app, "_startup_connection_dialog", None)
    if _dialog_alive(existing):
        try:
            existing.lift()
        except Exception:
            pass
        return

    dialog = tk.Toplevel(app)
    app._startup_connection_dialog = dialog
    dialog.title("Simple Sender startup")
    dialog.transient(app)
    dialog.resizable(False, False)
    apply_toplevel_theme(dialog, app)

    frame = ttk.Frame(dialog, padding=14)
    frame.pack(fill="both", expand=True)

    message = tk.StringVar(
        value=(
            f"Welcome to Simple Sender v{__version__}.\n\n"
            "Attempting communication with connected devices."
        )
    )
    app._startup_connection_message_var = message
    ttk.Label(frame, textvariable=message, wraplength=520, justify="left").pack(
        fill="x", pady=(0, 8)
    )
    if port:
        ttk.Label(
            frame,
            text=f"Configured startup port: {port}",
            wraplength=520,
            justify="left",
        ).pack(fill="x", pady=(0, 10))
    ttk.Button(frame, text="Dismiss", command=lambda: close_startup_connection_dialog(app)).pack(
        side="right"
    )

    dialog.protocol("WM_DELETE_WINDOW", lambda: close_startup_connection_dialog(app))
    try:
        dialog.bind("<Escape>", lambda _event: close_startup_connection_dialog(app))
    except Exception:
        pass
    center_window(dialog, app)


__all__ = ["close_startup_connection_dialog", "show_startup_connection_dialog"]
