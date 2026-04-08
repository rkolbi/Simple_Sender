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

import sys
import tkinter as tk
from tkinter import ttk

from simple_sender.utils.constants import (
    JOG_DRO_SMOOTHING_ALL_JOG,
    JOG_DRO_SMOOTHING_OFF,
    JOG_DRO_SMOOTHING_UI_JOG_ONLY,
)
from simple_sender.ui.widgets_keypad import attach_numeric_keypad
from simple_sender.ui.widgets_tooltips import apply_tooltip
from simple_sender.ui.widgets_common import set_kb_id


_KASA_OUTLET_OPTIONS = ("Outlet 1", "Outlet 2")
_ALIGNED_SETTINGS_LABEL_WIDTH_CHARS = 36
_ALIGNED_SETTINGS_ENTRY_WIDTH = 12
_ALIGNED_SETTINGS_PAIR_GAP_PX = 24
_ALIGNED_SETTINGS_SECTION_PADDING = 8
_ALIGNED_SETTINGS_NESTED_CONTENT_OFFSET_PX = 10


def _configure_aligned_numeric_columns(frame) -> None:
    frame.grid_columnconfigure(2, minsize=_ALIGNED_SETTINGS_PAIR_GAP_PX)
    frame.grid_columnconfigure(5, weight=1)


def _aligned_setting_label(frame, *, text: str):
    return ttk.Label(frame, text=text, width=_ALIGNED_SETTINGS_LABEL_WIDTH_CHARS, anchor="w")


def _add_numeric_setting(
    app,
    frame,
    *,
    row_index: int,
    pair_index: int = 0,
    label_text: str,
    var,
    attr_name: str,
    tooltip_text: str,
    suffix_text: str | None = None,
) -> ttk.Entry:
    base_col = int(pair_index) * 3
    _aligned_setting_label(frame, text=label_text).grid(
        row=row_index,
        column=base_col,
        sticky="w",
        padx=(0, 8),
        pady=4,
    )
    entry = ttk.Entry(
        frame,
        textvariable=var,
        width=_ALIGNED_SETTINGS_ENTRY_WIDTH,
    )
    entry.grid(row=row_index, column=base_col + 1, sticky="w", pady=4)
    setattr(app, attr_name, entry)
    attach_numeric_keypad(entry, allow_decimal=True)
    if suffix_text:
        ttk.Label(frame, text=suffix_text).grid(
            row=row_index,
            column=base_col + 2,
            sticky="w",
            padx=(8, 0),
            pady=4,
        )
    apply_tooltip(entry, tooltip_text)
    return entry


def _build_xyz_plate_settings(app, frame: ttk.LabelFrame) -> None:
    _configure_aligned_numeric_columns(frame)
    _add_numeric_setting(
        app,
        frame,
        row_index=0,
        pair_index=0,
        label_text="XYZ Plate Thickness",
        var=app.xyz_plate_thickness,
        attr_name="xyz_plate_thickness_entry",
        tooltip_text="Measured touch-plate thickness in mm used when setting Z during XYZ Plate and Z Plate setup.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=0,
        pair_index=1,
        label_text="XYZ Plate Min Safe Probe Distance",
        var=app.xyz_plate_min_safe_probe_distance,
        attr_name="xyz_plate_min_safe_probe_distance_entry",
        tooltip_text="Minimum remaining downward machine-Z travel required before starting the touch-plate fast probe.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=1,
        pair_index=0,
        label_text="XYZ Plate X Offset",
        var=app.xyz_plate_x_offset,
        attr_name="xyz_plate_x_offset_entry",
        tooltip_text="Work-coordinate X offset written after probing the XYZ plate X edge.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=1,
        pair_index=1,
        label_text="XYZ Plate Y Offset",
        var=app.xyz_plate_y_offset,
        attr_name="xyz_plate_y_offset_entry",
        tooltip_text="Work-coordinate Y offset written after probing the XYZ plate Y edge.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=2,
        pair_index=0,
        label_text="XYZ Plate Side Clearance Distance",
        var=app.xyz_plate_side_clearance_distance,
        attr_name="xyz_plate_side_clearance_distance_entry",
        tooltip_text="Distance in mm to move clear of the touch plate before probing the X and Y side edges.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=2,
        pair_index=1,
        label_text="XYZ Plate Z Rough Probe Speed",
        var=app.xyz_plate_z_rough_probe_speed,
        attr_name="xyz_plate_z_rough_probe_speed_entry",
        tooltip_text="Initial Z touch-plate seek speed in mm/min for XYZ Plate and Z Plate setup.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=3,
        pair_index=0,
        label_text="XYZ Plate Z Re-Probe Speed",
        var=app.xyz_plate_z_reprobe_speed,
        attr_name="xyz_plate_z_reprobe_speed_entry",
        tooltip_text="Intermediate Z re-probe speed in mm/min before the final fine touch-plate pass.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=3,
        pair_index=1,
        label_text="XYZ Plate Z Fine Probe Speed",
        var=app.xyz_plate_z_fine_probe_speed,
        attr_name="xyz_plate_z_fine_probe_speed_entry",
        tooltip_text="Final fine Z touch-plate probe speed in mm/min.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=4,
        pair_index=0,
        label_text="XYZ Plate XY Rough Probe Speed",
        var=app.xyz_plate_xy_rough_probe_speed,
        attr_name="xyz_plate_xy_rough_probe_speed_entry",
        tooltip_text="Initial X/Y edge-probe speed in mm/min for the XYZ plate workflow.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=4,
        pair_index=1,
        label_text="XYZ Plate XY Fine Probe Speed",
        var=app.xyz_plate_xy_fine_probe_speed,
        attr_name="xyz_plate_xy_fine_probe_speed_entry",
        tooltip_text="Final X/Y edge-probe speed in mm/min for the XYZ plate workflow.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=5,
        pair_index=0,
        label_text="XYZ Plate Probe Dwell (Seconds)",
        var=app.xyz_plate_probe_dwell,
        attr_name="xyz_plate_probe_dwell_entry",
        tooltip_text="Pause in seconds between the Z re-probe retract and the final fine touch-plate pass.",
    )


def _build_bit_setter_settings(app, frame: ttk.LabelFrame) -> None:
    _configure_aligned_numeric_columns(frame)
    _add_numeric_setting(
        app,
        frame,
        row_index=0,
        pair_index=0,
        label_text="Bit Setter X",
        var=app.bit_setter_x,
        attr_name="bit_setter_x_entry",
        tooltip_text="Machine-coordinate X location of the fixed tool-height sensor / bit setter.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=0,
        pair_index=1,
        label_text="Bit Setter Y",
        var=app.bit_setter_y,
        attr_name="bit_setter_y_entry",
        tooltip_text="Machine-coordinate Y location of the fixed tool-height sensor / bit setter.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=1,
        pair_index=0,
        label_text="Bit Setter Rough Probe Speed",
        var=app.bit_setter_rough_probe_speed,
        attr_name="bit_setter_rough_probe_speed_entry",
        tooltip_text="Initial fixed-sensor coarse seek speed in mm/min for Job Setup and Tool Change.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=1,
        pair_index=1,
        label_text="Bit Setter Fine Probe Speed",
        var=app.bit_setter_fine_probe_speed,
        attr_name="bit_setter_fine_probe_speed_entry",
        tooltip_text="Fine re-probe speed in mm/min for the high-precision fixed-sensor cycle.",
    )
    _add_numeric_setting(
        app,
        frame,
        row_index=2,
        pair_index=0,
        label_text="Bit Setter Probe Dwell (Seconds)",
        var=app.bit_setter_probe_dwell,
        attr_name="bit_setter_probe_dwell_entry",
        tooltip_text="Pause between fine retract and the next exact fixed-sensor probe sample.",
    )


def build_probing_setup_section(app, parent: ttk.Frame, row: int) -> int:
    probing_frame = ttk.LabelFrame(parent, text="Probing & Setup", padding=_ALIGNED_SETTINGS_SECTION_PADDING)
    probing_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    _configure_aligned_numeric_columns(probing_frame)

    probe_start_frame = ttk.Frame(
        probing_frame,
        padding=(_ALIGNED_SETTINGS_NESTED_CONTENT_OFFSET_PX, 0, 0, 0),
    )
    probe_start_frame.grid(row=0, column=0, columnspan=6, sticky="w")
    _configure_aligned_numeric_columns(probe_start_frame)

    _add_numeric_setting(
        app,
        probe_start_frame,
        row_index=0,
        pair_index=0,
        label_text="Probe Z start (machine, mm)",
        var=app.macro_probe_z_location,
        attr_name="macro_probe_z_location_entry",
        tooltip_text="Machine-coordinate Z starting point for fixed-sensor probing in Job Setup and Tool Change (typically -5).",
    )
    _add_numeric_setting(
        app,
        probe_start_frame,
        row_index=0,
        pair_index=1,
        label_text="Probe safety margin (mm)",
        var=app.macro_probe_safety_margin,
        attr_name="macro_probe_safety_margin_entry",
        tooltip_text="Subtracted from the $132-based probe travel calculation used by Job Setup and Tool Change.",
    )

    app.xyz_plate_frame = ttk.LabelFrame(
        probing_frame,
        text="XYZ Plate",
        padding=_ALIGNED_SETTINGS_SECTION_PADDING,
    )
    app.xyz_plate_frame.grid(
        row=1,
        column=0,
        columnspan=6,
        sticky="ew",
        pady=(8, 6),
    )
    _build_xyz_plate_settings(app, app.xyz_plate_frame)

    app.bit_setter_frame = ttk.LabelFrame(
        probing_frame,
        text="Bit Setter",
        padding=_ALIGNED_SETTINGS_SECTION_PADDING,
    )
    app.bit_setter_frame.grid(
        row=2,
        column=0,
        columnspan=6,
        sticky="ew",
        pady=(0, 0),
    )
    _build_bit_setter_settings(app, app.bit_setter_frame)
    return row + 1


def build_macros_section(app, parent: ttk.Frame, row: int) -> int:
    macro_frame = ttk.LabelFrame(parent, text="Macros", padding=8)
    macro_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    macro_frame.grid_columnconfigure(1, weight=1)
    if not hasattr(app, "disable_macro_timeouts"):
        app.disable_macro_timeouts = tk.BooleanVar(master=parent, value=False)
    app.macros_allow_python_check = ttk.Checkbutton(
        macro_frame,
        text="Allow macro scripting (Python/eval)",
        variable=app.macros_allow_python,
    )
    app.macros_allow_python_check.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
    apply_tooltip(
        app.macros_allow_python_check,
        "Disable to allow only plain G-code lines in macros (no scripting or expressions).",
    )
    _aligned_setting_label(macro_frame, text="Line timeout (sec)").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.macro_line_timeout_entry = ttk.Entry(
        macro_frame,
        textvariable=app.macro_line_timeout_sec,
        width=12,
    )
    app.macro_line_timeout_entry.grid(row=1, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.macro_line_timeout_entry, allow_decimal=True)
    ttk.Label(macro_frame, text="0 disables").grid(
        row=1, column=2, sticky="w", padx=(8, 0), pady=4
    )
    apply_tooltip(
        app.macro_line_timeout_entry,
        "Maximum allowed time per macro line in seconds. Set 0 to disable.",
    )

    _aligned_setting_label(macro_frame, text="Total timeout (sec)").grid(
        row=2, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.macro_total_timeout_entry = ttk.Entry(
        macro_frame,
        textvariable=app.macro_total_timeout_sec,
        width=12,
    )
    app.macro_total_timeout_entry.grid(row=2, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.macro_total_timeout_entry, allow_decimal=True)
    ttk.Label(macro_frame, text="0 disables").grid(row=2, column=2, sticky="w", padx=(8, 0), pady=4)
    apply_tooltip(
        app.macro_total_timeout_entry,
        "Maximum allowed time for a full macro run in seconds. Set 0 to disable.",
    )
    app.disable_macro_timeouts_check = ttk.Checkbutton(
        macro_frame,
        text="Disable Macro Timeouts",
        variable=app.disable_macro_timeouts,
    )
    app.disable_macro_timeouts_check.grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(6, 2)
    )
    apply_tooltip(
        app.disable_macro_timeouts_check,
        "When enabled, general macros will wait indefinitely instead of using the normal line/total timeout limits. Operator-assisted Job Setup/Tool Change waits already bypass those limits automatically.",
    )
    ttk.Label(
        macro_frame,
        text="General user macros use finite default timeouts. Protected built-in workflows run independently of those general macro timeout settings.",
        wraplength=560,
        justify="left",
    ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 4))
    app.btn_open_macro_manager = ttk.Button(
        macro_frame,
        text="Open Macro Manager",
        command=app._open_macro_manager,
    )
    app.btn_open_macro_manager.grid(row=5, column=0, sticky="w", pady=(6, 2))
    apply_tooltip(
        app.btn_open_macro_manager,
        "Edit, duplicate, and reorder the 5 file-backed user macro slots from inside the app.",
    )

    ttk.Label(
        macro_frame,
        text="Warning: enabled macros can execute arbitrary Python; disable for plain G-code macros.",
        wraplength=560,
        justify="left",
    ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))
    return row + 1


def build_zeroing_section(app, parent: ttk.Frame, row: int) -> int:
    zeroing_frame = ttk.LabelFrame(parent, text="Zeroing", padding=8)
    zeroing_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
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
    return row + 1


def build_jogging_section(app, parent: ttk.Frame, row: int) -> int:
    jog_frame = ttk.LabelFrame(parent, text="Jogging", padding=8)
    jog_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    jog_frame.grid_columnconfigure(1, weight=1)
    _aligned_setting_label(jog_frame, text="Default jog feed (X/Y)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    jog_xy_row = ttk.Frame(jog_frame)
    jog_xy_row.grid(row=0, column=1, sticky="w", pady=4)
    app.jog_feed_xy_entry = ttk.Entry(jog_xy_row, textvariable=app.jog_feed_xy, width=12)
    app.jog_feed_xy_entry.pack(side="left")
    attach_numeric_keypad(app.jog_feed_xy_entry, allow_decimal=True)
    app.jog_feed_xy_entry.bind("<Return>", app._on_jog_feed_change_xy)
    app.jog_feed_xy_entry.bind("<FocusOut>", app._on_jog_feed_change_xy)
    ttk.Label(jog_xy_row, text="Units: mm/min (in/min when in inches mode)").pack(
        side="left", padx=(8, 0)
    )
    _aligned_setting_label(jog_frame, text="Default jog feed (Z)").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.jog_feed_z_entry = ttk.Entry(jog_frame, textvariable=app.jog_feed_z, width=12)
    app.jog_feed_z_entry.grid(row=1, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.jog_feed_z_entry, allow_decimal=True)
    app.jog_feed_z_entry.bind("<Return>", app._on_jog_feed_change_z)
    app.jog_feed_z_entry.bind("<FocusOut>", app._on_jog_feed_change_z)
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
    app.btn_safe_mode_profile = ttk.Button(
        jog_frame,
        text="Apply safe mode",
        command=app._apply_safe_mode_profile,
    )
    app.btn_safe_mode_profile.grid(row=3, column=0, sticky="w", pady=(4, 0))
    apply_tooltip(
        app.btn_safe_mode_profile,
        "Set conservative jog feeds/steps for first-time setup.",
    )
    ttk.Label(
        jog_frame,
        text="Safe mode sets jog feeds to 1000/200 mm/min and steps to 1.0/0.1 mm.",
        wraplength=560,
        justify="left",
    ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))
    _aligned_setting_label(jog_frame, text="DRO jog smoothing (interpolation)").grid(
        row=4, column=0, sticky="w", padx=(0, 10), pady=(8, 4)
    )
    if not hasattr(app, "jog_dro_smoothing_mode"):
        app.jog_dro_smoothing_mode = tk.StringVar(value=JOG_DRO_SMOOTHING_OFF)
    mode_map = {
        "Off": JOG_DRO_SMOOTHING_OFF,
        "UI jog only": JOG_DRO_SMOOTHING_UI_JOG_ONLY,
        "All jog": JOG_DRO_SMOOTHING_ALL_JOG,
    }
    reverse_mode_map = {value: key for key, value in mode_map.items()}
    current_mode = str(app.jog_dro_smoothing_mode.get() or "").strip().lower()
    if current_mode not in reverse_mode_map:
        current_mode = JOG_DRO_SMOOTHING_OFF
        app.jog_dro_smoothing_mode.set(current_mode)
    app._jog_dro_smoothing_label_var = tk.StringVar(value=reverse_mode_map[current_mode])
    app.jog_dro_smoothing_combo = ttk.Combobox(
        jog_frame,
        textvariable=app._jog_dro_smoothing_label_var,
        values=("Off", "UI jog only", "All jog"),
        state="readonly",
        width=14,
    )
    app.jog_dro_smoothing_combo.grid(row=4, column=1, sticky="w", pady=(8, 4))

    def _sync_jog_dro_smoothing(_event=None) -> None:
        label = str(app._jog_dro_smoothing_label_var.get() or "").strip()
        mode = mode_map.get(label, JOG_DRO_SMOOTHING_OFF)
        app.jog_dro_smoothing_mode.set(mode)

    app.jog_dro_smoothing_combo.bind("<<ComboboxSelected>>", _sync_jog_dro_smoothing)
    apply_tooltip(
        app.jog_dro_smoothing_combo,
        "Controls jog DRO interpolation between status reports: Off, UI jog only, or all jog sources.",
    )
    return row + 1


def build_keyboard_shortcuts_section(app, parent: ttk.Frame, row: int) -> int:
    kb_frame = ttk.LabelFrame(parent, text="Keyboard shortcuts", padding=8)
    kb_frame.grid(row=row, column=0, sticky="nsew", pady=(0, 8))
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
    joystick_btn_row = ttk.Frame(joystick_test_frame)
    joystick_btn_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
    app.btn_refresh_joysticks = ttk.Button(
        joystick_btn_row,
        text="Refresh joystick list",
        command=app._refresh_joystick_test_info,
    )
    app.btn_refresh_joysticks.pack(side="left")
    apply_tooltip(
        app.btn_refresh_joysticks,
        "Rescan for connected joysticks (use after plugging in or swapping controllers).",
    )
    app.btn_toggle_joystick_bindings = ttk.Button(
        joystick_btn_row,
        text="Enable USB Joystick Bindings",
        command=app._toggle_joystick_bindings,
    )
    set_kb_id(app.btn_toggle_joystick_bindings, "toggle_joystick_bindings")
    app.btn_toggle_joystick_bindings.pack(side="left", padx=(8, 0))
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
    joystick_safety_btn_row = ttk.Frame(joystick_test_frame)
    joystick_safety_btn_row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
    app.btn_set_joystick_safety = ttk.Button(
        joystick_safety_btn_row,
        text="Set Safety Button",
        command=app._start_joystick_safety_capture,
    )
    app.btn_set_joystick_safety.pack(side="left")
    app.btn_clear_joystick_safety = ttk.Button(
        joystick_safety_btn_row,
        text="Clear Safety Button",
        command=app._clear_joystick_safety_binding,
    )
    app.btn_clear_joystick_safety.pack(side="left", padx=(8, 0))
    apply_tooltip(app.btn_set_joystick_safety, "Capture a joystick button to use as a safety hold.")
    apply_tooltip(app.btn_clear_joystick_safety, "Clear the safety button binding.")
    app._refresh_joystick_safety_display()

    app.stop_hold_focus_check = ttk.Checkbutton(
        joystick_test_frame,
        text="Stop joystick hold when app loses focus",
        variable=app.stop_hold_on_focus_loss,
    )
    app.stop_hold_focus_check.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
    apply_tooltip(
        app.stop_hold_focus_check,
        "Stop held jog actions if focus leaves the app window.",
    )
    if not hasattr(app, "joystick_hold_miss_limit"):
        app.joystick_hold_miss_limit = tk.IntVar(value=2)
    ttk.Label(joystick_test_frame, text="Hold release sensitivity").grid(
        row=8, column=0, sticky="w", pady=(8, 0)
    )
    app.joystick_hold_miss_limit_entry = ttk.Entry(
        joystick_test_frame,
        textvariable=app.joystick_hold_miss_limit,
        width=6,
    )
    app.joystick_hold_miss_limit_entry.grid(
        row=8, column=1, sticky="w", pady=(8, 0), padx=(8, 0)
    )
    attach_numeric_keypad(app.joystick_hold_miss_limit_entry, allow_decimal=False)
    apply_tooltip(
        app.joystick_hold_miss_limit_entry,
        "Number of consecutive missed joystick polls before ending a held jog "
        "(1=most sensitive, 8=most tolerant).",
    )
    ttk.Label(
        joystick_test_frame,
        text="Higher values can reduce accidental early stop on noisy controllers.",
        wraplength=520,
        justify="left",
    ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))

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
    # The controls surface is built lazily. Sync it to the already-loaded
    # runtime binding state immediately so the enabled checkbox, binding table,
    # and joystick toggle text all reflect reality without requiring a manual
    # toggle cycle.
    app._apply_keyboard_bindings()
    app._refresh_joystick_toggle_text()
    return row + 1


def build_kasa_plug_section(app, parent: ttk.Frame, row: int) -> int:
    if not sys.platform.startswith("linux"):
        return row
    kasa_frame = ttk.LabelFrame(parent, text="Kasa Plug", padding=8)
    kasa_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    kasa_frame.grid_columnconfigure(1, weight=1)

    app.kasa_enable_check = ttk.Checkbutton(
        kasa_frame,
        text="Enable Kasa Plug control",
        variable=app.kasa_enabled,
        command=app._on_kasa_master_change,
    )
    app.kasa_enable_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
    apply_tooltip(
        app.kasa_enable_check,
        "Master switch for Kasa outlet control. Disabled means no background Kasa commands.",
    )

    discovery_row = ttk.Frame(kasa_frame)
    discovery_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
    discovery_row.grid_columnconfigure(1, weight=1)
    app.btn_kasa_discover = ttk.Button(
        discovery_row,
        text="Discover",
        command=app._discover_kasa_devices,
    )
    app.btn_kasa_discover.grid(row=0, column=0, sticky="w")
    apply_tooltip(app.btn_kasa_discover, "Scan LAN for Kasa devices.")
    app.kasa_device_combo = ttk.Combobox(
        discovery_row,
        textvariable=app.kasa_device_choice,
        state="readonly",
        width=48,
    )
    app.kasa_device_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
    app.kasa_device_combo.bind("<<ComboboxSelected>>", app._on_kasa_device_selected)
    apply_tooltip(
        app.kasa_device_combo,
        "Choose one physical Kasa device (requires at least two outlets).",
    )

    app.kasa_outlet_info_label = ttk.Label(
        kasa_frame,
        textvariable=app.kasa_outlet_info_var,
        justify="left",
        wraplength=560,
    )
    app.kasa_outlet_info_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 6))

    app.kasa_status_line_label = ttk.Label(
        kasa_frame,
        textvariable=app.kasa_status_line_var,
        justify="left",
        wraplength=560,
    )
    app.kasa_status_line_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 6))
    apply_tooltip(
        app.kasa_status_line_label,
        "Read-only Kasa status (enabled, device, and mapped outlet states).",
    )

    vacuum_row = ttk.Frame(kasa_frame)
    vacuum_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 4))
    vacuum_row.grid_columnconfigure(2, weight=1)
    app.vacuum_check = ttk.Checkbutton(
        vacuum_row,
        text="Vacuum",
        variable=app.vacuum_enabled,
        command=lambda: app._on_kasa_mapping_change("vacuum_enable"),
    )
    app.vacuum_check.grid(row=0, column=0, sticky="w")
    ttk.Label(vacuum_row, text="Outlet").grid(row=0, column=1, sticky="w", padx=(12, 6))
    app.vacuum_outlet_combo = ttk.Combobox(
        vacuum_row,
        textvariable=app.vacuum_outlet_label,
        state="readonly",
        values=_KASA_OUTLET_OPTIONS,
        width=12,
    )
    app.vacuum_outlet_combo.grid(row=0, column=2, sticky="w")
    app.vacuum_outlet_combo.bind(
        "<<ComboboxSelected>>",
        lambda _evt: (
            app.vacuum_outlet.set(
                2 if str(app.vacuum_outlet_label.get() or "").strip().endswith("2") else 1
            ),
            app._on_kasa_mapping_change("vacuum"),
        ),
    )
    apply_tooltip(app.vacuum_outlet_combo, "Select which outlet controls Vacuum.")
    ttk.Label(vacuum_row, text="Off delay (sec)").grid(
        row=1, column=1, sticky="w", padx=(12, 6), pady=(4, 0)
    )
    app.vacuum_off_delay_entry = ttk.Entry(
        vacuum_row,
        textvariable=app.vacuum_off_delay_sec,
        width=12,
    )
    app.vacuum_off_delay_entry.grid(row=1, column=2, sticky="w", pady=(4, 0))
    attach_numeric_keypad(app.vacuum_off_delay_entry, allow_decimal=True)
    apply_tooltip(
        app.vacuum_off_delay_entry,
        "Delay VACUUM_OFF by this many seconds so dust collection can clear remaining debris.",
    )

    light_row = ttk.Frame(kasa_frame)
    light_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 4))
    light_row.grid_columnconfigure(2, weight=1)
    app.light_check = ttk.Checkbutton(
        light_row,
        text="Spindle Light",
        variable=app.light_enabled,
        command=lambda: app._on_kasa_mapping_change("light_enable"),
    )
    app.light_check.grid(row=0, column=0, sticky="w")
    ttk.Label(light_row, text="Outlet").grid(row=0, column=1, sticky="w", padx=(12, 6))
    app.light_outlet_combo = ttk.Combobox(
        light_row,
        textvariable=app.light_outlet_label,
        state="readonly",
        values=_KASA_OUTLET_OPTIONS,
        width=12,
    )
    app.light_outlet_combo.grid(row=0, column=2, sticky="w")
    app.light_outlet_combo.bind(
        "<<ComboboxSelected>>",
        lambda _evt: (
            app.light_outlet.set(
                2 if str(app.light_outlet_label.get() or "").strip().endswith("2") else 1
            ),
            app._on_kasa_mapping_change("light"),
        ),
    )
    apply_tooltip(app.light_outlet_combo, "Select which outlet controls Spindle Light.")

    app.kasa_validation_label = ttk.Label(
        kasa_frame,
        textvariable=app.kasa_validation_var,
        justify="left",
        wraplength=560,
        foreground="#b00020",
    )
    app.kasa_validation_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 6))

    test_frame = ttk.LabelFrame(kasa_frame, text="Test Outlets", padding=8)
    test_frame.grid(row=7, column=0, columnspan=2, sticky="ew")
    test_frame.grid_columnconfigure(1, weight=1)

    app.btn_kasa_refresh_outlets = ttk.Button(
        test_frame,
        text="Refresh Outlet List",
        command=app._refresh_kasa_outlet_list,
    )
    app.btn_kasa_refresh_outlets.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
    apply_tooltip(
        app.btn_kasa_refresh_outlets,
        "Re-read outlets from the selected Kasa device.",
    )

    outlet1_row = ttk.Frame(test_frame)
    outlet1_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 2))
    ttk.Label(outlet1_row, text="Outlet 1").pack(side="left")
    app.btn_kasa_outlet1_on = ttk.Button(
        outlet1_row,
        text="ON",
        command=lambda: app._test_kasa_outlet(1, True),
        width=6,
    )
    app.btn_kasa_outlet1_on.pack(side="left", padx=(8, 4))
    app.btn_kasa_outlet1_off = ttk.Button(
        outlet1_row,
        text="OFF",
        command=lambda: app._test_kasa_outlet(1, False),
        width=6,
    )
    app.btn_kasa_outlet1_off.pack(side="left")
    app.kasa_outlet_1_status_label = ttk.Label(
        test_frame,
        textvariable=app.kasa_outlet_1_status,
        justify="left",
        wraplength=520,
    )
    app.kasa_outlet_1_status_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))

    outlet2_row = ttk.Frame(test_frame)
    outlet2_row.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 2))
    ttk.Label(outlet2_row, text="Outlet 2").pack(side="left")
    app.btn_kasa_outlet2_on = ttk.Button(
        outlet2_row,
        text="ON",
        command=lambda: app._test_kasa_outlet(2, True),
        width=6,
    )
    app.btn_kasa_outlet2_on.pack(side="left", padx=(8, 4))
    app.btn_kasa_outlet2_off = ttk.Button(
        outlet2_row,
        text="OFF",
        command=lambda: app._test_kasa_outlet(2, False),
        width=6,
    )
    app.btn_kasa_outlet2_off.pack(side="left")
    app.kasa_outlet_2_status_label = ttk.Label(
        test_frame,
        textvariable=app.kasa_outlet_2_status,
        justify="left",
        wraplength=520,
    )
    app.kasa_outlet_2_status_label.grid(row=4, column=0, columnspan=2, sticky="w")

    app._on_kasa_mapping_change(None)
    app._refresh_kasa_controls_state()
    return row + 1


