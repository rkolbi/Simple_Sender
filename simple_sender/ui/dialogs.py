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

import logging
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from simple_sender.ui.widgets import set_kb_id
from simple_sender.ui.popup_utils import center_window

logger = logging.getLogger(__name__)


def show_resume_dialog(app):
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before resuming from a line.")
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
    if app._last_acked_index >= 0:
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

    def use_last_acked():
        if app._last_acked_index >= 0:
            line_var.set(str(min(total_lines, app._last_acked_index + 2)))
            update_preview()

    ttk.Button(frm, text="Use last acked", command=use_last_acked).grid(
        row=0, column=2, sticky="w", padx=(8, 0), pady=4
    )
    sync_var = tk.BooleanVar(value=True)
    sync_chk = ttk.Checkbutton(frm, text="Send modal re-sync before resuming", variable=sync_var)
    sync_chk.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))
    preview_var = tk.StringVar(value="")
    warning_var = tk.StringVar(value="")
    preview_lbl = ttk.Label(frm, textvariable=preview_var, wraplength=460, justify="left")
    preview_lbl.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 2))
    warning_lbl = ttk.Label(frm, textvariable=warning_var, foreground="#b00020", wraplength=460, justify="left")
    warning_lbl.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 8))

    def update_preview():
        try:
            line_no = int(line_var.get())
        except Exception:
            preview_var.set("Enter a valid line number.")
            warning_var.set("")
            return
        if line_no < 1 or line_no > total_lines:
            preview_var.set("Line number is out of range.")
            warning_var.set("")
            return
        preamble, has_g92 = app._build_resume_preamble(app._last_gcode_lines, line_no - 1)
        if sync_var.get():
            if preamble:
                preview_var.set("Modal re-sync: " + " ".join(preamble))
            else:
                preview_var.set("Modal re-sync: (none)")
        else:
            preview_var.set("Modal re-sync: disabled")
        if has_g92:
            warning_var.set(
                "Warning: G92 offsets appear before this line. Confirm work zero before resuming."
            )
        else:
            warning_var.set("")

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
            preamble, _ = app._build_resume_preamble(app._last_gcode_lines, line_no - 1)
        app._resume_from_line(line_no - 1, preamble)
        dlg.destroy()

    update_preview()
    line_entry.bind("<KeyRelease>", lambda _evt: update_preview())
    sync_chk.config(command=update_preview)

    btn_row = ttk.Frame(frm)
    btn_row.grid(row=4, column=0, columnspan=3, sticky="w")
    ttk.Button(btn_row, text="Start Resume", command=on_start).pack(side="left", padx=(0, 6))
    ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="left")
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    center_window(dlg, app)



def show_alarm_recovery(app):
    if not app._alarm_locked:
        messagebox.showinfo("Alarm recovery", "No active alarm.")
        return
    msg = app._format_alarm_message(app._alarm_message)
    dlg = tk.Toplevel(app)
    dlg.title("Alarm recovery")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=msg, wraplength=460, justify="left").pack(fill="x", pady=(0, 8))
    ttk.Label(
        frm,
        text="Suggested steps: Unlock ($X) to clear the alarm, then Home ($H) if required. "
        "If motion feels unsafe, use Reset (Ctrl-X).",
        wraplength=460,
        justify="left",
    ).pack(fill="x", pady=(0, 10))
    btn_row = ttk.Frame(frm)
    btn_row.pack(fill="x")

    def run_and_close(action):
        if not app._require_grbl_connection():
            return
        try:
            action()
        except Exception as exc:
            logger.exception("Alarm recovery action failed: %s", exc)
        try:
            dlg.destroy()
        except Exception as exc:
            logger.exception("Failed to close alarm recovery dialog: %s", exc)

    ttk.Button(btn_row, text="Unlock ($X)", command=lambda: run_and_close(app.grbl.unlock)).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(btn_row, text="Home ($H)", command=lambda: run_and_close(app._start_homing)).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(btn_row, text="Reset", command=lambda: run_and_close(app.grbl.reset)).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side="left")
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    center_window(dlg, app)


def show_macro_prompt(
    app,
    title: str,
    message: str,
    choices: list[str],
    cancel_label: str,
    result_q: queue.Queue,
) -> None:
    try:
        dlg = tk.Toplevel(app)
        dlg.title(title)
        dlg.transient(app)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        lbl = ttk.Label(frm, text=message, wraplength=460, justify="left")
        lbl.pack(fill="x", pady=(0, 10))
        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x")

        def choose(label: str):
            if result_q.empty():
                result_q.put(label)
            try:
                dlg.destroy()
            except Exception:
                pass

        for idx, lbl_text in enumerate(choices):
            b = ttk.Button(btn_row, text=lbl_text, command=lambda t=lbl_text: choose(t))
            set_kb_id(b, f"macro_prompt_{idx}")
            b.pack(side="left", padx=(0, 6))

        def on_close():
            choose(cancel_label)

        dlg.protocol("WM_DELETE_WINDOW", on_close)
        center_window(dlg, app)
    except Exception as exc:
        try:
            app.streaming_controller.log(f"[macro] Prompt failed: {exc}")
        except Exception:
            pass
        if result_q.empty():
            result_q.put(cancel_label)
