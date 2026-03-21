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
import threading
from tkinter import ttk, messagebox

from simple_sender.constants.messages import BusyMessages, DialogTitles
from .alarm_recovery_dialog import show_alarm_recovery
from .macro_prompt_dialog import show_macro_prompt
from .spoilboard_generator import show_spoilboard_generator_dialog
from .popup_utils import center_window
from simple_sender.ui.widgets_keypad import attach_numeric_keypad
from simple_sender.ui.autolevel_dialog.dialog_controller import show_auto_level_dialog

__all__ = [
    "show_alarm_recovery",
    "show_macro_prompt",
    "show_spoilboard_generator_dialog",
    "show_auto_level_dialog",
    "show_resume_dialog",
]


def show_resume_dialog(app):
    if app.grbl.is_streaming():
        messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_RESUMING_FROM_LINE,
        )
        return
    if not app._require_grbl_connection():
        return
    if not app._grbl_ready:
        messagebox.showwarning("Not ready", "Wait for GRBL to be ready.")
        return
    if app._alarm_locked:
        messagebox.showwarning("Alarm", "Clear the alarm before resuming.")
        return
    total_lines = (
        app._gcode_total_lines
        if getattr(app, "_gcode_streaming_mode", False)
        else len(app._last_gcode_lines)
    )
    if total_lines <= 0:
        messagebox.showwarning("No G-code", "Load a G-code file first.")
        return
    default_line = 1
    last_error_index = getattr(app, "_last_error_index", -1)
    if last_error_index >= 0:
        default_line = min(total_lines, last_error_index + 1)
    elif app._last_acked_index >= 0:
        default_line = min(total_lines, app._last_acked_index + 2)

    dlg = tk.Toplevel(app)
    dlg.title("Resume from line")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"Line number (1-{total_lines})").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    line_var = tk.StringVar(value=str(default_line))
    line_entry = ttk.Entry(frm, textvariable=line_var, width=10)
    line_entry.grid(row=0, column=1, sticky="w", pady=4)
    attach_numeric_keypad(line_entry, allow_decimal=False)

    def use_last_acked():
        if app._last_acked_index >= 0:
            line_var.set(str(min(total_lines, app._last_acked_index + 2)))
            update_sample()

    ttk.Button(frm, text="Use last acked", command=use_last_acked).grid(
        row=0, column=2, sticky="w", padx=(8, 0), pady=4
    )
    sync_var = tk.BooleanVar(value=True)
    sync_chk = ttk.Checkbutton(frm, text="Send modal re-sync before resuming", variable=sync_var)
    sync_chk.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))
    sample_var = tk.StringVar(value="")
    warning_var = tk.StringVar(value="")
    sample_lbl = ttk.Label(frm, textvariable=sample_var, wraplength=460, justify="left")
    sample_lbl.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 2))
    warning_lbl = ttk.Label(
        frm, textvariable=warning_var, foreground="#b00020", wraplength=460, justify="left"
    )
    warning_lbl.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 8))
    preview_cache: dict[int, tuple[list[str], bool]] = {}
    preview_after_id: dict[str, str | None] = {"value": None}
    preview_seq = {"value": 0}

    def _post_ui(func, *args, **kwargs) -> None:
        poster = getattr(app, "_post_ui_thread", None)
        if callable(poster):
            try:
                poster(func, *args, **kwargs)
                return
            except Exception:
                pass
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("ui_post", func, args, kwargs))
                return
            except Exception:
                pass

    def _render_preview(line_no: int, preamble: list[str], has_g92: bool) -> None:
        preview_cache[int(line_no)] = (list(preamble), bool(has_g92))
        try:
            current_line = int(line_var.get())
        except Exception:
            return
        if current_line != int(line_no):
            return
        if sync_var.get():
            if preamble:
                sample_var.set("Modal re-sync: " + " ".join(preamble))
            else:
                sample_var.set("Modal re-sync: (none)")
        else:
            sample_var.set("Modal re-sync: disabled")
        if has_g92:
            warning_var.set(
                "Warning: G92 offsets appear before this line. Confirm work zero before resuming."
            )
        else:
            warning_var.set("")

    def _schedule_preview(line_no: int) -> None:
        if preview_after_id["value"] is not None:
            try:
                dlg.after_cancel(preview_after_id["value"])
            except Exception:
                pass
            preview_after_id["value"] = None
        preview_seq["value"] = int(preview_seq["value"]) + 1
        request_seq = int(preview_seq["value"])

        def _start_worker() -> None:
            preview_after_id["value"] = None
            cached = preview_cache.get(int(line_no))
            if cached is not None:
                _render_preview(int(line_no), cached[0], cached[1])
                return

            def worker() -> None:
                preamble, has_g92 = app._build_resume_preamble(
                    app._last_gcode_lines, int(line_no) - 1
                )

                def apply_preview() -> None:
                    if not bool(dlg.winfo_exists()):
                        return
                    if int(preview_seq["value"]) != int(request_seq):
                        preview_cache[int(line_no)] = (list(preamble), bool(has_g92))
                        return
                    _render_preview(int(line_no), preamble, has_g92)

                _post_ui(apply_preview)

            threading.Thread(target=worker, daemon=True).start()

        preview_after_id["value"] = dlg.after(90, _start_worker)

    def update_sample():
        try:
            line_no = int(line_var.get())
        except Exception:
            sample_var.set("Enter a valid line number.")
            warning_var.set("")
            return
        if line_no < 1 or line_no > total_lines:
            sample_var.set("Line number is out of range.")
            warning_var.set("")
            return
        cached = preview_cache.get(int(line_no))
        if cached is not None:
            _render_preview(int(line_no), cached[0], cached[1])
            return
        if sync_var.get():
            sample_var.set("Modal re-sync: calculating...")
        else:
            sample_var.set("Modal re-sync: disabled")
        warning_var.set("Checking modal-state warnings...")
        _schedule_preview(int(line_no))

    def on_start():
        try:
            line_no = int(line_var.get())
        except Exception:
            messagebox.showwarning("Resume", "Enter a valid line number.")
            return
        if line_no < 1 or line_no > total_lines:
            messagebox.showwarning("Resume", "Line number is out of range.")
            return
        preamble = []
        if sync_var.get():
            cached = preview_cache.get(int(line_no))
            if cached is not None:
                preamble = list(cached[0])
            else:
                preamble, _ = app._build_resume_preamble(app._last_gcode_lines, line_no - 1)
        app._resume_from_line(line_no - 1, preamble)
        dlg.destroy()

    update_sample()
    line_entry.bind("<KeyRelease>", lambda _evt: update_sample())
    sync_chk.config(command=update_sample)

    btn_row = ttk.Frame(frm)
    btn_row.grid(row=4, column=0, columnspan=3, sticky="w")
    ttk.Button(btn_row, text="Start Resume", command=on_start).pack(side="left", padx=(0, 6))
    ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="left")
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    def _on_destroy(_event=None):
        if preview_after_id["value"] is not None:
            try:
                dlg.after_cancel(preview_after_id["value"])
            except Exception:
                pass
            preview_after_id["value"] = None
    dlg.bind("<Destroy>", _on_destroy, add="+")
    center_window(dlg, app)
