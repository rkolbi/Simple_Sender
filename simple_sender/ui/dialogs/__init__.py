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
import tkinter as tk
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
    if app.grbl.is_streaming() or bool(getattr(app, "_stream_done_pending_idle", False)):
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
    sync_state = {"enabled": True}
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
    preview_cache: dict[int, tuple[list[str], bool, bool]] = {}
    preview_after_id: dict[str, str | None] = {"value": None}
    preview_seq = {"value": 0}
    pending_resume_line: dict[str, int | None] = {"value": None}
    start_btn_holder: dict[str, ttk.Button | None] = {"widget": None}

    def _restore_start_button() -> None:
        start_btn = start_btn_holder["widget"]
        if start_btn is None:
            return
        try:
            start_btn.config(state="normal")
        except Exception:
            pass

    def _clear_pending_resume_start() -> None:
        pending_resume_line["value"] = None
        _restore_start_button()

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

    def _sync_enabled() -> bool:
        return bool(sync_state["enabled"])

    def _sync_trace(*_args) -> None:
        try:
            sync_state["enabled"] = bool(sync_var.get())
        except Exception:
            sync_state["enabled"] = True

    sync_var.trace_add("write", _sync_trace)

    def _render_preview(
        line_no: int,
        preamble: list[str],
        has_g92: bool,
        unsupported_dynamic_tlo: bool = False,
    ) -> None:
        preview_cache[int(line_no)] = (
            list(preamble),
            bool(has_g92),
            bool(unsupported_dynamic_tlo),
        )
        try:
            current_line = int(line_var.get())
        except Exception:
            return
        if current_line != int(line_no):
            return
        if _sync_enabled():
            if preamble:
                sample_var.set("Modal re-sync: " + " ".join(preamble))
            else:
                sample_var.set("Modal re-sync: (none)")
        else:
            sample_var.set("Modal re-sync: disabled")
        if unsupported_dynamic_tlo:
            warning_var.set(
                "Resume blocked: G43.1 tool length offset state before this line cannot be reconstructed safely."
            )
        elif has_g92:
            warning_var.set(
                "Warning: G92 offsets appear before this line. Confirm work zero before resuming."
            )
        else:
            warning_var.set("")
        if pending_resume_line["value"] == int(line_no):
            pending_resume_line["value"] = None
            _restore_start_button()
            current_sync_enabled = _sync_enabled()
            resume_preamble = list(preamble) if current_sync_enabled else []
            app._resume_from_line(
                int(line_no) - 1,
                resume_preamble,
                has_g92=bool(has_g92),
                unsupported_dynamic_tlo=bool(unsupported_dynamic_tlo),
            )
            if bool(dlg.winfo_exists()):
                dlg.destroy()

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
                _render_preview(int(line_no), cached[0], cached[1], cached[2])
                return

            def worker() -> None:
                try:
                    result = app._build_resume_preamble(
                        app._last_gcode_lines, int(line_no) - 1
                    )
                    if len(result) >= 3:
                        preamble, has_g92, unsupported_dynamic_tlo = result[:3]
                    else:
                        preamble, has_g92 = result
                        unsupported_dynamic_tlo = False
                except Exception as exc:
                    error_text = str(exc)

                    def apply_preview_error() -> None:
                        if not bool(dlg.winfo_exists()):
                            return
                        if int(preview_seq["value"]) != int(request_seq):
                            return
                        _clear_pending_resume_start()
                        if _sync_enabled():
                            sample_var.set("Modal re-sync: failed.")
                        else:
                            sample_var.set("Modal re-sync: disabled")
                        warning_var.set("Resume preamble preview failed. Adjust settings and try again.")
                        messagebox.showwarning(
                            "Resume preview failed",
                            f"Failed to build resume preamble:\n{error_text}",
                        )

                    _post_ui(apply_preview_error)
                    return

                def apply_preview() -> None:
                    if not bool(dlg.winfo_exists()):
                        return
                    if int(preview_seq["value"]) != int(request_seq):
                        preview_cache[int(line_no)] = (
                            list(preamble),
                            bool(has_g92),
                            bool(unsupported_dynamic_tlo),
                        )
                        return
                    _render_preview(
                        int(line_no),
                        preamble,
                        has_g92,
                        unsupported_dynamic_tlo,
                    )

                _post_ui(apply_preview)

            threading.Thread(target=worker, daemon=True).start()

        preview_after_id["value"] = dlg.after(90, _start_worker)

    def update_sample():
        pending_line = pending_resume_line["value"]
        try:
            line_no = int(line_var.get())
        except Exception:
            _clear_pending_resume_start()
            sample_var.set("Enter a valid line number.")
            warning_var.set("")
            return
        if line_no < 1 or line_no > total_lines:
            _clear_pending_resume_start()
            sample_var.set("Line number is out of range.")
            warning_var.set("")
            return
        if pending_line is not None and int(pending_line) != int(line_no):
            _clear_pending_resume_start()
        cached = preview_cache.get(int(line_no))
        if cached is not None:
            _render_preview(int(line_no), cached[0], cached[1], cached[2])
            return
        current_sync_enabled = _sync_enabled()
        if pending_line is not None and not current_sync_enabled:
            sample_var.set("Modal re-sync: disabled")
            warning_var.set("")
            return
        if current_sync_enabled:
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
        has_g92 = False
        unsupported_dynamic_tlo = False
        cached = preview_cache.get(int(line_no))
        if cached is not None:
            if _sync_enabled():
                preamble = list(cached[0])
            has_g92 = bool(cached[1])
            unsupported_dynamic_tlo = bool(cached[2])
        else:
            pending_resume_line["value"] = int(line_no)
            start_btn = start_btn_holder["widget"]
            if start_btn is not None:
                try:
                    start_btn.config(state="disabled")
                except Exception:
                    pass
            sample_var.set("Modal re-sync: calculating...")
            warning_var.set("Resume will start after the safety checks are ready.")
            _schedule_preview(int(line_no))
            return
        app._resume_from_line(
            line_no - 1,
            preamble,
            has_g92=has_g92,
            unsupported_dynamic_tlo=unsupported_dynamic_tlo,
        )
        dlg.destroy()

    def _on_sync_toggle() -> None:
        _sync_trace()
        update_sample()

    update_sample()
    line_entry.bind("<KeyRelease>", lambda _evt: update_sample())
    sync_chk.config(command=_on_sync_toggle)

    btn_row = ttk.Frame(frm)
    btn_row.grid(row=4, column=0, columnspan=3, sticky="w")
    start_btn = ttk.Button(btn_row, text="Start Resume", command=on_start)
    start_btn.pack(side="left", padx=(0, 6))
    start_btn_holder["widget"] = start_btn
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
