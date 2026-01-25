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

from tkinter import ttk

from simple_sender.utils.constants import CURRENT_LINE_CHOICES
from simple_sender.ui.widgets import apply_tooltip, attach_numeric_keypad, set_kb_id

def build_macros_section(app, parent: ttk.Frame, row: int) -> int:
    macro_frame = ttk.LabelFrame(parent, text="Macros", padding=8)
    macro_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
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
    ttk.Label(jog_frame, text="Default jog feed (X/Y)").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.jog_feed_xy_entry = ttk.Entry(jog_frame, textvariable=app.jog_feed_xy, width=12)
    app.jog_feed_xy_entry.grid(row=0, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.jog_feed_xy_entry, allow_decimal=True)
    app.jog_feed_xy_entry.bind("<Return>", app._on_jog_feed_change_xy)
    app.jog_feed_xy_entry.bind("<FocusOut>", app._on_jog_feed_change_xy)
    ttk.Label(jog_frame, text="Default jog feed (Z)").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=4
    )
    app.jog_feed_z_entry = ttk.Entry(jog_frame, textvariable=app.jog_feed_z, width=12)
    app.jog_feed_z_entry.grid(row=1, column=1, sticky="w", pady=4)
    attach_numeric_keypad(app.jog_feed_z_entry, allow_decimal=True)
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
    return row + 1


def build_gcode_view_section(app, parent: ttk.Frame, row: int) -> int:
    view_frame = ttk.LabelFrame(parent, text="G-code view", padding=8)
    view_frame.grid(row=row, column=0, sticky="ew")
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
    return row + 1


