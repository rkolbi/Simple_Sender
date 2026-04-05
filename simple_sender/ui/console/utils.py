#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.utils.atomic_files import atomic_write_text

logger = logging.getLogger(__name__)

def _console_theme_palette(app) -> dict[str, str]:
    palette = getattr(app, "theme_palette", None)
    palette = palette if isinstance(palette, dict) else {}
    console = getattr(app, "console", None)

    def _widget_color(option: str, fallback: str) -> str:
        getter = getattr(console, "cget", None)
        if callable(getter):
            try:
                value = str(getter(option) or "").strip()
                if value:
                    return value
            except Exception:
                pass
        return fallback

    base_fg = str(
        palette.get("text_pane_fg")
        or palette.get("fg")
        or _widget_color("foreground", "#F5F7FB")
    )
    return {
        "console_tx": str(palette.get("accent_secondary") or palette.get("accent") or "#8CCBFF"),
        "console_ok": str(palette.get("success_fg") or "#8FD8AE"),
        "console_status": str(palette.get("warning_fg") or "#F0D28B"),
        "console_error": str(palette.get("error_fg") or "#FF9F9F"),
        "console_alarm": str(palette.get("alarm_fg") or palette.get("error_fg") or "#FF7F7F"),
        "default": base_fg,
    }

def setup_console_tags(app):
    """Apply the standard tag palette used by the console text widget."""

    colors = _console_theme_palette(app)
    try:
        app.console.tag_configure("console_tx", foreground=colors["console_tx"])
        app.console.tag_configure("console_ok", foreground=colors["console_ok"])
        app.console.tag_configure("console_status", foreground=colors["console_status"])
        app.console.tag_configure("console_error", foreground=colors["console_error"])
        app.console.tag_configure("console_alarm", foreground=colors["console_alarm"])
    except Exception:
        logger.exception("Failed to configure console tag palette")

def send_console(app):
    s = app.cmd_entry.get().strip()
    if not s:
        return
    accepted = True
    if s == "$$" and hasattr(app, "_request_settings_dump"):
        accepted = bool(app._request_settings_dump())
    else:
        accepted = bool(app._send_manual(s, "console"))
    if accepted:
        app.cmd_entry.delete(0, "end")

def clear_console_log(app):
    if not messagebox.askyesno("Clear console", "Clear the console log?"):
        return
    app.streaming_controller.clear_console()

def save_console_log(app):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_console_{timestamp}.txt"
    initial_dir = Path.home() / "Desktop"
    if not initial_dir.exists():
        initial_dir = Path.home()
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Save console log",
        defaultextension=".txt",
        initialdir=str(initial_dir),
        initialfile=default_name,
        filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
    )
    if not path:
        return
    # Save from stored console lines (position reports are excluded)
    data_lines = [
        text
        for text, tag in app.streaming_controller.get_console_lines()
        if app.streaming_controller.matches_filter((text, tag), for_save=True)
        and (not app.streaming_controller.is_position_line(text))
    ]
    data = "\n".join(data_lines)
    try:
        atomic_write_text(path, data, encoding="utf-8")
    except Exception as e:
        messagebox.showerror("Save failed", str(e))
