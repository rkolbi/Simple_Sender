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

from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import (
    AUTOLEVEL_LARGE_MIN_AREA_DEFAULT,
    TOOLPATH_STREAMING_RENDER_INTERVAL_MAX,
    TOOLPATH_STREAMING_RENDER_INTERVAL_MIN,
)
from simple_sender.ui.autolevel_dialog.prefs import pref_dict, pref_float, pref_interp
from simple_sender.ui.widgets import apply_tooltip, attach_numeric_keypad, set_kb_id

def build_safety_aids_section(app, parent: ttk.Frame, row: int) -> int:
    tw_frame = ttk.LabelFrame(parent, text="Safety Aids", padding=8)
    tw_frame.grid(row=row, column=0, sticky="ew", pady=(8, 0))
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
    return row + 1


def build_interface_section(app, parent: ttk.Frame, row: int) -> int:
    interface_frame = ttk.LabelFrame(parent, text="Interface", padding=8)
    interface_frame.grid(row=row, column=0, sticky="ew", pady=(8, 0))
    interface_frame.grid_columnconfigure(0, weight=1)
    app.fullscreen_startup_check = ttk.Checkbutton(
        interface_frame,
        text="Start in fullscreen",
        variable=app.fullscreen_on_startup,
    )
    app.fullscreen_startup_check.grid(row=0, column=0, sticky="w")
    apply_tooltip(
        app.fullscreen_startup_check,
        "Enable fullscreen on startup (takes effect after restart).",
    )
    app.resume_button_check = ttk.Checkbutton(
        interface_frame,
        text="Show 'Resume From...' button",
        variable=app.show_resume_from_button,
        command=app._on_resume_button_visibility_change,
    )
    app.resume_button_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
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
    app.recover_button_check.grid(row=2, column=0, sticky="w", pady=(4, 0))
    apply_tooltip(
        app.recover_button_check,
        "Show or hide the Recover button that brings up the alarm recovery dialog.",
    )
    app.auto_level_enabled_check = ttk.Checkbutton(
        interface_frame,
        text="Enable Auto-Level",
        variable=app.auto_level_enabled,
        command=app._on_auto_level_enabled_change,
    )
    app.auto_level_enabled_check.grid(row=3, column=0, sticky="w", pady=(4, 0))
    apply_tooltip(
        app.auto_level_enabled_check,
        "Show Auto-Level in the toolbar after a job loads (disable to keep it hidden).",
    )
    perf_row = ttk.Frame(interface_frame)
    perf_row.grid(row=4, column=0, sticky="w", pady=(6, 0))
    ttk.Label(perf_row, text="Performance mode (batch console updates)").pack(side="left")

    def _refresh_performance_button() -> None:
        try:
            enabled = bool(app.performance_mode.get())
        except Exception:
            enabled = False
        try:
            app.btn_performance_mode.config(text=f"Performance: {'On' if enabled else 'Off'}")
        except Exception:
            pass

    def _toggle_performance_mode() -> None:
        before = False
        try:
            before = bool(app.performance_mode.get())
        except Exception:
            before = False
        handler = getattr(app, "_toggle_performance", None)
        if callable(handler):
            handler()
        else:
            try:
                app.performance_mode.set(not before)
            except Exception:
                pass
            on_change = getattr(app, "_on_performance_mode_change", None)
            if callable(on_change):
                on_change()
        _refresh_performance_button()

    app.btn_performance_mode = ttk.Button(
        perf_row,
        text="Performance: Off",
        command=_toggle_performance_mode,
    )
    app.btn_performance_mode.pack(side="left", padx=(8, 0))
    apply_tooltip(app.btn_performance_mode, "Toggle performance mode (batch console updates).")
    _refresh_performance_button()
    try:
        app.performance_mode.trace_add("write", lambda *_args: _refresh_performance_button())
    except Exception:
        pass

    app.logging_check = ttk.Checkbutton(
        interface_frame,
        text="Log GUI button actions",
        variable=app.gui_logging_enabled,
        command=app._on_gui_logging_change,
    )
    app.logging_check.grid(row=5, column=0, sticky="w", pady=(8, 0))
    apply_tooltip(
        app.logging_check,
        "Record GUI button actions in the console log when enabled.",
    )
    logs_btn_row = ttk.Frame(interface_frame)
    logs_btn_row.grid(row=6, column=0, sticky="w", pady=(6, 0))
    def _show_logs():
        handler = getattr(app, "_show_logs_dialog", None)
        if callable(handler):
            handler()

    app.view_logs_button = ttk.Button(
        logs_btn_row,
        text="View Logs...",
        command=_show_logs,
    )
    if not callable(getattr(app, "_show_logs_dialog", None)):
        app.view_logs_button.state(["disabled"])
    app.view_logs_button.pack(side="left")
    apply_tooltip(
        app.view_logs_button,
        "Open the application log viewer and export logs for diagnostics.",
    )
    indicator_label = ttk.Label(interface_frame, text="Status indicators")
    indicator_label.grid(row=7, column=0, sticky="w", pady=(10, 0))
    indicator_row = ttk.Frame(interface_frame)
    indicator_row.grid(row=8, column=0, sticky="w", pady=(2, 0))
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

    status_bar_label = ttk.Label(interface_frame, text="Status bar")
    status_bar_label.grid(row=9, column=0, sticky="w", pady=(10, 0))
    quick_buttons_row = ttk.Frame(interface_frame)
    quick_buttons_row.grid(row=10, column=0, sticky="w", pady=(2, 0))
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
    apply_tooltip(
        app.quick_3d_check,
        "Show or hide the 3D Render quick button in the status bar.",
    )
    app.quick_keys_check = ttk.Checkbutton(
        quick_buttons_row,
        text="Keys",
        variable=app.show_quick_keys_button,
        command=app._on_quick_button_visibility_change,
    )
    app.quick_keys_check.pack(side="left", padx=(12, 0))
    apply_tooltip(app.quick_keys_check, "Show or hide the Keys quick button in the status bar.")
    app.quick_alo_check = ttk.Checkbutton(
        quick_buttons_row,
        text="ALO",
        variable=app.show_quick_alo_button,
        command=app._on_quick_button_visibility_change,
    )
    app.quick_alo_check.pack(side="left", padx=(12, 0))
    apply_tooltip(
        app.quick_alo_check,
        "Show or hide the Auto-Level Overlay quick button in the status bar.",
    )
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

    quick_toggle_label = ttk.Label(interface_frame, text="Status bar quick toggles")
    quick_toggle_label.grid(row=11, column=0, sticky="w", pady=(6, 0))
    toggle_btn_row = ttk.Frame(interface_frame)
    toggle_btn_row.grid(row=12, column=0, sticky="w", pady=(2, 0))
    app.btn_toggle_tips_settings = ttk.Button(
        toggle_btn_row,
        text="Tips",
        command=app._toggle_tooltips,
    )
    set_kb_id(app.btn_toggle_tips_settings, "toggle_tooltips_settings")
    app.btn_toggle_tips_settings.pack(side="left")
    apply_tooltip(
        app.btn_toggle_tips_settings,
        "Toggle tooltips on/off (same as the Tips quick button).",
    )
    app.btn_toggle_3d_settings = ttk.Button(
        toggle_btn_row,
        text="3DR",
        command=app._toggle_render_3d,
    )
    set_kb_id(app.btn_toggle_3d_settings, "toggle_render_3d_settings")
    app.btn_toggle_3d_settings.pack(side="left", padx=(8, 0))
    apply_tooltip(
        app.btn_toggle_3d_settings,
        "Toggle the 3D render preview (same as the 3D Render quick button).",
    )
    app.btn_toggle_keybinds_settings = ttk.Button(
        toggle_btn_row,
        text="Keys",
        command=app._toggle_keyboard_bindings,
    )
    set_kb_id(app.btn_toggle_keybinds_settings, "toggle_keybindings_settings")
    app.btn_toggle_keybinds_settings.pack(side="left", padx=(8, 0))
    apply_tooltip(
        app.btn_toggle_keybinds_settings,
        "Toggle keyboard shortcuts on/off (same as the Keys quick button).",
    )
    app.btn_toggle_autolevel_overlay_settings = ttk.Button(
        toggle_btn_row,
        text="ALO",
        command=app._toggle_autolevel_overlay,
    )
    set_kb_id(app.btn_toggle_autolevel_overlay_settings, "toggle_autolevel_overlay_settings")
    app.btn_toggle_autolevel_overlay_settings.pack(side="left", padx=(8, 0))
    apply_tooltip(
        app.btn_toggle_autolevel_overlay_settings,
        "Toggle the Auto-Level overlay in the toolpath views (same as the Auto-Level Overlay quick button).",
    )
    return row + 1


def build_auto_level_section(app, parent: ttk.Frame, row: int) -> int:
    auto_level_frame = ttk.LabelFrame(parent, text="Auto-Level", padding=8)
    auto_level_frame.grid(row=row, column=0, sticky="ew", pady=(8, 0))
    auto_level_frame.grid_columnconfigure(1, weight=1)
    app.auto_level_frame = auto_level_frame
    if not bool(app.auto_level_enabled.get()):
        auto_level_frame.grid_remove()
    default_job_prefs = DEFAULT_SETTINGS.get("auto_level_job_prefs", {})
    job_prefs = getattr(app, "auto_level_job_prefs", None)
    if not isinstance(job_prefs, dict):
        job_prefs = {}

    small_defaults = pref_dict(default_job_prefs, "small")
    large_defaults = pref_dict(default_job_prefs, "large")
    custom_defaults = pref_dict(default_job_prefs, "custom")
    small_vals = pref_dict(job_prefs, "small")
    large_vals = pref_dict(job_prefs, "large")
    custom_vals = pref_dict(job_prefs, "custom")

    small_max_area_var = tk.StringVar(
        value=str(pref_float(job_prefs.get("small_max_area"), default_job_prefs.get("small_max_area", 2500.0)))
    )
    large_min_area_var = tk.StringVar(
        value=str(
            pref_float(
                job_prefs.get("large_min_area"),
                default_job_prefs.get("large_min_area", AUTOLEVEL_LARGE_MIN_AREA_DEFAULT),
            )
        )
    )
    small_spacing_var = tk.StringVar(
        value=str(pref_float(small_vals.get("spacing"), small_defaults.get("spacing", 3.0)))
    )
    small_interp_var = tk.StringVar(
        value=pref_interp(small_vals.get("interpolation"), small_defaults.get("interpolation", "bicubic"))
    )
    large_spacing_var = tk.StringVar(
        value=str(pref_float(large_vals.get("spacing"), large_defaults.get("spacing", 8.0)))
    )
    large_interp_var = tk.StringVar(
        value=pref_interp(large_vals.get("interpolation"), large_defaults.get("interpolation", "bilinear"))
    )
    custom_spacing_var = tk.StringVar(
        value=str(pref_float(custom_vals.get("spacing"), custom_defaults.get("spacing", 5.0)))
    )
    custom_interp_var = tk.StringVar(
        value=pref_interp(custom_vals.get("interpolation"), custom_defaults.get("interpolation", "bicubic"))
    )

    def _save_autolevel_job_prefs(_event=None) -> None:
        small_max_area = pref_float(
            small_max_area_var.get(),
            pref_float(job_prefs.get("small_max_area"), default_job_prefs.get("small_max_area", 2500.0)),
        )
        large_min_area = pref_float(
            large_min_area_var.get(),
            pref_float(
                job_prefs.get("large_min_area"),
                default_job_prefs.get("large_min_area", AUTOLEVEL_LARGE_MIN_AREA_DEFAULT),
            ),
        )
        small_spacing = pref_float(
            small_spacing_var.get(),
            pref_float(small_vals.get("spacing"), small_defaults.get("spacing", 3.0)),
        )
        large_spacing = pref_float(
            large_spacing_var.get(),
            pref_float(large_vals.get("spacing"), large_defaults.get("spacing", 8.0)),
        )
        custom_spacing = pref_float(
            custom_spacing_var.get(),
            pref_float(custom_vals.get("spacing"), custom_defaults.get("spacing", 5.0)),
        )
        small_interp = pref_interp(small_interp_var.get(), "bicubic")
        large_interp = pref_interp(large_interp_var.get(), "bilinear")
        custom_interp = pref_interp(custom_interp_var.get(), "bicubic")
        prefs = {
            "small_max_area": small_max_area,
            "large_min_area": large_min_area,
            "small": {"spacing": small_spacing, "interpolation": small_interp},
            "large": {"spacing": large_spacing, "interpolation": large_interp},
            "custom": {"spacing": custom_spacing, "interpolation": custom_interp},
        }
        app.auto_level_job_prefs = prefs
        try:
            app.settings["auto_level_job_prefs"] = dict(prefs)
        except Exception:
            pass

    ttk.Label(auto_level_frame, text="Job size thresholds (area, mm^2)").grid(
        row=0, column=0, columnspan=3, sticky="w"
    )
    ttk.Label(auto_level_frame, text="Small max area").grid(row=1, column=0, sticky="w", pady=2)
    small_max_area_entry = ttk.Entry(auto_level_frame, textvariable=small_max_area_var, width=12)
    small_max_area_entry.grid(row=1, column=1, sticky="w", pady=2)
    attach_numeric_keypad(small_max_area_entry, allow_decimal=True)
    apply_tooltip(
        small_max_area_entry,
        "Max job area (mm^2) that uses the Small preset.",
    )
    ttk.Label(auto_level_frame, text="Large min area").grid(row=2, column=0, sticky="w", pady=2)
    large_min_area_entry = ttk.Entry(auto_level_frame, textvariable=large_min_area_var, width=12)
    large_min_area_entry.grid(row=2, column=1, sticky="w", pady=2)
    attach_numeric_keypad(large_min_area_entry, allow_decimal=True)
    apply_tooltip(
        large_min_area_entry,
        "Min job area (mm^2) that uses the Large preset.",
    )
    ttk.Label(auto_level_frame, text="Preset").grid(row=3, column=0, sticky="w", pady=(8, 2))
    preset_header = ttk.Frame(auto_level_frame)
    preset_header.grid(row=3, column=1, sticky="w", pady=(8, 2))
    ttk.Label(preset_header, text="Base spacing (mm)").pack(side="left")
    ttk.Label(preset_header, text="Interpolation").pack(side="left", padx=(18, 0))

    ttk.Label(auto_level_frame, text="Small").grid(row=4, column=0, sticky="w", pady=2)
    small_row = ttk.Frame(auto_level_frame)
    small_row.grid(row=4, column=1, sticky="w", pady=2)
    small_spacing_entry = ttk.Entry(small_row, textvariable=small_spacing_var, width=12)
    small_spacing_entry.pack(side="left")
    attach_numeric_keypad(small_spacing_entry, allow_decimal=True)
    small_interp_combo = ttk.Combobox(
        small_row, textvariable=small_interp_var, values=("bilinear", "bicubic"), state="readonly", width=10
    )
    small_interp_combo.pack(side="left", padx=(12, 0))
    apply_tooltip(
        small_spacing_entry,
        "Base spacing used for Small jobs before adaptive scaling.",
    )
    apply_tooltip(
        small_interp_combo,
        "Interpolation method for Small jobs.",
    )

    ttk.Label(auto_level_frame, text="Large").grid(row=5, column=0, sticky="w", pady=2)
    large_row = ttk.Frame(auto_level_frame)
    large_row.grid(row=5, column=1, sticky="w", pady=2)
    large_spacing_entry = ttk.Entry(large_row, textvariable=large_spacing_var, width=12)
    large_spacing_entry.pack(side="left")
    attach_numeric_keypad(large_spacing_entry, allow_decimal=True)
    large_interp_combo = ttk.Combobox(
        large_row, textvariable=large_interp_var, values=("bilinear", "bicubic"), state="readonly", width=10
    )
    large_interp_combo.pack(side="left", padx=(12, 0))
    apply_tooltip(
        large_spacing_entry,
        "Base spacing used for Large jobs before adaptive scaling.",
    )
    apply_tooltip(
        large_interp_combo,
        "Interpolation method for Large jobs.",
    )

    ttk.Label(auto_level_frame, text="Custom").grid(row=6, column=0, sticky="w", pady=2)
    custom_row = ttk.Frame(auto_level_frame)
    custom_row.grid(row=6, column=1, sticky="w", pady=2)
    custom_spacing_entry = ttk.Entry(custom_row, textvariable=custom_spacing_var, width=12)
    custom_spacing_entry.pack(side="left")
    attach_numeric_keypad(custom_spacing_entry, allow_decimal=True)
    custom_interp_combo = ttk.Combobox(
        custom_row, textvariable=custom_interp_var, values=("bilinear", "bicubic"), state="readonly", width=10
    )
    custom_interp_combo.pack(side="left", padx=(12, 0))
    apply_tooltip(
        custom_spacing_entry,
        "Base spacing used for Custom jobs before adaptive scaling.",
    )
    apply_tooltip(
        custom_interp_combo,
        "Interpolation method for Custom jobs.",
    )

    ttk.Label(
        auto_level_frame,
        text="Auto-Level uses these presets based on G-code area; Custom applies between thresholds.",
        wraplength=560,
        justify="left",
    ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))

    for entry in (
        small_max_area_entry,
        large_min_area_entry,
        small_spacing_entry,
        large_spacing_entry,
        custom_spacing_entry,
    ):
        entry.bind("<Return>", _save_autolevel_job_prefs)
        entry.bind("<FocusOut>", _save_autolevel_job_prefs)
    for combo in (small_interp_combo, large_interp_combo, custom_interp_combo):
        combo.bind("<<ComboboxSelected>>", _save_autolevel_job_prefs)
    return row + 1


def build_toolpath_settings_section(app, parent: ttk.Frame, row: int) -> int:
    toolpath_settings = ttk.LabelFrame(parent, text="3D View", padding=8)
    toolpath_settings.grid(row=row, column=0, sticky="ew", pady=(8, 0))
    toolpath_settings.grid_columnconfigure(1, weight=1)
    ttk.Label(toolpath_settings, text="Streaming refresh (sec)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    toolpath_interval_row = ttk.Frame(toolpath_settings)
    toolpath_interval_row.grid(row=0, column=1, sticky="w", pady=4)
    app.toolpath_streaming_interval_entry = ttk.Entry(
        toolpath_interval_row,
        textvariable=app.toolpath_streaming_render_interval,
        width=10,
    )
    app.toolpath_streaming_interval_entry.pack(side="left")
    attach_numeric_keypad(app.toolpath_streaming_interval_entry, allow_decimal=True)
    app.toolpath_streaming_interval_entry.bind(
        "<Return>", app._apply_toolpath_streaming_render_interval
    )
    app.toolpath_streaming_interval_entry.bind(
        "<FocusOut>", app._apply_toolpath_streaming_render_interval
    )
    ttk.Label(
        toolpath_interval_row,
        text=f"({TOOLPATH_STREAMING_RENDER_INTERVAL_MIN:g} - {TOOLPATH_STREAMING_RENDER_INTERVAL_MAX:g})",
    ).pack(side="left", padx=(6, 0))
    apply_tooltip(
        app.toolpath_streaming_interval_entry,
        "Minimum time between 3D redraws while streaming.",
    )
    return row + 1
