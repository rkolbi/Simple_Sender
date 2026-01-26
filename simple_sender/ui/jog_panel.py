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

from simple_sender.ui.icons import ICON_HOME, ICON_HOLD, ICON_RESUME, icon_label
from simple_sender.ui.widgets import (
    StopSignButton,
    _resolve_widget_bg,
    apply_tooltip,
    attach_log_gcode,
    set_kb_id,
)
from simple_sender.utils.constants import (
    JOG_FEED_EPSILON,
    JOG_PANEL_ALL_STOP_OFFSET_IN,
    JOG_PANEL_ALL_STOP_SIZE,
    JOG_STEP_XY_VALUES,
    JOG_STEP_Z_VALUES,
)

def build_jog_panel(app, parent):
    top = ttk.Frame(parent)
    top.pack(side="top", fill="x")

    # Left: DRO
    mpos = ttk.Labelframe(top, text="Machine Position (MPos)", padding=8)
    mpos.pack(side="left", fill="y", padx=(0, 10))

    app._dro_value_row(mpos, "X", app.mpos_x)
    app._dro_value_row(mpos, "Y", app.mpos_y)
    app._dro_value_row(mpos, "Z", app.mpos_z)
    app.btn_home_mpos = ttk.Button(
        mpos,
        text=icon_label(ICON_HOME, "Home"),
        style=getattr(app, "home_button_style", app.icon_button_style),
        command=app._start_homing,
    )
    set_kb_id(app.btn_home_mpos, "home")
    app.btn_home_mpos.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_home_mpos)
    apply_tooltip(app.btn_home_mpos, "Run the homing cycle.")
    attach_log_gcode(app.btn_home_mpos, "$H")
    app.btn_unit_toggle = ttk.Button(
        mpos,
        text=app._unit_toggle_label(),
        command=app._toggle_unit_mode,
    )
    set_kb_id(app.btn_unit_toggle, "unit_toggle")
    app.btn_unit_toggle.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_unit_toggle)
    app._offline_controls.add(app.btn_unit_toggle)
    apply_tooltip(
        app.btn_unit_toggle,
        "Toggle modal units (G20/G21). Blue text means report units are tracked ($13).",
    )
    app._update_unit_toggle_display()
    app.btn_hold_mpos = ttk.Button(
        mpos,
        text=icon_label(ICON_HOLD, "Hold"),
        command=lambda: app._run_if_connected(app.grbl.hold),
    )
    set_kb_id(app.btn_hold_mpos, "feed_hold")
    app.btn_hold_mpos.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_hold_mpos)
    apply_tooltip(app.btn_hold_mpos, "Feed hold.")
    attach_log_gcode(app.btn_hold_mpos, "!")
    app.btn_resume_mpos = ttk.Button(
        mpos,
        text=icon_label(ICON_RESUME, "Resume"),
        command=lambda: app._run_if_connected(app.grbl.resume),
    )
    set_kb_id(app.btn_resume_mpos, "feed_resume")
    app.btn_resume_mpos.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_resume_mpos)
    apply_tooltip(app.btn_resume_mpos, "Resume after hold.")
    attach_log_gcode(app.btn_resume_mpos, "~")

    dro = ttk.Labelframe(top, text="Work Position (WPos)", padding=8)
    dro.pack(side="left", fill="y", padx=(0, 10))

    app.btn_zero_x = app._dro_row(dro, "X", app.wpos_x, app.zero_x)
    app.btn_zero_y = app._dro_row(dro, "Y", app.wpos_y, app.zero_y)
    app.btn_zero_z = app._dro_row(dro, "Z", app.wpos_z, app.zero_z)
    app._manual_controls.extend([app.btn_zero_x, app.btn_zero_y, app.btn_zero_z])

    btns = ttk.Frame(dro)
    btns.pack(fill="x", pady=(6, 0))
    app.btn_zero_all = ttk.Button(btns, text="Zero All", command=app.zero_all)
    set_kb_id(app.btn_zero_all, "zero_all")
    app.btn_zero_all.pack(side="left", expand=True, fill="x")
    app._manual_controls.append(app.btn_zero_all)
    app._refresh_zeroing_ui()
    app.btn_goto_zero = ttk.Button(btns, text="Goto Zero", command=app.goto_zero)
    set_kb_id(app.btn_goto_zero, "goto_zero")
    app.btn_goto_zero.pack(side="left", expand=True, fill="x", padx=(6, 0))
    app._manual_controls.append(app.btn_goto_zero)
    apply_tooltip(app.btn_goto_zero, "Rapid move to WCS X0 Y0.")
    attach_log_gcode(app.btn_goto_zero, "G0 X0 Y0")

    style = ttk.Style()
    sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background") or "#a0a0a0"
    bg_color = _resolve_widget_bg(dro)
    try:
        r1, g1, b1 = dro.winfo_rgb(sep_color)
        r2, g2, b2 = dro.winfo_rgb(bg_color)
        t = 0.65
        sep_color = "#{:02x}{:02x}{:02x}".format(
            int((r1 + (r2 - r1) * t) / 256),
            int((g1 + (g2 - g1) * t) / 256),
            int((b1 + (b2 - b1) * t) / 256),
        )
    except tk.TclError:
        sep_color = "#dcdcdc"

    tk.Frame(dro, height=1, bg=sep_color, bd=0, highlightthickness=0).pack(fill="x", pady=(8, 6))
    macro_left = ttk.Frame(dro)
    macro_left.pack(fill="x")

    # Center: Jog
    jog = ttk.Labelframe(top, text="Jog", padding=8)
    jog.pack(side="left", fill="both", expand=True, padx=(10, 10))

    pad = ttk.Frame(jog)
    pad.pack(side="left", padx=(0, 12))

    def _jog_feed_for_move(dx, dy, dz) -> float:
        # Use Z feed only for pure Z moves; otherwise use XY feed.
        if abs(dz) > 0 and abs(dx) < JOG_FEED_EPSILON and abs(dy) < JOG_FEED_EPSILON:
            return float(app.jog_feed_z.get())
        return float(app.jog_feed_xy.get())

    def j(dx, dy, dz):
        if not app.grbl.is_connected():
            app.streaming_controller.log("Jog ignored - GRBL is not connected.")
            return
        feed = _jog_feed_for_move(dx, dy, dz)
        app.grbl.jog(dx, dy, dz, feed, app.unit_mode.get())

    def jog_cmd(dx, dy, dz):
        feed = _jog_feed_for_move(dx, dy, dz)
        gunit = "G21" if app.unit_mode.get() == "mm" else "G20"
        return f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"

    # 3x3 pad for XY
    app.btn_jog_y_plus = ttk.Button(pad, text="Y+", width=6, command=lambda: j(0, app.step_xy.get(), 0))
    set_kb_id(app.btn_jog_y_plus, "jog_y_plus")
    app.btn_jog_y_plus.grid(row=0, column=1, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_y_plus, lambda: jog_cmd(0, app.step_xy.get(), 0))
    app.btn_jog_x_minus = ttk.Button(pad, text="X-", width=6, command=lambda: j(-app.step_xy.get(), 0, 0))
    set_kb_id(app.btn_jog_x_minus, "jog_x_minus")
    app.btn_jog_x_minus.grid(row=1, column=0, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_x_minus, lambda: jog_cmd(-app.step_xy.get(), 0, 0))
    app.btn_jog_x_plus = ttk.Button(pad, text="X+", width=6, command=lambda: j(app.step_xy.get(), 0, 0))
    set_kb_id(app.btn_jog_x_plus, "jog_x_plus")
    app.btn_jog_x_plus.grid(row=1, column=2, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_x_plus, lambda: jog_cmd(app.step_xy.get(), 0, 0))
    app.btn_jog_y_minus = ttk.Button(pad, text="Y-", width=6, command=lambda: j(0, -app.step_xy.get(), 0))
    set_kb_id(app.btn_jog_y_minus, "jog_y_minus")
    app.btn_jog_y_minus.grid(row=2, column=1, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_y_minus, lambda: jog_cmd(0, -app.step_xy.get(), 0))
    apply_tooltip(app.btn_jog_y_plus, "Jog +Y by the selected step.")
    apply_tooltip(app.btn_jog_y_minus, "Jog -Y by the selected step.")
    apply_tooltip(app.btn_jog_x_minus, "Jog -X by the selected step.")
    apply_tooltip(app.btn_jog_x_plus, "Jog +X by the selected step.")

    style = ttk.Style()
    sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background")
    pad_bg = style.lookup("TFrame", "background") or app.cget("bg")
    style.configure("JogSeparator.TSeparator", background=sep_color)
    # Z
    def cancel_jog():
        app._stop_joystick_hold()
        app.grbl.jog_cancel()
        try:
            app.grbl.cancel_pending_jogs()
        except Exception:
            pass

    app.btn_jog_cancel = StopSignButton(
        pad,
        text="JOG\nSTOP",
        fill="#f2b200",
        text_color="#000000",
        command=cancel_jog,
        bg=pad_bg,
    )
    set_kb_id(app.btn_jog_cancel, "jog_stop")
    app.btn_jog_cancel.grid(row=0, column=3, rowspan=3, padx=(6, 2), pady=2, sticky="ns")
    apply_tooltip(app.btn_jog_cancel, "Cancel an active jog ($J cancel).")
    attach_log_gcode(app.btn_jog_cancel, "RT 0x85")

    sep = ttk.Separator(pad, orient="vertical", style="JogSeparator.TSeparator")
    # Align the jog tab separator with the macro panel spacing by nudging it right.
    sep.grid(row=0, column=4, rowspan=5, sticky="ns", padx=(10, 2))

    app.btn_jog_z_plus = ttk.Button(pad, text="Z+", width=6, command=lambda: j(0, 0, app.step_z.get()))
    set_kb_id(app.btn_jog_z_plus, "jog_z_plus")
    app.btn_jog_z_plus.grid(row=0, column=5, padx=(6, 2), pady=2)
    app.tool_reference_label = ttk.Label(
        pad,
        textvariable=app.tool_reference_var,
        width=18,
        anchor="center",
    )
    app.tool_reference_label.grid(row=1, column=5, padx=(6, 2), pady=2)
    apply_tooltip(app.tool_reference_label, "Tool reference height.")
    app.btn_jog_z_minus = ttk.Button(pad, text="Z-", width=6, command=lambda: j(0, 0, -app.step_z.get()))
    set_kb_id(app.btn_jog_z_minus, "jog_z_minus")
    app.btn_jog_z_minus.grid(row=2, column=5, padx=(6, 2), pady=2)
    apply_tooltip(app.btn_jog_z_plus, "Jog +Z by the selected step.")
    apply_tooltip(app.btn_jog_z_minus, "Jog -Z by the selected step.")
    attach_log_gcode(app.btn_jog_z_plus, lambda: jog_cmd(0, 0, app.step_z.get()))
    attach_log_gcode(app.btn_jog_z_minus, lambda: jog_cmd(0, 0, -app.step_z.get()))

    all_stop_size = JOG_PANEL_ALL_STOP_SIZE
    app._all_stop_offset_px = int(app.winfo_fpixels(f"{JOG_PANEL_ALL_STOP_OFFSET_IN}i"))
    app._all_stop_slot = ttk.Frame(pad, width=all_stop_size, height=all_stop_size)
    app._all_stop_slot.grid(row=0, column=6, rowspan=3, padx=(6, 0), pady=2, sticky="ns")
    app._all_stop_slot.grid_propagate(False)
    app.btn_all_stop = StopSignButton(
        pad,
        text="ALL\nSTOP",
        fill="#d83b2d",
        text_color="#ffffff",
        command=app._all_stop_action,
        bg=pad_bg,
        size=all_stop_size,
    )
    set_kb_id(app.btn_all_stop, "all_stop")
    apply_tooltip(app.btn_all_stop, "Immediate stop (behavior from App Settings).")
    attach_log_gcode(app.btn_all_stop, app._all_stop_gcode_label)
    pad.bind("<Configure>", app._position_all_stop_offset, add="+")
    app.after(0, app._position_all_stop_offset)

    app._manual_controls.extend([
        app.btn_jog_y_plus,
        app.btn_jog_x_minus,
        app.btn_jog_x_plus,
        app.btn_jog_y_minus,
        app.btn_jog_z_plus,
        app.btn_jog_z_minus,
    ])
    app._manual_controls.append(app.btn_jog_cancel)
    app._manual_controls.append(app.btn_all_stop)

    spacer = ttk.Frame(pad, height=6)
    spacer.grid(row=3, column=0, columnspan=6)
    steps_spacer = ttk.Frame(pad, height=24)
    steps_spacer.grid(row=4, column=0, columnspan=6)

    xy_steps = ttk.Frame(pad)
    xy_steps.grid(row=5, column=0, columnspan=4, pady=(0, 0), sticky="ew")
    xy_steps.grid_columnconfigure(1, weight=1)
    app._xy_step_values = list(JOG_STEP_XY_VALUES)

    def _xy_step_index_for(value: float) -> int:
        try:
            val = float(value)
        except Exception:
            val = app._xy_step_values[0]
        return min(range(len(app._xy_step_values)), key=lambda i: abs(app._xy_step_values[i] - val))

    app._xy_step_index = tk.IntVar(value=_xy_step_index_for(app.step_xy.get()))
    ttk.Label(xy_steps, text="XY Step").grid(row=0, column=0, sticky="w", padx=(0, 6))

    def _step_xy_delta(delta: int) -> None:
        idx = int(app._xy_step_index.get()) + delta
        idx = max(0, min(len(app._xy_step_values) - 1, idx))
        app._set_step_xy(app._xy_step_values[idx])

    app._xy_step_minus = ttk.Button(
        xy_steps,
        text="-",
        width=4,
        command=lambda: _step_xy_delta(-1),
    )
    app._xy_step_minus.grid(row=0, column=1, sticky="w")
    apply_tooltip(app._xy_step_minus, "Decrease XY step.")

    app._xy_step_progress = ttk.Progressbar(
        xy_steps,
        orient="horizontal",
        mode="determinate",
        maximum=max(len(app._xy_step_values) - 1, 1),
        variable=app._xy_step_index,
        length=160,
    )
    app._xy_step_progress.grid(row=0, column=2, sticky="ew", padx=(6, 6))

    app._xy_step_plus = ttk.Button(
        xy_steps,
        text="+",
        width=4,
        command=lambda: _step_xy_delta(1),
    )
    app._xy_step_plus.grid(row=0, column=3, sticky="e")
    apply_tooltip(app._xy_step_plus, "Increase XY step.")

    initial_idx = int(app._xy_step_index.get())
    initial_idx = max(0, min(len(app._xy_step_values) - 1, initial_idx))
    app._xy_step_value_label = ttk.Label(
        xy_steps,
        text=f"{app._xy_step_values[initial_idx]:g}",
        width=6,
        anchor="e",
    )
    app._xy_step_value_label.grid(row=0, column=4, sticky="e", padx=(6, 0))

    z_steps = ttk.Frame(pad)
    z_steps.grid(row=5, column=5, padx=(6, 0), pady=(0, 0), sticky="ew")
    z_steps.grid_columnconfigure(1, weight=1)
    app._z_step_values = list(JOG_STEP_Z_VALUES)

    def _z_step_index_for(value: float) -> int:
        try:
            val = float(value)
        except Exception:
            val = app._z_step_values[0]
        return min(range(len(app._z_step_values)), key=lambda i: abs(app._z_step_values[i] - val))

    app._z_step_index = tk.IntVar(value=_z_step_index_for(app.step_z.get()))
    ttk.Label(z_steps, text="Z Step").grid(row=0, column=0, sticky="w", padx=(0, 6))

    def _step_z_delta(delta: int) -> None:
        idx = int(app._z_step_index.get()) + delta
        idx = max(0, min(len(app._z_step_values) - 1, idx))
        app._set_step_z(app._z_step_values[idx])

    app._z_step_minus = ttk.Button(
        z_steps,
        text="-",
        width=4,
        command=lambda: _step_z_delta(-1),
    )
    app._z_step_minus.grid(row=0, column=1, sticky="w")
    apply_tooltip(app._z_step_minus, "Decrease Z step.")

    app._z_step_progress = ttk.Progressbar(
        z_steps,
        orient="horizontal",
        mode="determinate",
        maximum=max(len(app._z_step_values) - 1, 1),
        variable=app._z_step_index,
        length=160,
    )
    app._z_step_progress.grid(row=0, column=2, sticky="ew", padx=(6, 6))

    app._z_step_plus = ttk.Button(
        z_steps,
        text="+",
        width=4,
        command=lambda: _step_z_delta(1),
    )
    app._z_step_plus.grid(row=0, column=3, sticky="e")
    apply_tooltip(app._z_step_plus, "Increase Z step.")

    initial_z_idx = int(app._z_step_index.get())
    initial_z_idx = max(0, min(len(app._z_step_values) - 1, initial_z_idx))
    app._z_step_value_label = ttk.Label(
        z_steps,
        text=f"{app._z_step_values[initial_z_idx]:g}",
        width=6,
        anchor="e",
    )
    app._z_step_value_label.grid(row=0, column=4, sticky="e", padx=(6, 0))

    app._manual_controls.extend([app._xy_step_minus, app._xy_step_plus])
    app._manual_controls.extend([app._z_step_minus, app._z_step_plus])
    app._offline_controls.update([app._xy_step_minus, app._xy_step_plus, app._z_step_minus, app._z_step_plus])
    app._set_step_xy(app.step_xy.get())
    app._set_step_z(app.step_z.get())

    macro_spacer = ttk.Frame(pad, height=28)
    macro_spacer.grid(row=6, column=0, columnspan=6, sticky="ew")
    macro_right = ttk.Frame(pad)
    macro_right.grid(row=7, column=0, columnspan=6, pady=(6, 0), sticky="ew")

    app.macro_panel.attach_frames(macro_left, macro_right)

    app._set_unit_mode(app.unit_mode.get())

