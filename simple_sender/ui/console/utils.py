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

logger = logging.getLogger(__name__)

def setup_console_tags(app):
    text_fg = "#111111"
    try:
        app.console.tag_configure("console_tx", background="#e5efff", foreground=text_fg)       # light blue
        app.console.tag_configure("console_ok", background="#e6f7ed", foreground=text_fg)       # light green
        app.console.tag_configure("console_status", background="#fff4d8", foreground=text_fg)   # light orange
        app.console.tag_configure("console_error", background="#ffe5e5", foreground=text_fg)    # light red
        app.console.tag_configure("console_alarm", background="#ffd8d8", foreground=text_fg)    # light red/darker
    except Exception as exc:
        logger.exception("Failed to configure console tags: %s", exc)

def send_console(app):
    s = app.cmd_entry.get().strip()
    if not s:
        return
    if s == "$$" and hasattr(app, "_request_settings_dump"):
        app._request_settings_dump()
    else:
        app._send_manual(s, "console")
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
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    except Exception as e:
        messagebox.showerror("Save failed", str(e))
