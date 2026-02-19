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

import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from simple_sender.utils.constants import ALL_STOP_CHOICES
from simple_sender.ui.widgets import apply_tooltip, attach_numeric_keypad

def build_diagnostics_section(app, parent: ttk.Frame, row: int) -> int:
    diagnostics_frame = ttk.LabelFrame(parent, text="Diagnostics", padding=8)
    diagnostics_frame.grid(row=row, column=0, sticky="ew", pady=(8, 0))
    diagnostics_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(diagnostics_frame, text="Preflight check").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.btn_preflight_check = ttk.Button(
        diagnostics_frame,
        text="Run check",
        command=app._run_preflight_check,
    )
    app.btn_preflight_check.grid(row=0, column=1, sticky="w", pady=4)
    apply_tooltip(
        app.btn_preflight_check,
        "Scan loaded G-code for bounds and validation warnings.",
    )
    ttk.Label(diagnostics_frame, text="Export session diagnostics").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.btn_export_diagnostics = ttk.Button(
        diagnostics_frame,
        text="Save report",
        command=app._export_session_diagnostics,
    )
    app.btn_export_diagnostics.grid(row=1, column=1, sticky="w", pady=4)
    apply_tooltip(
        app.btn_export_diagnostics,
        "Save recent console/status history and settings to a text file.",
    )
    ttk.Label(diagnostics_frame, text="Backup bundle").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=4
    )
    backup_row = ttk.Frame(diagnostics_frame)
    backup_row.grid(row=2, column=1, sticky="w", pady=4)
    app.btn_export_backup_bundle = ttk.Button(
        backup_row,
        text="Export bundle",
        command=app._export_backup_bundle,
    )
    app.btn_export_backup_bundle.pack(side="left")
    apply_tooltip(
        app.btn_export_backup_bundle,
        "Export settings, macro files, and checklists into one zip bundle.",
    )
    app.btn_import_backup_bundle = ttk.Button(
        backup_row,
        text="Import bundle",
        command=app._import_backup_bundle,
    )
    app.btn_import_backup_bundle.pack(side="left", padx=(8, 0))
    apply_tooltip(
        app.btn_import_backup_bundle,
        "Import settings and macro assets from a previously exported bundle.",
    )
    app.validate_streaming_check = ttk.Checkbutton(
        diagnostics_frame,
        text="Validate streaming (large) G-code files",
        variable=app.validate_streaming_gcode,
    )
    app.validate_streaming_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
    apply_tooltip(
        app.validate_streaming_check,
        "Validate large files while loading; adds an extra scan but improves preflight checks.",
    )
    ttk.Label(diagnostics_frame, text="Streaming line threshold").grid(
        row=4, column=0, sticky="w", padx=(0, 10), pady=(6, 0)
    )
    app.streaming_line_threshold_entry = ttk.Entry(
        diagnostics_frame,
        textvariable=app.streaming_line_threshold,
        width=10,
    )
    app.streaming_line_threshold_entry.grid(row=4, column=1, sticky="w", pady=(6, 0))
    attach_numeric_keypad(app.streaming_line_threshold_entry, allow_decimal=False)
    apply_tooltip(
        app.streaming_line_threshold_entry,
        "Cleaned line count that forces streaming mode (set to 0 to disable).",
    )
    return row + 1


def build_theme_section(app, parent: ttk.Frame, row: int) -> int:
    theme_frame = ttk.LabelFrame(parent, text="Theme", padding=8)
    theme_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    theme_frame.grid_columnconfigure(1, weight=1)
    if not hasattr(app, "ui_scale"):
        app.ui_scale = tk.DoubleVar(master=parent, value=1.0)
    if not hasattr(app, "scrollbar_width"):
        app.scrollbar_width = tk.StringVar(master=parent, value="wide")
    if not hasattr(app, "numeric_keypad_enabled"):
        app.numeric_keypad_enabled = tk.BooleanVar(master=parent, value=True)
    if not hasattr(app, "tooltip_enabled"):
        app.tooltip_enabled = tk.BooleanVar(master=parent, value=True)
    if not hasattr(app, "tooltip_timeout_sec"):
        app.tooltip_timeout_sec = tk.DoubleVar(master=parent, value=10.0)
    ttk.Label(theme_frame, text="UI theme").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
    app.theme_combo = ttk.Combobox(
        theme_frame,
        state="readonly",
        values=app.available_themes,
        textvariable=app.selected_theme,
        width=28,
    )
    app.theme_combo.grid(row=0, column=1, sticky="w", pady=4)
    on_theme_change = getattr(app, "_on_theme_change", lambda *_args, **_kwargs: None)
    app.theme_combo.bind("<<ComboboxSelected>>", on_theme_change)
    apply_tooltip(
        app.theme_combo,
        "Pick a ttk theme; some themes require a restart for best results.",
    )
    ttk.Label(theme_frame, text="UI scale").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
    ui_scale_row = ttk.Frame(theme_frame)
    ui_scale_row.grid(row=1, column=1, sticky="w", pady=4)
    app.ui_scale_entry = ttk.Entry(ui_scale_row, textvariable=app.ui_scale, width=10)
    app.ui_scale_entry.pack(side="left")
    attach_numeric_keypad(app.ui_scale_entry, allow_decimal=True)
    ttk.Label(ui_scale_row, text="(0.5 - 3.0)").pack(side="left", padx=(6, 0))
    on_ui_scale_change = getattr(app, "_on_ui_scale_change", lambda *_args, **_kwargs: None)
    app.ui_scale_entry.bind("<Return>", on_ui_scale_change)
    app.ui_scale_entry.bind("<FocusOut>", on_ui_scale_change)
    app.ui_scale_apply_btn = ttk.Button(ui_scale_row, text="Apply", command=on_ui_scale_change)
    app.ui_scale_apply_btn.pack(side="left", padx=(8, 0))
    def _apply_scale_preset(value: float) -> None:
        try:
            app.ui_scale.set(value)
        except Exception:
            pass
        on_ui_scale_change()
    apply_tooltip(
        app.ui_scale_entry,
        "Scale the UI; changes apply immediately.",
    )
    apply_tooltip(
        app.ui_scale_apply_btn,
        "Apply the UI scale immediately.",
    )
    ttk.Label(theme_frame, text="Scrollbar width").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
    app.scrollbar_width_combo = ttk.Combobox(
        theme_frame,
        state="readonly",
        values=["default", "wide", "wider", "widest"],
        textvariable=app.scrollbar_width,
        width=12,
    )
    app.scrollbar_width_combo.grid(row=2, column=1, sticky="w", pady=4)
    on_scrollbar_width_change = getattr(
        app,
        "_on_scrollbar_width_change",
        lambda *_args, **_kwargs: None,
    )
    app.scrollbar_width_combo.bind("<<ComboboxSelected>>", on_scrollbar_width_change)
    apply_tooltip(
        app.scrollbar_width_combo,
        "Set the width used for all scrollbars (wide matches the current App Settings size).",
    )
    def _sync_tooltip_timeout_state() -> None:
        try:
            enabled = bool(app.tooltip_enabled.get())
        except Exception:
            enabled = True
        state = "normal" if enabled else "disabled"
        try:
            app.tooltip_timeout_entry.configure(state=state)
        except Exception:
            pass

    def _on_tooltip_setting_change() -> None:
        refresh_tooltips = getattr(app, "_refresh_tooltips_toggle_text", None)
        if callable(refresh_tooltips):
            refresh_tooltips()
        _sync_tooltip_timeout_state()

    app.tooltips_enabled_check = ttk.Checkbutton(
        theme_frame,
        text="Enable tooltips",
        variable=app.tooltip_enabled,
        command=_on_tooltip_setting_change,
    )
    app.tooltips_enabled_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
    apply_tooltip(
        app.tooltips_enabled_check,
        "Show tooltips on hover (disabled controls include the reason).",
    )
    try:
        app.tooltip_enabled.trace_add("write", lambda *_args: _sync_tooltip_timeout_state())
    except Exception:
        pass

    ttk.Label(theme_frame, text="Tooltip display duration (sec)").grid(
        row=4, column=0, sticky="w", padx=(0, 10), pady=4
    )
    tooltip_timeout_row = ttk.Frame(theme_frame)
    tooltip_timeout_row.grid(row=4, column=1, sticky="w", pady=4)
    app.tooltip_timeout_entry = ttk.Entry(
        tooltip_timeout_row, textvariable=app.tooltip_timeout_sec, width=10
    )
    app.tooltip_timeout_entry.pack(side="left")
    attach_numeric_keypad(app.tooltip_timeout_entry, allow_decimal=True)
    ttk.Label(tooltip_timeout_row, text="(0 = no auto-hide)").pack(side="left", padx=(6, 0))
    apply_tooltip(
        app.tooltip_timeout_entry,
        "How long tooltips stay visible before hiding automatically (0 keeps them open).",
    )
    _sync_tooltip_timeout_state()
    app.numeric_keypad_check = ttk.Checkbutton(
        theme_frame,
        text="Enable numeric keypad popups (click numeric fields)",
        variable=app.numeric_keypad_enabled,
    )
    app.numeric_keypad_check.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
    apply_tooltip(
        app.numeric_keypad_check,
        "Show the touch keypad when tapping numeric fields.",
    )
    return row + 1


def build_safety_section(app, parent: ttk.Frame, row: int) -> int:
    safety = ttk.LabelFrame(parent, text="Safety", padding=8)
    safety.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    safety.grid_columnconfigure(1, weight=1)
    ttk.Label(safety, text="All Stop behavior").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
    app.all_stop_combo = ttk.Combobox(
        safety,
        state="readonly",
        values=[label for label, _ in ALL_STOP_CHOICES],
        width=32,
    )
    app.all_stop_combo.grid(row=0, column=1, sticky="w", pady=4)
    app.all_stop_combo.bind("<<ComboboxSelected>>", app._on_all_stop_mode_change)
    apply_tooltip(
        app.all_stop_combo,
        "Select how ALL STOP behaves: Soft Reset (Ctrl-X) immediately resets, Stop Stream + Reset halts sending first.",
    )
    app._sync_all_stop_mode_combo()
    app.all_stop_desc = ttk.Label(
        safety,
        text="Soft Reset (Ctrl-X) stops GRBL immediately. Stop Stream + Reset halts sending first, then resets.",
        wraplength=560,
        justify="left",
    )
    app.all_stop_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
    app.dry_run_sanitize_check = ttk.Checkbutton(
        safety,
        text="Dry run: disable spindle/coolant/tool changes while streaming",
        variable=app.dry_run_sanitize_stream,
    )
    app.dry_run_sanitize_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
    apply_tooltip(
        app.dry_run_sanitize_check,
        "Strip M3/M4/M5, M7/M8/M9, M6, S, and T words from streamed G-code for safe dry runs.",
    )
    app.homing_watchdog_check = ttk.Checkbutton(
        safety,
        text="Suspend watchdog during homing",
        variable=app.homing_watchdog_enabled,
        command=app._on_homing_watchdog_change,
    )
    app.homing_watchdog_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
    apply_tooltip(
        app.homing_watchdog_check,
        "Ignore watchdog timeouts while the homing cycle runs.",
    )
    ttk.Label(safety, text="Homing watchdog grace (seconds)").grid(
        row=4, column=0, sticky="w", padx=(0, 10), pady=(6, 0)
    )
    app.homing_watchdog_timeout_entry = ttk.Entry(
        safety,
        textvariable=app.homing_watchdog_timeout,
        width=12,
    )
    app.homing_watchdog_timeout_entry.grid(row=4, column=1, sticky="w", pady=(6, 0))
    attach_numeric_keypad(app.homing_watchdog_timeout_entry, allow_decimal=True)
    app.homing_watchdog_timeout_entry.bind("<Return>", app._on_homing_watchdog_change)
    app.homing_watchdog_timeout_entry.bind("<FocusOut>", app._on_homing_watchdog_change)
    apply_tooltip(
        app.homing_watchdog_timeout_entry,
        "Seconds to suspend the watchdog after issuing $H.",
    )
    app._on_homing_watchdog_change()
    return row + 1


def build_estimation_section(app, parent: ttk.Frame, row: int) -> int:
    estimation = ttk.LabelFrame(parent, text="Estimation", padding=8)
    estimation.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    estimation.grid_columnconfigure(1, weight=1)
    ttk.Label(estimation, text="Fallback rapid rate (mm/min)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.fallback_rapid_entry = ttk.Entry(estimation, textvariable=app.fallback_rapid_rate, width=12)
    app.fallback_rapid_entry.grid(row=0, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.fallback_rapid_entry, allow_decimal=True)
    app.fallback_rapid_entry.bind("<Return>", app._on_fallback_rate_change)
    app.fallback_rapid_entry.bind("<FocusOut>", app._on_fallback_rate_change)
    apply_tooltip(
        app.fallback_rapid_entry,
        "Used for time estimates when GRBL max rates ($110-$112) are not available.",
    )
    ttk.Label(estimation, text="Estimator adjustment").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.estimate_factor_scale = ttk.Scale(
        estimation,
        from_=0.5,
        to=2.0,
        orient="horizontal",
        variable=app.estimate_factor,
        command=app._on_estimate_factor_change,
    )
    app.estimate_factor_scale.grid(row=1, column=1, sticky="ew", pady=4)
    app.estimate_factor_value = ttk.Label(estimation, textvariable=app._estimate_factor_label)
    app.estimate_factor_value.grid(row=1, column=2, sticky="w", padx=(8, 0))
    apply_tooltip(
        app.estimate_factor_scale,
        "Scale time estimates up or down (1.00x = default).",
    )
    ttk.Label(estimation, text="Max rates (X/Y/Z)").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=4
    )
    rates_frame = ttk.Frame(estimation)
    rates_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
    validate_rate = (app.register(app._validate_estimate_rate_text), "%P")
    ttk.Label(rates_frame, text="X").pack(side="left")
    app.estimate_rate_x_entry = ttk.Entry(
        rates_frame,
        textvariable=app.estimate_rate_x_var,
        width=8,
        validate="key",
        validatecommand=validate_rate,
    )
    app.estimate_rate_x_entry.pack(side="left", padx=(4, 8))
    app.estimate_rate_x_units = ttk.Label(rates_frame, text="mm/min")
    app.estimate_rate_x_units.pack(side="left", padx=(0, 8))
    ttk.Label(rates_frame, text="Y").pack(side="left")
    app.estimate_rate_y_entry = ttk.Entry(
        rates_frame,
        textvariable=app.estimate_rate_y_var,
        width=8,
        validate="key",
        validatecommand=validate_rate,
    )
    app.estimate_rate_y_entry.pack(side="left", padx=(4, 8))
    app.estimate_rate_y_units = ttk.Label(rates_frame, text="mm/min")
    app.estimate_rate_y_units.pack(side="left", padx=(0, 8))
    ttk.Label(rates_frame, text="Z").pack(side="left")
    app.estimate_rate_z_entry = ttk.Entry(
        rates_frame,
        textvariable=app.estimate_rate_z_var,
        width=8,
        validate="key",
        validatecommand=validate_rate,
    )
    app.estimate_rate_z_entry.pack(side="left", padx=(4, 8))
    app.estimate_rate_z_units = ttk.Label(rates_frame, text="mm/min")
    app.estimate_rate_z_units.pack(side="left", padx=(0, 8))
    app.estimate_rate_x_entry.bind("<Return>", app._on_estimate_rates_change)
    app.estimate_rate_x_entry.bind("<FocusOut>", app._on_estimate_rates_change)
    app.estimate_rate_y_entry.bind("<Return>", app._on_estimate_rates_change)
    app.estimate_rate_y_entry.bind("<FocusOut>", app._on_estimate_rates_change)
    app.estimate_rate_z_entry.bind("<Return>", app._on_estimate_rates_change)
    app.estimate_rate_z_entry.bind("<FocusOut>", app._on_estimate_rates_change)
    attach_numeric_keypad(app.estimate_rate_x_entry, allow_decimal=True)
    attach_numeric_keypad(app.estimate_rate_y_entry, allow_decimal=True)
    attach_numeric_keypad(app.estimate_rate_z_entry, allow_decimal=True)
    apply_tooltip(
        app.estimate_rate_x_entry,
        "Set machine max rate for X (used in time estimates).",
    )
    apply_tooltip(
        app.estimate_rate_y_entry,
        "Set machine max rate for Y (used in time estimates).",
    )
    apply_tooltip(
        app.estimate_rate_z_entry,
        "Set machine max rate for Z (used in time estimates).",
    )
    app._update_estimate_rate_units_label()
    return row + 1


def build_status_polling_section(app, parent: ttk.Frame, row: int) -> int:
    status_frame = ttk.LabelFrame(parent, text="Status polling", padding=8)
    status_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    status_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(status_frame, text="Status report interval (seconds)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.status_poll_entry = ttk.Entry(
        status_frame, textvariable=app.status_poll_interval, width=12
    )
    app.status_poll_entry.grid(row=0, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.status_poll_entry, allow_decimal=True)
    app.status_poll_entry.bind("<Return>", app._on_status_interval_change)
    app.status_poll_entry.bind("<FocusOut>", app._on_status_interval_change)
    apply_tooltip(
        app.status_poll_entry,
        "Set how often GRBL status reports are requested (seconds).",
    )
    app._on_status_interval_change()
    ttk.Label(status_frame, text="Disconnect after failures").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    status_fail_row = ttk.Frame(status_frame)
    status_fail_row.grid(row=1, column=1, sticky="w", pady=4)
    app.status_fail_limit_entry = ttk.Entry(
        status_fail_row, textvariable=app.status_query_failure_limit, width=12
    )
    app.status_fail_limit_entry.pack(side="left")
    attach_numeric_keypad(app.status_fail_limit_entry, allow_decimal=False)
    ttk.Label(status_fail_row, text="(1-10)").pack(side="left", padx=(6, 0))
    app.status_fail_limit_entry.bind("<Return>", app._on_status_failure_limit_change)
    app.status_fail_limit_entry.bind("<FocusOut>", app._on_status_failure_limit_change)
    apply_tooltip(
        app.status_fail_limit_entry,
        "Consecutive status send failures before disconnecting (clamped to 1-10).",
    )
    return row + 1


def build_error_dialogs_section(app, parent: ttk.Frame, row: int) -> int:
    dialog_frame = ttk.LabelFrame(parent, text="Error dialogs", padding=8)
    dialog_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    dialog_frame.grid_columnconfigure(1, weight=1)
    app.error_dialogs_check = ttk.Checkbutton(
        dialog_frame,
        text="Enable error dialogs",
        variable=app.error_dialogs_enabled,
        command=app._on_error_dialogs_enabled_change,
    )
    app.error_dialogs_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
    apply_tooltip(app.error_dialogs_check, "Show modal dialogs for errors (tracebacks still log to console).")
    ttk.Label(dialog_frame, text="Minimum interval (seconds)").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    error_interval_row = ttk.Frame(dialog_frame)
    error_interval_row.grid(row=1, column=1, sticky="w", pady=4)
    app.error_dialog_interval_entry = ttk.Entry(
        error_interval_row, textvariable=app.error_dialog_interval_var, width=12
    )
    app.error_dialog_interval_entry.pack(side="left")
    attach_numeric_keypad(app.error_dialog_interval_entry, allow_decimal=True)
    ttk.Label(error_interval_row, text="sec").pack(side="left", padx=(6, 0))
    app.error_dialog_interval_entry.bind("<Return>", app._apply_error_dialog_settings)
    app.error_dialog_interval_entry.bind("<FocusOut>", app._apply_error_dialog_settings)
    ttk.Label(dialog_frame, text="Burst window (seconds)").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=4
    )
    error_window_row = ttk.Frame(dialog_frame)
    error_window_row.grid(row=2, column=1, sticky="w", pady=4)
    app.error_dialog_window_entry = ttk.Entry(
        error_window_row, textvariable=app.error_dialog_burst_window_var, width=12
    )
    app.error_dialog_window_entry.pack(side="left")
    attach_numeric_keypad(app.error_dialog_window_entry, allow_decimal=True)
    ttk.Label(error_window_row, text="sec").pack(side="left", padx=(6, 0))
    app.error_dialog_window_entry.bind("<Return>", app._apply_error_dialog_settings)
    app.error_dialog_window_entry.bind("<FocusOut>", app._apply_error_dialog_settings)
    ttk.Label(dialog_frame, text="Max dialogs per window").grid(
        row=3, column=0, sticky="w", padx=(0, 10), pady=4
    )
    error_limit_row = ttk.Frame(dialog_frame)
    error_limit_row.grid(row=3, column=1, sticky="w", pady=4)
    app.error_dialog_limit_entry = ttk.Entry(
        error_limit_row, textvariable=app.error_dialog_burst_limit_var, width=12
    )
    app.error_dialog_limit_entry.pack(side="left")
    attach_numeric_keypad(app.error_dialog_limit_entry, allow_decimal=False)
    ttk.Label(error_limit_row, text="count").pack(side="left", padx=(6, 0))
    app.error_dialog_limit_entry.bind("<Return>", app._apply_error_dialog_settings)
    app.error_dialog_limit_entry.bind("<FocusOut>", app._apply_error_dialog_settings)
    apply_tooltip(
        app.error_dialog_interval_entry,
        "Minimum seconds between modal error dialogs.",
    )
    apply_tooltip(
        app.error_dialog_window_entry,
        "Time window for counting dialog bursts.",
    )
    apply_tooltip(
        app.error_dialog_limit_entry,
        "Maximum dialogs allowed inside the burst window before suppressing.",
    )
    app.job_completion_popup_check = ttk.Checkbutton(
        dialog_frame,
        text="Show job completion dialog",
        variable=app.job_completion_popup,
    )
    app.job_completion_popup_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 2))
    apply_tooltip(
        app.job_completion_popup_check,
        "Pop up an alert when a job completes, summarizing start/finish/elapsed times.",
    )
    app.job_completion_beep_check = ttk.Checkbutton(
        dialog_frame,
        text="Play reminder beep on completion",
        variable=app.job_completion_beep,
    )
    app.job_completion_beep_check.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 4))
    apply_tooltip(
        app.job_completion_beep_check,
        "Ring the system bell when a job has finished streaming.",
    )
    return row + 1


def build_power_section(app, parent: ttk.Frame, row: int) -> int:
    if not sys.platform.startswith("linux"):
        return row
    power_frame = ttk.LabelFrame(parent, text="System", padding=8)
    power_frame.grid(row=row, column=0, sticky="ew", pady=(8, 0))
    power_frame.grid_columnconfigure(1, weight=1)

    def _log_status(text: str) -> None:
        try:
            app.ui_q.put(("log", text))
        except Exception:
            pass
        try:
            app.status.config(text=text)
        except Exception:
            pass

    def _run_power_action(action: str, label: str) -> None:
        confirm = messagebox.askyesno(
            "Confirm",
            f"{label} the system now?",
        )
        if not confirm:
            return
        try:
            app._save_settings()
        except Exception:
            pass
        try:
            subprocess.Popen(["systemctl", action])
            _log_status(f"[system] {label} requested")
            return
        except Exception:
            pass
        fallback_args = ["shutdown", "-h", "now"] if action == "poweroff" else ["shutdown", "-r", "now"]
        try:
            subprocess.Popen(fallback_args)
            _log_status(f"[system] {label} requested")
        except Exception as exc:
            _log_status(f"[system] {label} failed: {exc}")

    btn_row = ttk.Frame(power_frame)
    btn_row.grid(row=0, column=0, sticky="w")
    app.btn_shutdown = ttk.Button(
        btn_row,
        text="Shutdown",
        command=lambda: _run_power_action("poweroff", "Shutdown"),
    )
    app.btn_shutdown.pack(side="left")
    app.btn_reboot = ttk.Button(
        btn_row,
        text="Reboot",
        command=lambda: _run_power_action("reboot", "Reboot"),
    )
    app.btn_reboot.pack(side="left", padx=(8, 0))
    apply_tooltip(app.btn_shutdown, "Power off the system (Linux only).")
    apply_tooltip(app.btn_reboot, "Reboot the system (Linux only).")
    return row + 1
