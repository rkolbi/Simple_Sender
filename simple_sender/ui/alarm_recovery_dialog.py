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
import tkinter as tk
from tkinter import ttk, messagebox

from simple_sender.ui.popup_utils import center_window

logger = logging.getLogger(__name__)


def show_alarm_recovery(app) -> None:
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
    extra_lines = []
    last_status = getattr(app, "_last_status_raw", "") or ""
    if last_status:
        extra_lines.append(f"Last status: {last_status.strip()}")
    pins = getattr(app, "_last_status_pins", None)
    if pins:
        extra_lines.append(f"Pins: {pins}")
    if extra_lines:
        ttk.Label(
            frm,
            text="\n".join(extra_lines),
            wraplength=460,
            justify="left",
        ).pack(fill="x", pady=(0, 8))
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
