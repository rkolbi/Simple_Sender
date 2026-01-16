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
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time
from tkinter import messagebox

def install_dialog_loggers(app):
    orig_error = messagebox.showerror

    def _showerror(title, message, **kwargs):
        try:
            app.streaming_controller.handle_log(f"[dialog] {title}: {message}")
        except Exception:
            pass
        return orig_error(title, message, **kwargs)

    messagebox.showerror = _showerror

def toggle_error_dialogs(app):
    app.error_dialogs_enabled.set(not bool(app.error_dialogs_enabled.get()))
    app._on_error_dialogs_enabled_change()

def on_error_dialogs_enabled_change(app):
    enabled = bool(app.error_dialogs_enabled.get())
    if enabled:
        app._reset_error_dialog_state()
    else:
        app._set_error_dialog_status("Dialogs: Off")

def should_show_error_dialog(app) -> bool:
    if not bool(app.error_dialogs_enabled.get()):
        return False
    if app._closing:
        return False
    if app._error_dialog_suppressed:
        return False
    now = time.monotonic()
    if (now - app._error_dialog_last_ts) < app._error_dialog_interval:
        return False
    if (now - app._error_dialog_window_start) > app._error_dialog_burst_window:
        app._error_dialog_window_start = now
        app._error_dialog_count = 0
    app._error_dialog_count += 1
    app._error_dialog_last_ts = now
    if app._error_dialog_count > app._error_dialog_burst_limit:
        app._error_dialog_suppressed = True
        msg = "[error] Too many errors; suppressing dialogs for this session."
        try:
            if threading.current_thread() is threading.main_thread():
                app.streaming_controller.handle_log(msg)
            else:
                app.ui_q.put(("log", msg))
        except Exception:
            pass
        app._set_error_dialog_status("Dialogs: Suppressed")
        return False
    return True

def reset_error_dialog_state(app):
    app._error_dialog_last_ts = 0.0
    app._error_dialog_window_start = 0.0
    app._error_dialog_count = 0
    app._error_dialog_suppressed = False
    app._set_error_dialog_status("")

def set_error_dialog_status(app, text: str):
    def update():
        if hasattr(app, "error_dialog_status_var"):
            app.error_dialog_status_var.set(text)
    if threading.current_thread() is threading.main_thread():
        update()
    else:
        app._post_ui_thread(update)


def apply_error_dialog_settings(app, _event=None):
    def coerce_float(var, fallback):
        try:
            value = float(var.get())
        except Exception:
            value = fallback
        if value <= 0:
            value = fallback
        return value

    def coerce_int(var, fallback):
        try:
            value = int(var.get())
        except Exception:
            value = fallback
        if value <= 0:
            value = fallback
        return value

    interval = coerce_float(app.error_dialog_interval_var, app._error_dialog_interval)
    burst_window = coerce_float(app.error_dialog_burst_window_var, app._error_dialog_burst_window)
    burst_limit = coerce_int(app.error_dialog_burst_limit_var, app._error_dialog_burst_limit)
    app._error_dialog_interval = interval
    app._error_dialog_burst_window = burst_window
    app._error_dialog_burst_limit = burst_limit
    app.error_dialog_interval_var.set(interval)
    app.error_dialog_burst_window_var.set(burst_window)
    app.error_dialog_burst_limit_var.set(burst_limit)
    app._reset_error_dialog_state()
