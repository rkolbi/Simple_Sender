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

import threading
import time
from typing import Any, Callable, cast
import tkinter as tk
from tkinter import messagebox, ttk

from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.utils.grbl_errors import extract_grbl_code


class ErrorDialogManager:
    def __init__(self, app) -> None:
        self.app = app
        self.interval = float(getattr(app, "_error_dialog_interval", 2.0) or 2.0)
        self.burst_window = float(getattr(app, "_error_dialog_burst_window", 30.0) or 30.0)
        self.burst_limit = int(getattr(app, "_error_dialog_burst_limit", 3) or 3)
        self.last_ts = float(getattr(app, "_error_dialog_last_ts", 0.0) or 0.0)
        self.window_start = float(getattr(app, "_error_dialog_window_start", 0.0) or 0.0)
        self.count = int(getattr(app, "_error_dialog_count", 0) or 0)
        self.suppressed = bool(getattr(app, "_error_dialog_suppressed", False))
        self._sync_to_app()

    def _sync_to_app(self) -> None:
        self.app._error_dialog_interval = self.interval
        self.app._error_dialog_burst_window = self.burst_window
        self.app._error_dialog_burst_limit = self.burst_limit
        self.app._error_dialog_last_ts = self.last_ts
        self.app._error_dialog_window_start = self.window_start
        self.app._error_dialog_count = self.count
        self.app._error_dialog_suppressed = self.suppressed

    def set_status(self, text: str) -> None:
        def update():
            if hasattr(self.app, "error_dialog_status_var"):
                self.app.error_dialog_status_var.set(text)

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self.app._post_ui_thread(update)

    def reset_state(self) -> None:
        self.last_ts = 0.0
        self.window_start = 0.0
        self.count = 0
        self.suppressed = False
        self._sync_to_app()
        self.set_status("")

    def should_show_dialog(self) -> bool:
        if not bool(self.app.error_dialogs_enabled.get()):
            return False
        if self.app._closing:
            return False
        if self.suppressed:
            return False
        now = time.monotonic()
        if (now - self.last_ts) < self.interval:
            return False
        if (now - self.window_start) > self.burst_window:
            self.window_start = now
            self.count = 0
        self.count += 1
        self.last_ts = now
        if self.count > self.burst_limit:
            self.suppressed = True
            msg = "[error] Too many errors; suppressing dialogs for this session."
            try:
                if threading.current_thread() is threading.main_thread():
                    self.app.streaming_controller.handle_log(msg)
                else:
                    self.app.ui_q.put(("log", msg))
            except Exception:
                pass
            self._sync_to_app()
            self.set_status("Dialogs: Suppressed")
            return False
        self._sync_to_app()
        return True

    def apply_settings(self, _event=None) -> None:
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

        def coerce_non_negative_float(var, fallback):
            try:
                value = float(var.get())
            except Exception:
                value = fallback
            if value < 0:
                value = fallback
            return value

        self.interval = coerce_float(
            self.app.error_dialog_interval_var,
            self.interval,
        )
        self.burst_window = coerce_float(
            self.app.error_dialog_burst_window_var,
            self.burst_window,
        )
        self.burst_limit = coerce_int(
            self.app.error_dialog_burst_limit_var,
            self.burst_limit,
        )
        self.app.error_dialog_interval_var.set(self.interval)
        self.app.error_dialog_burst_window_var.set(self.burst_window)
        self.app.error_dialog_burst_limit_var.set(self.burst_limit)
        if hasattr(self.app, "grbl_popup_auto_dismiss_sec"):
            dismiss = coerce_non_negative_float(
                self.app.grbl_popup_auto_dismiss_sec,
                getattr(self.app, "settings", {}).get("grbl_popup_auto_dismiss_sec", 12.0),
            )
            self.app.grbl_popup_auto_dismiss_sec.set(dismiss)
        if hasattr(self.app, "grbl_popup_dedupe_sec"):
            dedupe = coerce_non_negative_float(
                self.app.grbl_popup_dedupe_sec,
                getattr(self.app, "settings", {}).get("grbl_popup_dedupe_sec", 3.0),
            )
            self.app.grbl_popup_dedupe_sec.set(dedupe)
        if hasattr(self.app, "grbl_popup_enabled") and not bool(self.app.grbl_popup_enabled.get()):
            _close_grbl_code_popup(self.app)
        self.reset_state()


def _get_error_dialog_manager(app) -> ErrorDialogManager:
    manager = getattr(app, "_error_dialog_manager", None)
    if isinstance(manager, ErrorDialogManager):
        return manager
    manager = ErrorDialogManager(app)
    app._error_dialog_manager = manager
    return manager


def install_dialog_loggers(app):
    orig_error = messagebox.showerror

    def _showerror(title, message, **kwargs):
        try:
            app.streaming_controller.handle_log(f"[dialog] {title}: {message}")
        except Exception:
            pass
        return orig_error(title, message, **kwargs)

    messagebox.showerror = cast(Callable[..., Any], _showerror)

def toggle_error_dialogs(app):
    app.error_dialogs_enabled.set(not bool(app.error_dialogs_enabled.get()))
    app._on_error_dialogs_enabled_change()

def on_error_dialogs_enabled_change(app):
    enabled = bool(app.error_dialogs_enabled.get())
    if enabled:
        _get_error_dialog_manager(app).reset_state()
    else:
        _get_error_dialog_manager(app).set_status("Dialogs: Off")

def should_show_error_dialog(app) -> bool:
    return _get_error_dialog_manager(app).should_show_dialog()

def reset_error_dialog_state(app):
    _get_error_dialog_manager(app).reset_state()

def set_error_dialog_status(app, text: str):
    _get_error_dialog_manager(app).set_status(text)


def apply_error_dialog_settings(app, _event=None):
    _get_error_dialog_manager(app).apply_settings(_event)


def _close_grbl_code_popup(app) -> None:
    after_id = getattr(app, "_grbl_code_popup_after_id", None)
    if after_id is not None:
        try:
            app.after_cancel(after_id)
        except Exception:
            pass
    app._grbl_code_popup_after_id = None
    popup = getattr(app, "_grbl_code_popup", None)
    try:
        if popup is not None and popup.winfo_exists():
            popup.destroy()
    except Exception:
        pass
    app._grbl_code_popup = None
    app._grbl_code_popup_vars = None


def close_grbl_code_popup(app) -> None:
    """Public helper for shutdown/cleanup paths."""
    _close_grbl_code_popup(app)


def _ensure_grbl_code_popup(app):
    popup = getattr(app, "_grbl_code_popup", None)
    popup_vars = getattr(app, "_grbl_code_popup_vars", None)
    try:
        if popup is not None and popup.winfo_exists() and isinstance(popup_vars, dict):
            return popup, popup_vars
    except Exception:
        pass

    popup = tk.Toplevel(app)
    popup.title("GRBL Alert")
    popup.transient(app)
    popup.resizable(False, False)
    popup.configure(padx=16, pady=12)

    title_var = tk.StringVar(master=popup, value="GRBL alarm/error detected")
    time_var = tk.StringVar(master=popup, value="")
    code_var = tk.StringVar(master=popup, value="")
    definition_var = tk.StringVar(master=popup, value="")

    ttk.Label(
        popup,
        textvariable=title_var,
        font=("TkDefaultFont", 10, "bold"),
        justify="left",
    ).pack(anchor="w")
    ttk.Label(popup, textvariable=time_var, justify="left").pack(anchor="w", pady=(8, 0))
    ttk.Label(popup, textvariable=code_var, justify="left").pack(anchor="w", pady=(2, 0))
    ttk.Label(
        popup,
        textvariable=definition_var,
        justify="left",
        wraplength=520,
    ).pack(anchor="w", pady=(2, 10))
    ttk.Button(popup, text="Dismiss", command=lambda: _close_grbl_code_popup(app)).pack(
        anchor="e"
    )

    def _on_close() -> None:
        _close_grbl_code_popup(app)

    popup.protocol("WM_DELETE_WINDOW", _on_close)
    popup_vars = {
        "title": title_var,
        "time": time_var,
        "code": code_var,
        "definition": definition_var,
    }
    app._grbl_code_popup = popup
    app._grbl_code_popup_vars = popup_vars
    center_window(popup, app)
    return popup, popup_vars


def _get_bool_setting(app, attr: str, default: bool) -> bool:
    var = getattr(app, attr, None)
    if var is None:
        return bool(default)
    try:
        return bool(var.get())
    except Exception:
        return bool(default)


def _get_non_negative_float_setting(app, attr: str, default: float) -> float:
    var = getattr(app, attr, None)
    try:
        value = float(var.get()) if var is not None else float(default)
    except Exception:
        value = float(default)
    if value < 0:
        return float(default)
    return value


def _schedule_grbl_popup_close(app) -> None:
    auto_close_s = _get_non_negative_float_setting(
        app,
        "grbl_popup_auto_dismiss_sec",
        12.0,
    )
    after_id = getattr(app, "_grbl_code_popup_after_id", None)
    if after_id is not None:
        try:
            app.after_cancel(after_id)
        except Exception:
            pass
    app._grbl_code_popup_after_id = None
    if auto_close_s <= 0:
        return
    if not hasattr(app, "after"):
        return
    try:
        app._grbl_code_popup_after_id = app.after(
            int(round(auto_close_s * 1000)),
            lambda: _close_grbl_code_popup(app),
        )
    except Exception:
        app._grbl_code_popup_after_id = None


def _maybe_log_grbl_popup_dedupe(app, code_token: str, dedupe_key: str, dedupe_s: float, now_mono: float) -> None:
    last_log_by_code = getattr(app, "_grbl_code_popup_last_suppressed_log_ts_by_code", None)
    if not isinstance(last_log_by_code, dict):
        last_log_by_code = {}
        app._grbl_code_popup_last_suppressed_log_ts_by_code = last_log_by_code
    last_log_ts = float(last_log_by_code.get(dedupe_key, 0.0) or 0.0)
    if dedupe_s > 0 and (now_mono - last_log_ts) < dedupe_s:
        return
    last_log_by_code[dedupe_key] = now_mono
    try:
        app.streaming_controller.handle_log(
            f"[popup] Suppressed duplicate GRBL popup for {code_token} (dedupe {dedupe_s:.1f}s)."
        )
    except Exception:
        return


def show_grbl_code_popup(app, message: str | None) -> None:
    """Show/update a non-blocking popup for known GRBL alarm/error codes."""
    if getattr(app, "_closing", False):
        return
    if not _get_bool_setting(app, "grbl_popup_enabled", True):
        return
    parsed = extract_grbl_code(str(message or ""))
    if not parsed:
        return
    kind, code, definition = parsed
    dedupe_s = _get_non_negative_float_setting(app, "grbl_popup_dedupe_sec", 3.0)
    dedupe_key = f"{kind}:{code}"
    code_token = f"{kind.upper()}:{code}"
    now_mono = time.monotonic()
    seen = getattr(app, "_grbl_code_popup_last_ts_by_code", None)
    if not isinstance(seen, dict):
        seen = {}
        app._grbl_code_popup_last_ts_by_code = seen
    last_ts = float(seen.get(dedupe_key, 0.0) or 0.0)
    if dedupe_s > 0 and (now_mono - last_ts) < dedupe_s:
        _maybe_log_grbl_popup_dedupe(app, code_token, dedupe_key, dedupe_s, now_mono)
        return
    seen[dedupe_key] = now_mono
    kind_label = "Alarm" if kind == "alarm" else "Error"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        popup, popup_vars = _ensure_grbl_code_popup(app)
        popup_vars["title"].set(f"GRBL {kind_label} detected")
        popup_vars["time"].set(f"Time of {kind_label.lower()}: {timestamp}")
        popup_vars["code"].set(f"Alarm/Error # {code} ({code_token})")
        popup_vars["definition"].set(f"Definition: {definition}")
        popup.title(f"GRBL {kind_label}")
        popup.deiconify()
        popup.lift()
        center_window(popup, app)
        _schedule_grbl_popup_close(app)
    except Exception:
        return
