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

import tkinter as tk
from tkinter import ttk

from simple_sender.ui.popup_utils import center_window

def ensure_gcode_loading_popup(app):
    if app._gcode_load_popup is not None:
        try:
            if app._gcode_load_popup.winfo_exists():
                return
        except Exception:
            pass
    popup = tk.Toplevel(app)
    popup.title("Loading G-code")
    popup.transient(app)
    popup.resizable(False, False)
    popup.protocol("WM_DELETE_WINDOW", lambda: None)
    frame = ttk.Frame(popup, padding=12)
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, textvariable=app.gcode_load_var, anchor="w")
    label.pack(fill="x", padx=4, pady=(0, 8))
    bar = ttk.Progressbar(frame, length=320, mode="determinate")
    bar.pack(fill="x", padx=4)
    app._gcode_load_popup = popup
    app._gcode_load_popup_label = label
    app._gcode_load_popup_bar = bar
    center_window(popup, app)

def show_gcode_loading(app):
    app._ensure_gcode_loading_popup()
    popup = app._gcode_load_popup
    if popup is None:
        return
    try:
        if not popup.winfo_viewable():
            popup.deiconify()
        popup.lift()
    except Exception:
        pass

def hide_gcode_loading(app):
    popup = app._gcode_load_popup
    if popup is not None:
        try:
            if app._gcode_load_popup_bar is not None:
                app._gcode_load_popup_bar.stop()
            popup.withdraw()
        except Exception:
            pass
    app.gcode_load_var.set("")

def set_gcode_loading_indeterminate(app, text: str):
    app._show_gcode_loading()
    app.gcode_load_var.set(text)
    if app._gcode_load_popup_bar is not None:
        app._gcode_load_popup_bar.config(mode="indeterminate")
        app._gcode_load_popup_bar.start(10)

def set_gcode_loading_progress(app, done: int, total: int, name: str = ""):
    app._show_gcode_loading()
    if app._gcode_load_popup_bar is not None:
        app._gcode_load_popup_bar.stop()
    display_total = int(total)
    bar_total = max(1, display_total)
    done = max(0, min(int(done), bar_total))
    display_done = min(int(done), display_total) if display_total > 0 else 0
    if app._gcode_load_popup_bar is not None:
        app._gcode_load_popup_bar.config(mode="determinate", maximum=bar_total, value=done)
    if name:
        app.gcode_load_var.set(f"Loading {name}: {display_done}/{display_total}")
    else:
        app.gcode_load_var.set(f"Loading {display_done}/{display_total}")

def finish_gcode_loading(app):
    app._hide_gcode_loading()
