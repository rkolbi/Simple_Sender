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

import tkinter as tk
from tkinter import ttk

from simple_sender.utils.constants import ALL_STOP_CHOICES, CURRENT_LINE_CHOICES
from simple_sender.ui.widgets import apply_tooltip, set_kb_id


def build_app_settings_tab(app, notebook):
    nb = notebook
    # App Settings tab
    sstab = ttk.Frame(nb, padding=8)
    nb.add(sstab, text="App Settings")
    sstab.grid_columnconfigure(0, weight=1)
    sstab.grid_rowconfigure(0, weight=1)
    app.app_settings_canvas = tk.Canvas(sstab, highlightthickness=0)
    app.app_settings_canvas.grid(row=0, column=0, sticky="nsew")
    app.app_settings_scroll = ttk.Scrollbar(
        sstab, orient="vertical", command=app.app_settings_canvas.yview
    )
    app.app_settings_scroll.grid(row=0, column=1, sticky="ns")
    app.app_settings_canvas.configure(yscrollcommand=app.app_settings_scroll.set)
    app._app_settings_inner = ttk.Frame(app.app_settings_canvas)
    app._app_settings_window = app.app_settings_canvas.create_window(
        (0, 0), window=app._app_settings_inner, anchor="nw"
    )
    app._app_settings_inner.bind("<Configure>", lambda event: app._update_app_settings_scrollregion())
    app.app_settings_canvas.bind("<Configure>", lambda event: app.app_settings_canvas.itemconfig(
        app._app_settings_window, width=event.width
    ))
    app._app_settings_inner.bind("<Enter>", lambda event: app._bind_app_settings_mousewheel())
    app._app_settings_inner.bind("<Leave>", lambda event: app._unbind_app_settings_mousewheel())
    app._app_settings_inner.grid_columnconfigure(0, weight=1)

    version_label = ttk.Label(
        app._app_settings_inner,
        textvariable=app.version_var,
        font=("TkDefaultFont", 10, "bold"),
    )
    version_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

    theme_frame = ttk.LabelFrame(app._app_settings_inner, text="Theme", padding=8)
    theme_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    theme_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(theme_frame, text="UI theme").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
    app.theme_combo = ttk.Combobox(
        theme_frame,
        state="readonly",
        values=app.available_themes,
        textvariable=app.selected_theme,
        width=28,
    )
    app.theme_combo.grid(row=0, column=1, sticky="w", pady=4)
    app.theme_combo.bind("<<ComboboxSelected>>", app._on_theme_change)
    apply_tooltip(
        app.theme_combo,
        "Pick a ttk theme; some themes require a restart for best results.",
    )

    safety = ttk.LabelFrame(app._app_settings_inner, text="Safety", padding=8)
    safety.grid(row=2, column=0, sticky="ew", pady=(0, 8))
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
    apply_tooltip(app.all_stop_combo, "Select how the ALL STOP button behaves.")
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

    estimation = ttk.LabelFrame(app._app_settings_inner, text="Estimation", padding=8)
    estimation.grid(row=3, column=0, sticky="ew", pady=(0, 8))
    estimation.grid_columnconfigure(1, weight=1)
    ttk.Label(estimation, text="Fallback rapid rate (mm/min)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.fallback_rapid_entry = ttk.Entry(estimation, textvariable=app.fallback_rapid_rate, width=12)
    app.fallback_rapid_entry.grid(row=0, column=1, sticky="w", pady=4)
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

    status_frame = ttk.LabelFrame(app._app_settings_inner, text="Status polling", padding=8)
    status_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
    status_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(status_frame, text="Status report interval (seconds)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.status_poll_entry = ttk.Entry(
        status_frame, textvariable=app.status_poll_interval, width=12
    )
    app.status_poll_entry.grid(row=0, column=1, sticky="w", pady=4)
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
    app.status_fail_limit_entry = ttk.Entry(
        status_frame, textvariable=app.status_query_failure_limit, width=12
    )
    app.status_fail_limit_entry.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(status_frame, text="(1-10)").grid(row=1, column=2, sticky="w", padx=(6, 0))
    app.status_fail_limit_entry.bind("<Return>", app._on_status_failure_limit_change)
    app.status_fail_limit_entry.bind("<FocusOut>", app._on_status_failure_limit_change)
    apply_tooltip(
        app.status_fail_limit_entry,
        "Consecutive status send failures before disconnecting (clamped to 1-10).",
    )

    dialog_frame = ttk.LabelFrame(app._app_settings_inner, text="Error dialogs", padding=8)
    dialog_frame.grid(row=5, column=0, sticky="ew", pady=(0, 8))
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
    app.error_dialog_interval_entry = ttk.Entry(
        dialog_frame, textvariable=app.error_dialog_interval_var, width=12
    )
    app.error_dialog_interval_entry.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(dialog_frame, text="sec").grid(row=1, column=2, sticky="w", padx=(6, 0))
    app.error_dialog_interval_entry.bind("<Return>", app._apply_error_dialog_settings)
    app.error_dialog_interval_entry.bind("<FocusOut>", app._apply_error_dialog_settings)
    ttk.Label(dialog_frame, text="Burst window (seconds)").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.error_dialog_window_entry = ttk.Entry(
        dialog_frame, textvariable=app.error_dialog_burst_window_var, width=12
    )
    app.error_dialog_window_entry.grid(row=2, column=1, sticky="w", pady=4)
    ttk.Label(dialog_frame, text="sec").grid(row=2, column=2, sticky="w", padx=(6, 0))
    app.error_dialog_window_entry.bind("<Return>", app._apply_error_dialog_settings)
    app.error_dialog_window_entry.bind("<FocusOut>", app._apply_error_dialog_settings)
    ttk.Label(dialog_frame, text="Max dialogs per window").grid(
        row=3, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.error_dialog_limit_entry = ttk.Entry(
        dialog_frame, textvariable=app.error_dialog_burst_limit_var, width=12
    )
    app.error_dialog_limit_entry.grid(row=3, column=1, sticky="w", pady=4)
    ttk.Label(dialog_frame, text="count").grid(row=3, column=2, sticky="w", padx=(6, 0))
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

    macro_frame = ttk.LabelFrame(app._app_settings_inner, text="Macros", padding=8)
    macro_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
    macro_frame.grid_columnconfigure(0, weight=1)
    app.macros_allow_python_check = ttk.Checkbutton(
        macro_frame,
        text="Allow macro scripting (Python/eval)",
        variable=app.macros_allow_python,
    )
    app.macros_allow_python_check.grid(row=0, column=0, sticky="w", pady=(0, 4))
    apply_tooltip(
        app.macros_allow_python_check,
        "Disable to allow only plain G-code lines in macros (no scripting or expressions).",
    )
    ttk.Label(
        macro_frame,
        text="Warning: enabled macros can execute arbitrary Python; disable for plain G-code macros.",
        wraplength=560,
        justify="left",
    ).grid(row=1, column=0, sticky="w")

    zeroing_frame = ttk.LabelFrame(app._app_settings_inner, text="Zeroing", padding=8)
    zeroing_frame.grid(row=7, column=0, sticky="ew", pady=(0, 8))
    zeroing_frame.grid_columnconfigure(0, weight=1)
    app.zeroing_persistent_check = ttk.Checkbutton(
        zeroing_frame,
        text="Use persistent zeroing (G10 L20)",
        variable=app.zeroing_persistent,
        command=app._on_zeroing_mode_change,
    )
    app.zeroing_persistent_check.grid(row=0, column=0, sticky="w", pady=(0, 4))
    apply_tooltip(
        app.zeroing_persistent_check,
        "Use G10 L20 to write WCS offsets instead of temporary G92 offsets.",
    )
    ttk.Label(
        zeroing_frame,
        text="Persistent zeroing saves the active WCS offsets to GRBL; standard zeroing uses G92.",
        wraplength=560,
        justify="left",
    ).grid(row=1, column=0, sticky="w")

    jog_frame = ttk.LabelFrame(app._app_settings_inner, text="Jogging", padding=8)
    jog_frame.grid(row=8, column=0, sticky="ew", pady=(0, 8))
    jog_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(jog_frame, text="Default jog feed (X/Y)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.jog_feed_xy_entry = ttk.Entry(jog_frame, textvariable=app.jog_feed_xy, width=12)
    app.jog_feed_xy_entry.grid(row=0, column=1, sticky="w", pady=4)
    app.jog_feed_xy_entry.bind("<Return>", app._on_jog_feed_change_xy)
    app.jog_feed_xy_entry.bind("<FocusOut>", app._on_jog_feed_change_xy)
    ttk.Label(jog_frame, text="Default jog feed (Z)").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.jog_feed_z_entry = ttk.Entry(jog_frame, textvariable=app.jog_feed_z, width=12)
    app.jog_feed_z_entry.grid(row=1, column=1, sticky="w", pady=4)
    app.jog_feed_z_entry.bind("<Return>", app._on_jog_feed_change_z)
    app.jog_feed_z_entry.bind("<FocusOut>", app._on_jog_feed_change_z)
    ttk.Label(jog_frame, text="Units: mm/min (in/min when in inches mode)").grid(
        row=0, column=2, sticky="w", padx=(8, 0), pady=4
    )
    ttk.Label(
        jog_frame,
        text="Used by the jog buttons. Enter positive values.",
        wraplength=560,
        justify="left",
    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 2))
    apply_tooltip(
        app.jog_feed_xy_entry,
        "Default speed for X/Y jog buttons (mm/min when in metric, in/min when in inches).",
    )
    apply_tooltip(
        app.jog_feed_z_entry,
        "Default speed for Z jog buttons (mm/min when in metric, in/min when in inches).",
    )

    kb_frame = ttk.LabelFrame(app._app_settings_inner, text="Keyboard shortcuts", padding=8)
    kb_frame.grid(row=9, column=0, sticky="nsew", pady=(0, 8))
    kb_frame.grid_columnconfigure(0, weight=1)
    kb_frame.grid_rowconfigure(1, weight=1)
    app.kb_enable_check = ttk.Checkbutton(
        kb_frame,
        text="Enabled",
        variable=app.keyboard_bindings_enabled,
        command=app._on_keyboard_bindings_check,
    )
    app.kb_enable_check.grid(row=0, column=0, sticky="w", padx=(6, 10), pady=(4, 2))
    apply_tooltip(app.kb_enable_check, "Toggle keyboard shortcuts.")

    app.kb_table = ttk.Treeview(
        kb_frame, columns=("button", "axis", "key", "joystick", "clear"), show="headings", height=6
    )
    app.kb_table.heading("button", text="Button")
    app.kb_table.heading("axis", text="Axis")
    app.kb_table.heading("key", text="Key")
    app.kb_table.heading("joystick", text="Joystick")
    app.kb_table.heading("clear", text="")
    app.kb_table.column("button", width=220, anchor="w")
    app.kb_table.column("axis", width=50, anchor="center")
    app.kb_table.column("key", width=140, anchor="center")
    app.kb_table.column("joystick", width=180, anchor="center")
    app.kb_table.column("clear", width=160, anchor="e")
    app.kb_table.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))
    app.kb_table_scroll = ttk.Scrollbar(kb_frame, orient="vertical", command=app.kb_table.yview)
    app.kb_table.configure(yscrollcommand=app.kb_table_scroll.set)
    app.kb_table_scroll.grid(row=1, column=1, sticky="ns", padx=(4, 6), pady=(0, 6))
    app.kb_table.bind("<Double-1>", app._on_kb_table_double_click)
    app.kb_table.bind("<Button-1>", app._on_kb_table_click, add="+")

    app.kb_note = ttk.Label(
        kb_frame,
        text="Press up to three keys to bind a shortcut. Bindings are ignored while typing in text fields.",
        wraplength=560,
        justify="left",
    )
    app.kb_note.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

    joystick_test_frame = ttk.LabelFrame(kb_frame, text="Joystick testing", padding=8)
    joystick_test_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=6, pady=(0, 6))
    joystick_test_frame.grid_columnconfigure(0, weight=1)
    joystick_test_frame.grid_columnconfigure(1, weight=0)
    app.joystick_test_label = ttk.Label(
        joystick_test_frame,
        textvariable=app.joystick_test_status,
        wraplength=520,
        justify="left",
    )
    app.joystick_test_label.grid(row=0, column=0, sticky="w")
    app.joystick_device_label = ttk.Label(
        joystick_test_frame,
        textvariable=app.joystick_device_status,
        wraplength=520,
        justify="left",
    )
    app.joystick_device_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
    app.btn_refresh_joysticks = ttk.Button(
        joystick_test_frame,
        text="Refresh joystick list",
        command=app._refresh_joystick_test_info,
    )
    app.btn_refresh_joysticks.grid(row=2, column=0, sticky="w", pady=(6, 0))
    app.btn_toggle_joystick_bindings = ttk.Button(
        joystick_test_frame,
        text="Enable USB Joystick Bindings",
        command=app._toggle_joystick_bindings,
    )
    set_kb_id(app.btn_toggle_joystick_bindings, "toggle_joystick_bindings")
    app.btn_toggle_joystick_bindings.grid(row=2, column=1, sticky="e", padx=(6, 0), pady=(6, 0))
    apply_tooltip(
        app.btn_toggle_joystick_bindings,
        "Enable or disable joystick shortcuts and capture new bindings from a USB joystick.",
    )
    app.joystick_event_label = ttk.Label(
        joystick_test_frame,
        textvariable=app.joystick_event_status,
        wraplength=520,
        justify="left",
    )
    app.joystick_event_label.grid(row=3, column=0, sticky="w", pady=(6, 0))

    app.joystick_safety_check = ttk.Checkbutton(
        joystick_test_frame,
        text="Require safety hold for joystick actions",
        variable=app.joystick_safety_enabled,
        command=app._on_joystick_safety_toggle,
    )
    app.joystick_safety_check.grid(row=4, column=0, sticky="w", pady=(8, 0))
    apply_tooltip(
        app.joystick_safety_check,
        "Require holding a safety button before other joystick actions are accepted.",
    )
    app.joystick_safety_label = ttk.Label(
        joystick_test_frame,
        textvariable=app.joystick_safety_status,
        wraplength=520,
        justify="left",
    )
    app.joystick_safety_label.grid(row=5, column=0, sticky="w", pady=(4, 0))
    app.btn_set_joystick_safety = ttk.Button(
        joystick_test_frame,
        text="Set Safety Button",
        command=app._start_joystick_safety_capture,
    )
    app.btn_set_joystick_safety.grid(row=6, column=0, sticky="w", pady=(6, 0))
    app.btn_clear_joystick_safety = ttk.Button(
        joystick_test_frame,
        text="Clear Safety Button",
        command=app._clear_joystick_safety_binding,
    )
    app.btn_clear_joystick_safety.grid(row=6, column=1, sticky="e", padx=(6, 0), pady=(6, 0))
    apply_tooltip(app.btn_set_joystick_safety, "Capture a joystick button to use as a safety hold.")
    apply_tooltip(app.btn_clear_joystick_safety, "Clear the safety button binding.")
    app._refresh_joystick_safety_display()

    input_state_frame = ttk.LabelFrame(kb_frame, text="Live input state", padding=8)
    input_state_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=6, pady=(0, 6))
    input_state_frame.grid_columnconfigure(0, weight=1)
    app.joystick_live_label = ttk.Label(
        input_state_frame,
        textvariable=app.joystick_live_status,
        wraplength=520,
        justify="left",
    )
    app.joystick_live_label.grid(row=0, column=0, sticky="w")
    app.keyboard_live_label = ttk.Label(
        input_state_frame,
        textvariable=app.keyboard_live_status,
        wraplength=520,
        justify="left",
    )
    app.keyboard_live_label.grid(row=1, column=0, sticky="w", pady=(4, 0))

    view_frame = ttk.LabelFrame(app._app_settings_inner, text="G-code view", padding=8)
    view_frame.grid(row=10, column=0, sticky="ew")
    view_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(view_frame, text="Current line highlight").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.current_line_combo = ttk.Combobox(
        view_frame,
        state="readonly",
        values=[label for label, _ in CURRENT_LINE_CHOICES],
        width=32,
    )
    app.current_line_combo.grid(row=0, column=1, sticky="w", pady=4)
    app.current_line_combo.bind("<<ComboboxSelected>>", app._on_current_line_mode_change)
    apply_tooltip(app.current_line_combo, "Select which line is highlighted as current.")
    app._sync_current_line_mode_combo()
    app.current_line_desc = ttk.Label(
        view_frame,
        text=(
            "Processing highlights the line currently executing "
            "(the next line queued after the last ack). "
            "Sent highlights the most recently queued line."
        ),
        wraplength=560,
        justify="left",
    )
    app.current_line_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

    diagnostics_frame = ttk.LabelFrame(app._app_settings_inner, text="Diagnostics", padding=8)
    diagnostics_frame.grid(row=11, column=0, sticky="ew", pady=(8, 0))
    diagnostics_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(diagnostics_frame, text="Release checklist").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.btn_release_checklist = ttk.Button(
        diagnostics_frame,
        text="Open checklist",
        command=app._show_release_checklist,
    )
    app.btn_release_checklist.grid(row=0, column=1, sticky="w", pady=4)
    apply_tooltip(
        app.btn_release_checklist,
        "Open a quick regression checklist for streaming, jogging, and unit handling.",
    )

    tw_frame = ttk.LabelFrame(app._app_settings_inner, text="Safety Aids", padding=8)
    tw_frame.grid(row=12, column=0, sticky="ew", pady=(8, 0))
    app.training_wheels_check = ttk.Checkbutton(
        tw_frame,
        text="Training Wheels (confirm top-bar actions)",
        variable=app.training_wheels,
    )
    app.training_wheels_check.grid(row=0, column=0, sticky="w")
    apply_tooltip(app.training_wheels_check, "Show confirmation dialogs for top toolbar actions.")

    app.reconnect_check = ttk.Checkbutton(
        tw_frame,
        text="Reconnect to last port on open",
        variable=app.reconnect_on_open,
    )
    app.reconnect_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
    apply_tooltip(app.reconnect_check, "Auto-connect to the last used port when the app starts.")

    interface_frame = ttk.LabelFrame(app._app_settings_inner, text="Interface", padding=8)
    interface_frame.grid(row=13, column=0, sticky="ew", pady=(8, 0))
    interface_frame.grid_columnconfigure(0, weight=1)
    app.resume_button_check = ttk.Checkbutton(
        interface_frame,
        text="Show 'Resume From...' button",
        variable=app.show_resume_from_button,
        command=app._on_resume_button_visibility_change,
    )
    app.resume_button_check.grid(row=0, column=0, sticky="w")
    apply_tooltip(
        app.resume_button_check,
        "Toggle the visibility of the toolbar button that lets you resume from a specific line.",
    )
    app.recover_button_check = ttk.Checkbutton(
        interface_frame,
        text="Show 'Recover' button",
        variable=app.show_recover_button,
        command=app._on_recover_button_visibility_change,
    )
    app.recover_button_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
    apply_tooltip(
        app.recover_button_check,
        "Show or hide the Recover button that brings up the alarm recovery dialog.",
    )
    perf_btn_frame = ttk.Frame(interface_frame)
    perf_btn_frame.grid(row=2, column=0, sticky="w", pady=(6, 0))
    app.btn_performance_mode = ttk.Button(
        perf_btn_frame,
        text="Performance: On" if app.performance_mode.get() else "Performance: Off",
        command=app._toggle_performance,
    )
    set_kb_id(app.btn_performance_mode, "toggle_performance")
    app.btn_performance_mode.pack(side="left")
    apply_tooltip(app.btn_performance_mode, "Enable performance mode (batch console updates).")

    app.logging_check = ttk.Checkbutton(
        interface_frame,
        text="Log GUI button actions",
        variable=app.gui_logging_enabled,
        command=app._on_gui_logging_change,
    )
    app.logging_check.grid(row=3, column=0, sticky="w", pady=(8, 0))
    apply_tooltip(
        app.logging_check,
        "Record GUI button actions in the console log when enabled.",
    )

    indicator_label = ttk.Label(interface_frame, text="Status indicators")
    indicator_label.grid(row=4, column=0, sticky="w", pady=(10, 0))
    indicator_row = ttk.Frame(interface_frame)
    indicator_row.grid(row=5, column=0, sticky="w", pady=(2, 0))
    app.endstop_indicator_check = ttk.Checkbutton(
        indicator_row,
        text="Endstops",
        variable=app.show_endstop_indicator,
        command=app._on_led_visibility_change,
    )
    app.endstop_indicator_check.pack(side="left")
    apply_tooltip(app.endstop_indicator_check, "Show or hide the Endstops status indicator.")
    app.probe_indicator_check = ttk.Checkbutton(
        indicator_row,
        text="Probe",
        variable=app.show_probe_indicator,
        command=app._on_led_visibility_change,
    )
    app.probe_indicator_check.pack(side="left", padx=(12, 0))
    apply_tooltip(app.probe_indicator_check, "Show or hide the Probe status indicator.")
    app.hold_indicator_check = ttk.Checkbutton(
        indicator_row,
        text="Hold",
        variable=app.show_hold_indicator,
        command=app._on_led_visibility_change,
    )
    app.hold_indicator_check.pack(side="left", padx=(12, 0))
    apply_tooltip(app.hold_indicator_check, "Show or hide the Hold status indicator.")

    quick_buttons_label = ttk.Label(interface_frame, text="Quick buttons (status bar)")
    quick_buttons_label.grid(row=6, column=0, sticky="w", pady=(10, 0))
    quick_buttons_row = ttk.Frame(interface_frame)
    quick_buttons_row.grid(row=7, column=0, sticky="w", pady=(2, 0))
    app.quick_tips_check = ttk.Checkbutton(
        quick_buttons_row,
        text="Tips",
        variable=app.show_quick_tips_button,
        command=app._on_quick_button_visibility_change,
    )
    app.quick_tips_check.pack(side="left")
    apply_tooltip(app.quick_tips_check, "Show or hide the Tips quick button in the status bar.")
    app.quick_3d_check = ttk.Checkbutton(
        quick_buttons_row,
        text="3DR",
        variable=app.show_quick_3d_button,
        command=app._on_quick_button_visibility_change,
    )
    app.quick_3d_check.pack(side="left", padx=(12, 0))
    apply_tooltip(app.quick_3d_check, "Show or hide the 3DR quick button in the status bar.")
    app.quick_keys_check = ttk.Checkbutton(
        quick_buttons_row,
        text="Keys",
        variable=app.show_quick_keys_button,
        command=app._on_quick_button_visibility_change,
    )
    app.quick_keys_check.pack(side="left", padx=(12, 0))
    apply_tooltip(app.quick_keys_check, "Show or hide the Keys quick button in the status bar.")
    app.quick_release_check = ttk.Checkbutton(
        quick_buttons_row,
        text="Release",
        variable=app.show_quick_release_button,
        command=app._on_quick_button_visibility_change,
    )
    app.quick_release_check.pack(side="left", padx=(12, 0))
    apply_tooltip(
        app.quick_release_check,
        "Show or hide the Release checklist quick button in the status bar.",
    )

    toggle_btn_row = ttk.Frame(interface_frame)
    toggle_btn_row.grid(row=8, column=0, sticky="w", pady=(10, 0))
    app.btn_toggle_tips_settings = ttk.Button(
        toggle_btn_row,
        text="Tips",
        command=app._toggle_tooltips,
    )
    set_kb_id(app.btn_toggle_tips_settings, "toggle_tooltips_settings")
    app.btn_toggle_tips_settings.pack(side="left")
    apply_tooltip(app.btn_toggle_tips_settings, "Toggle tool tips.")
    app.btn_toggle_3d_settings = ttk.Button(
        toggle_btn_row,
        text="3DR",
        command=app._toggle_render_3d,
    )
    set_kb_id(app.btn_toggle_3d_settings, "toggle_render_3d_settings")
    app.btn_toggle_3d_settings.pack(side="left", padx=(8, 0))
    apply_tooltip(app.btn_toggle_3d_settings, "Toggle 3D toolpath rendering.")
    app.btn_toggle_keybinds_settings = ttk.Button(
        toggle_btn_row,
        text="Keys",
        command=app._toggle_keyboard_bindings,
    )
    set_kb_id(app.btn_toggle_keybinds_settings, "toggle_keybindings_settings")
    app.btn_toggle_keybinds_settings.pack(side="left", padx=(8, 0))
    apply_tooltip(app.btn_toggle_keybinds_settings, "Toggle keyboard shortcuts.")

    toolpath_settings = ttk.LabelFrame(app._app_settings_inner, text="3D View", padding=8)
    toolpath_settings.grid(row=15, column=0, sticky="ew", pady=(8, 0))
    toolpath_settings.grid_columnconfigure(1, weight=1)
    ttk.Label(toolpath_settings, text="Streaming refresh (sec)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.toolpath_streaming_interval_entry = ttk.Entry(
        toolpath_settings,
        textvariable=app.toolpath_streaming_render_interval,
        width=10,
    )
    app.toolpath_streaming_interval_entry.grid(row=0, column=1, sticky="w", pady=4)
    app.toolpath_streaming_interval_entry.bind(
        "<Return>", app._apply_toolpath_streaming_render_interval
    )
    app.toolpath_streaming_interval_entry.bind(
        "<FocusOut>", app._apply_toolpath_streaming_render_interval
    )
    ttk.Label(toolpath_settings, text="(0.05 - 2.0)").grid(
        row=0, column=2, sticky="w", padx=(6, 0), pady=4
    )
    apply_tooltip(
        app.toolpath_streaming_interval_entry,
        "Minimum time between 3D redraws while streaming.",
    )

