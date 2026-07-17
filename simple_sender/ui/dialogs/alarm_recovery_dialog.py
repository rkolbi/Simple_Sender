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

from simple_sender.ui.alarm_state import (
    alarm_recovery_evidence_lines,
    alarm_recovery_guidance_text,
)
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window

logger = logging.getLogger(__name__)
_ALARM_RECOVERY_WRAPLENGTH = 460
_ALARM_RECOVERY_SECTION_PAD_Y = (0, 8)
_ALARM_RECOVERY_BUTTON_PAD_X = (0, 6)

def show_alarm_recovery(app) -> None:
    """Open the alarm recovery dialog when an alarm lock is active."""

    recovery_checker = getattr(app.grbl, "recovery_required", None)
    if callable(recovery_checker):
        try:
            recovery_required = bool(recovery_checker())
        except Exception:
            recovery_required = False
        if recovery_required:
            from simple_sender.ui.dialogs.execution_recovery_dialog import (
                show_execution_recovery,
            )

            show_execution_recovery(app)
            return

    normal_checker = getattr(app.grbl, "normal_session_initialization_required", None)
    if callable(normal_checker):
        try:
            normal_required = bool(normal_checker())
        except Exception:
            normal_required = False
        if normal_required:
            from simple_sender.ui.dialogs.normal_session_initialization_dialog import (
                show_normal_session_initialization,
            )

            show_normal_session_initialization(app)
            return

    if not app._alarm_locked:
        messagebox.showinfo("Alarm recovery", "No active alarm.")
        return
    msg = app._format_alarm_message(app._alarm_message)
    dlg = tk.Toplevel(app)
    dlg.title("Alarm recovery")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    apply_toplevel_theme(dlg, app)
    frm = ttk.Frame(dlg, padding=12)
    frm.pack(fill="both", expand=True)
    ttk.Label(
        frm,
        text=msg,
        wraplength=_ALARM_RECOVERY_WRAPLENGTH,
        justify="left",
    ).pack(fill="x", pady=_ALARM_RECOVERY_SECTION_PAD_Y)
    extra_lines = alarm_recovery_evidence_lines(
        getattr(app, "_last_status_raw", ""),
        getattr(app, "_last_status_pins", None),
    )
    if extra_lines:
        ttk.Label(
            frm,
            text="Controller evidence:\n" + "\n".join(extra_lines),
            wraplength=_ALARM_RECOVERY_WRAPLENGTH,
            justify="left",
        ).pack(fill="x", pady=_ALARM_RECOVERY_SECTION_PAD_Y)
    ttk.Label(
        frm,
        text=alarm_recovery_guidance_text(getattr(app, "_alarm_message", "")),
        wraplength=_ALARM_RECOVERY_WRAPLENGTH,
        justify="left",
        ).pack(fill="x", pady=(0, 10))
    btn_row = ttk.Frame(frm)
    btn_row.pack(fill="x")

    def run_and_close(action, *, action_label: str) -> None:
        if not app._require_grbl_connection():
            return
        try:
            accepted = action()
        except Exception:
            logger.exception("Alarm recovery action %r failed", action_label)
            return
        if accepted is False:
            messagebox.showwarning(
                "Alarm recovery",
                f"{action_label} did not start. The alarm is still active.",
            )
            return
        try:
            dlg.destroy()
        except Exception:
            logger.exception(
                "Failed to close alarm recovery dialog after action %r",
                action_label,
            )

    def _reset_with_accessories_off() -> bool:
        from simple_sender.ui.job_setup_state import invalidate_job_setup_state

        try:
            if hasattr(app, "_stop_job_accessories"):
                app._stop_job_accessories("job_reset")
        except Exception:
            logger.exception("Failed stopping job accessories before alarm reset")
        accepted = bool(app.grbl.reset())
        if not accepted:
            return False
        try:
            app._machine_coordinates_trusted = False
        except Exception:
            logger.exception("Failed marking machine-coordinate trust unknown after alarm reset")
        invalidate_job_setup_state(app)
        return True

    ttk.Button(
        btn_row,
        text="Unlock ($X)",
        command=lambda: run_and_close(app.grbl.unlock, action_label="Unlock ($X)"),
    ).pack(
        side="left",
        padx=_ALARM_RECOVERY_BUTTON_PAD_X,
    )
    ttk.Button(
        btn_row,
        text="Home ($H)",
        command=lambda: run_and_close(app._start_homing, action_label="Home ($H)"),
    ).pack(
        side="left",
        padx=_ALARM_RECOVERY_BUTTON_PAD_X,
    )
    ttk.Button(
        btn_row,
        text="Reset",
        command=lambda: run_and_close(_reset_with_accessories_off, action_label="Reset"),
    ).pack(
        side="left",
        padx=_ALARM_RECOVERY_BUTTON_PAD_X,
    )
    ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side="left")
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    center_window(dlg, app)
