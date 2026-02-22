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
import logging
from tkinter import ttk

from simple_sender.ui.widgets import (
    StopSignButton,
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

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
JOG_STOP_POSITION_RETRY_MS = 50


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _bind_position_column_sync(app, align) -> None:
    def _sync_pos_column_widths(_event=None):
        try:
            mpos_bbox = align.grid_bbox(0, 1)
            wpos_bbox = align.grid_bbox(2, 1)
        except Exception as exc:
            _log_suppressed("Failed reading MPos/WPos column geometry", exc)
            return
        if not mpos_bbox or not wpos_bbox:
            return
        try:
            mpos_w = int(mpos_bbox[2])
            wpos_w = int(wpos_bbox[2])
        except Exception as exc:
            _log_suppressed("Failed converting MPos/WPos column widths", exc)
            return
        target = max(mpos_w, wpos_w)
        if target <= 0:
            return
        align.grid_columnconfigure(0, minsize=target)
        align.grid_columnconfigure(2, minsize=target)

    align.bind("<Configure>", _sync_pos_column_widths, add="+")
    app.after(0, _sync_pos_column_widths)


def _bind_jog_separator_position(app, align, sep_jog_line, xy_steps, z_steps) -> None:
    def _position_jog_separator(_event=None):
        try:
            height = int(align.winfo_height())
        except Exception as exc:
            _log_suppressed("Failed reading jog separator container height", exc)
            height = 0
        if height <= 0:
            return

        cx = None
        try:
            if hasattr(app, "_xy_step_plus") and hasattr(app, "_z_step_minus"):
                x1 = xy_steps.winfo_x() + app._xy_step_plus.winfo_x() + app._xy_step_plus.winfo_width() // 2
                x2 = z_steps.winfo_x() + app._z_step_minus.winfo_x() + app._z_step_minus.winfo_width() // 2
                if x1 > 0 and x2 > 0:
                    cx = (x1 + x2) // 2
        except Exception as exc:
            _log_suppressed("Failed deriving jog separator center from step controls", exc)
            cx = None

        if cx is None:
            try:
                bbox = align.grid_bbox(8, 1)
            except Exception as exc:
                _log_suppressed("Failed reading jog separator grid bbox", exc)
                return
            if not bbox:
                return
            x, _y, w, _h = bbox
            cx = x + w // 2

        sep_jog_line.place(x=cx, y=0, height=height)

    align.bind("<Configure>", _position_jog_separator, add="+")
    app.after(0, _position_jog_separator)


def _build_step_controls(app, align, z_jog_left_pad: int):
    STEP_BAR_LENGTH = 144
    xy_steps = ttk.Frame(align)
    xy_steps.grid(row=4, column=4, columnspan=3, pady=(6, 0), sticky="new")
    xy_steps.grid_columnconfigure(2, weight=1, minsize=STEP_BAR_LENGTH)
    app._xy_step_values = list(JOG_STEP_XY_VALUES)

    def _xy_step_index_for(value: float) -> int:
        try:
            val = float(value)
        except Exception as exc:
            _log_suppressed("Invalid XY step value; falling back to default", exc)
            val = app._xy_step_values[0]
        return min(range(len(app._xy_step_values)), key=lambda i: abs(app._xy_step_values[i] - val))

    app._xy_step_index = tk.IntVar(value=_xy_step_index_for(app.step_xy.get()))

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
        length=STEP_BAR_LENGTH,
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
        anchor="center",
    )
    app._xy_step_value_label.grid(row=1, column=2, sticky="n", pady=(2, 0))

    z_steps = ttk.Frame(align)
    z_steps.grid(row=4, column=9, padx=(z_jog_left_pad, 0), pady=(6, 0), sticky="new")
    z_steps.grid_columnconfigure(2, weight=1, minsize=STEP_BAR_LENGTH)
    app._z_step_values = list(JOG_STEP_Z_VALUES)

    def _z_step_index_for(value: float) -> int:
        try:
            val = float(value)
        except Exception as exc:
            _log_suppressed("Invalid Z step value; falling back to default", exc)
            val = app._z_step_values[0]
        return min(range(len(app._z_step_values)), key=lambda i: abs(app._z_step_values[i] - val))

    app._z_step_index = tk.IntVar(value=_z_step_index_for(app.step_z.get()))

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
        length=STEP_BAR_LENGTH,
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
        anchor="center",
    )
    app._z_step_value_label.grid(row=1, column=2, sticky="n", pady=(2, 0))

    def _ensure_z_steps_width():
        try:
            req = int(z_steps.winfo_reqwidth())
        except Exception as exc:
            _log_suppressed("Failed reading requested width for Z step controls", exc)
            return
        if req > 0:
            align.grid_columnconfigure(9, minsize=req)
        try:
            bbox = align.grid_bbox(9, 0)
            col_width = int(bbox[2]) if bbox else 0
        except Exception as exc:
            _log_suppressed("Failed reading Z step column width", exc)
            col_width = 0
        if col_width > 0:
            padx = max(0, (col_width - 48) // 2)
            app.btn_jog_z_plus.grid_configure(padx=padx)
            app.tool_reference_label.grid_configure(padx=padx)
            app.btn_jog_z_minus.grid_configure(padx=padx)

    app.after(0, _ensure_z_steps_width)

    def _ensure_xy_steps_width():
        try:
            req = int(xy_steps.winfo_reqwidth())
        except Exception as exc:
            _log_suppressed("Failed reading requested width for XY step controls", exc)
            return
        if req > 0:
            per = (req + 2) // 3
            for col in (4, 5, 6):
                align.grid_columnconfigure(col, minsize=per)
        try:
            bbox = align.grid_bbox(4, 0)
            bbox_w = int(bbox[2]) if bbox else 0
        except Exception as exc:
            _log_suppressed("Failed reading XY jog column width", exc)
            bbox_w = 0
        if bbox_w > 0:
            pad = max(0, (bbox_w - 48) // 2)
            app.btn_jog_x_minus.grid_configure(padx=pad)
            app.btn_jog_y_plus.grid_configure(padx=pad)
            app.btn_jog_x_plus.grid_configure(padx=pad)
            app.btn_jog_y_minus.grid_configure(padx=pad)

    app.after(0, _ensure_xy_steps_width)
    return xy_steps, z_steps


def _bind_jog_stop_position(app, align, z_steps) -> None:
    def _position_jog_stop(_event=None):
        btn = getattr(app, "btn_jog_cancel", None)
        if btn is None:
            return
        try:
            if not btn.winfo_exists():
                return
        except tk.TclError:
            return

        gap = 48
        x = None
        try:
            if z_steps.winfo_ismapped():
                x = z_steps.winfo_rootx() + z_steps.winfo_width() + gap
        except Exception as exc:
            _log_suppressed("Failed deriving jog stop X from Z steps", exc)
            x = None
        if x is None:
            try:
                if app.btn_jog_z_minus.winfo_ismapped():
                    x = app.btn_jog_z_minus.winfo_rootx() + app.btn_jog_z_minus.winfo_width() + gap
            except Exception as exc:
                _log_suppressed("Failed deriving jog stop X from Z- button", exc)
                x = None

        if x is None:
            app.after(JOG_STOP_POSITION_RETRY_MS, _position_jog_stop)
            return

        try:
            all_btn = app.btn_all_stop
            if all_btn.winfo_ismapped():
                max_x = all_btn.winfo_rootx() - btn.winfo_width() - gap
                if x > max_x:
                    x = max_x
        except Exception as exc:
            _log_suppressed("Failed clamping jog stop X against all-stop button", exc)

        y = None
        anchor = "n"
        try:
            all_btn = app.btn_all_stop
            if all_btn.winfo_ismapped():
                y = all_btn.winfo_rooty()
            else:
                slot = getattr(app, "_all_stop_slot", None)
                if slot is not None and slot.winfo_ismapped():
                    y = slot.winfo_rooty()
        except Exception as exc:
            _log_suppressed("Failed deriving jog stop Y from all-stop controls", exc)
            y = None

        if y is None:
            try:
                y1 = app.btn_jog_y_plus.winfo_rooty() + app.btn_jog_y_plus.winfo_height() // 2
                y2 = app.btn_jog_y_minus.winfo_rooty() + app.btn_jog_y_minus.winfo_height() // 2
                y = (y1 + y2) // 2
                anchor = "center"
            except Exception as exc:
                _log_suppressed("Failed deriving jog stop Y from Y jog buttons", exc)
                app.after(JOG_STOP_POSITION_RETRY_MS, _position_jog_stop)
                return

        try:
            root_x = align.winfo_rootx()
            root_y = align.winfo_rooty()
        except Exception as exc:
            _log_suppressed("Failed reading jog panel root coordinates", exc)
            root_x = 0
            root_y = 0

        x = x - root_x
        y = y - root_y
        try:
            width = align.winfo_width()
            if width > 0:
                x = max(0, min(x, width - btn.winfo_width()))
        except Exception as exc:
            _log_suppressed("Failed clamping jog stop position to panel bounds", exc)

        btn.place(in_=align, x=x, y=y, anchor=anchor)
        try:
            btn.tk.call("raise", btn._w)
        except Exception as exc:
            _log_suppressed("Failed raising jog stop button above peers", exc)

    align.bind("<Configure>", _position_jog_stop, add="+")
    app.after(0, _position_jog_stop)


def _configure_jog_grid_columns(align) -> None:
    align.grid_columnconfigure(0, weight=0)   # MPos
    align.grid_columnconfigure(1, weight=0)   # sep
    align.grid_columnconfigure(2, weight=0)   # WPos
    align.grid_columnconfigure(3, weight=0)   # sep
    align.grid_columnconfigure(4, weight=0)   # X-
    align.grid_columnconfigure(5, weight=0)   # Y+/Y-
    align.grid_columnconfigure(6, weight=0)   # X+
    align.grid_columnconfigure(7, weight=0)   # JOG STOP
    align.grid_columnconfigure(8, weight=0)   # sep
    align.grid_columnconfigure(9, weight=0)   # Z
    align.grid_columnconfigure(10, weight=0)  # Z
    align.grid_columnconfigure(11, weight=1)  # spacer for right-aligning ALL STOP
    align.grid_columnconfigure(12, weight=0)  # ALL STOP


def _build_position_and_action_controls(app, align):
    ttk.Label(align, text="Machine Position (MPos)").grid(row=0, column=0, sticky="w", pady=(0, 4))
    ttk.Label(align, text="Work Position (WPos)").grid(row=0, column=2, sticky="w", pady=(0, 4))
    ttk.Label(align, text="Jog").grid(row=0, column=4, columnspan=7, sticky="w", pady=(0, 4))

    sep_mpos = ttk.Separator(align, orient="vertical")
    sep_mpos.grid(row=0, column=1, rowspan=6, sticky="ns", padx=(8, 8))
    sep_wpos = ttk.Separator(align, orient="vertical")
    sep_wpos.grid(row=0, column=3, rowspan=6, sticky="ns", padx=(8, 8))
    style = ttk.Style()
    sep_bg = style.lookup("TFrame", "background") or app.cget("bg")
    sep_jog_line = tk.Frame(align, width=1, bg=sep_bg)

    app._dro_value_row(
        align,
        "X",
        app.mpos_x,
        grid_info={"row": 1, "column": 0, "sticky": "ew", "pady": 2},
    )
    app._dro_value_row(
        align,
        "Y",
        app.mpos_y,
        grid_info={"row": 2, "column": 0, "sticky": "ew", "pady": 2},
    )
    app._dro_value_row(
        align,
        "Z",
        app.mpos_z,
        grid_info={"row": 3, "column": 0, "sticky": "ew", "pady": 2},
    )

    app.btn_zero_x = app._dro_row(
        align,
        "X",
        app.wpos_x,
        app.zero_x,
        grid_info={"row": 1, "column": 2, "sticky": "ew", "pady": 2},
    )
    app.btn_zero_y = app._dro_row(
        align,
        "Y",
        app.wpos_y,
        app.zero_y,
        grid_info={"row": 2, "column": 2, "sticky": "ew", "pady": 2},
    )
    app.btn_zero_z = app._dro_row(
        align,
        "Z",
        app.wpos_z,
        app.zero_z,
        grid_info={"row": 3, "column": 2, "sticky": "ew", "pady": 2},
    )
    app._manual_controls.extend([app.btn_zero_x, app.btn_zero_y, app.btn_zero_z])

    mpos_actions_top = ttk.Frame(align)
    mpos_actions_top.grid(row=4, column=0, sticky="new", pady=(6, 0))
    mpos_actions_top.grid_columnconfigure(0, weight=1, uniform="mpos_buttons")
    mpos_actions_top.grid_columnconfigure(1, weight=1, uniform="mpos_buttons")

    app.btn_unit_toggle = ttk.Button(
        mpos_actions_top,
        text=app._unit_toggle_label(),
        style="TButton",
        command=app._toggle_unit_mode,
    )
    set_kb_id(app.btn_unit_toggle, "unit_toggle")
    app.btn_unit_toggle.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    app._manual_controls.append(app.btn_unit_toggle)
    app._offline_controls.add(app.btn_unit_toggle)
    apply_tooltip(
        app.btn_unit_toggle,
        "Toggle modal units (G20/G21). Blue text means report units are tracked ($13).",
    )
    app._update_unit_toggle_display()

    btns = ttk.Frame(align)
    btns.grid(row=4, column=2, sticky="new", pady=(6, 0))
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

    _bind_position_column_sync(app, align)
    return sep_mpos, sep_wpos, sep_jog_line


def _build_xy_jog_controls(app, align, j, jog_cmd) -> None:
    app.btn_jog_y_plus = ttk.Button(align, text="Y+", width=6, command=lambda: j(0, app.step_xy.get(), 0))
    set_kb_id(app.btn_jog_y_plus, "jog_y_plus")
    app.btn_jog_y_plus.grid(row=1, column=5, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_y_plus, lambda: jog_cmd(0, app.step_xy.get(), 0))
    app.btn_jog_x_minus = ttk.Button(align, text="X-", width=6, command=lambda: j(-app.step_xy.get(), 0, 0))
    set_kb_id(app.btn_jog_x_minus, "jog_x_minus")
    app.btn_jog_x_minus.grid(row=2, column=4, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_x_minus, lambda: jog_cmd(-app.step_xy.get(), 0, 0))
    app.btn_jog_x_plus = ttk.Button(align, text="X+", width=6, command=lambda: j(app.step_xy.get(), 0, 0))
    set_kb_id(app.btn_jog_x_plus, "jog_x_plus")
    app.btn_jog_x_plus.grid(row=2, column=6, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_x_plus, lambda: jog_cmd(app.step_xy.get(), 0, 0))
    app.btn_jog_y_minus = ttk.Button(align, text="Y-", width=6, command=lambda: j(0, -app.step_xy.get(), 0))
    set_kb_id(app.btn_jog_y_minus, "jog_y_minus")
    app.btn_jog_y_minus.grid(row=3, column=5, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_y_minus, lambda: jog_cmd(0, -app.step_xy.get(), 0))
    apply_tooltip(app.btn_jog_y_plus, "Jog +Y by the selected step.")
    apply_tooltip(app.btn_jog_y_minus, "Jog -Y by the selected step.")
    apply_tooltip(app.btn_jog_x_minus, "Jog -X by the selected step.")
    apply_tooltip(app.btn_jog_x_plus, "Jog +X by the selected step.")


def _apply_separator_styles(app, sep_mpos, sep_wpos, sep_jog_line) -> str:
    style = ttk.Style()
    sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background")
    pad_bg = style.lookup("TFrame", "background") or app.cget("bg")
    style.configure("JogSeparator.TSeparator", background=sep_color)
    for sep in (sep_mpos, sep_wpos):
        try:
            sep.configure(style="JogSeparator.TSeparator")
        except Exception as exc:
            _log_suppressed("Failed applying jog separator style", exc)
    try:
        sep_jog_line.configure(bg=sep_color)
    except Exception as exc:
        _log_suppressed("Failed applying jog separator line color", exc)
    if isinstance(pad_bg, str):
        return pad_bg
    return str(pad_bg or "")


def _build_z_and_stop_controls(app, align, j, jog_cmd, pad_bg: str) -> int:
    def cancel_jog():
        app._stop_joystick_hold()
        app.grbl.jog_cancel()
        try:
            app.grbl.cancel_pending_jogs()
        except Exception as exc:
            _log_suppressed("Failed cancelling pending jog queue", exc)

    app.btn_jog_cancel = StopSignButton(
        align,
        text="JOG\nSTOP",
        fill="#f2b200",
        text_color="#000000",
        command=cancel_jog,
        bg=pad_bg,
    )
    set_kb_id(app.btn_jog_cancel, "jog_stop")
    apply_tooltip(app.btn_jog_cancel, "Cancel an active jog ($J cancel).")
    attach_log_gcode(app.btn_jog_cancel, "RT 0x85")

    z_jog_left_pad = 64
    app.btn_jog_z_plus = ttk.Button(align, text="Z+", width=6, command=lambda: j(0, 0, app.step_z.get()))
    set_kb_id(app.btn_jog_z_plus, "jog_z_plus")
    app.btn_jog_z_plus.grid(row=1, column=9, padx=(z_jog_left_pad, 2), pady=2)
    app.tool_reference_label = ttk.Label(
        align,
        textvariable=app.tool_reference_var,
        width=18,
        anchor="center",
    )
    app.tool_reference_label.grid(row=2, column=9, padx=(z_jog_left_pad, 2), pady=2)
    apply_tooltip(app.tool_reference_label, "Tool reference height.")
    app.btn_jog_z_minus = ttk.Button(align, text="Z-", width=6, command=lambda: j(0, 0, -app.step_z.get()))
    set_kb_id(app.btn_jog_z_minus, "jog_z_minus")
    app.btn_jog_z_minus.grid(row=3, column=9, padx=(z_jog_left_pad, 2), pady=2)
    apply_tooltip(app.btn_jog_z_plus, "Jog +Z by the selected step.")
    apply_tooltip(app.btn_jog_z_minus, "Jog -Z by the selected step.")
    attach_log_gcode(app.btn_jog_z_plus, lambda: jog_cmd(0, 0, app.step_z.get()))
    attach_log_gcode(app.btn_jog_z_minus, lambda: jog_cmd(0, 0, -app.step_z.get()))

    all_stop_size = JOG_PANEL_ALL_STOP_SIZE
    app._all_stop_offset_px = int(app.winfo_fpixels(f"{JOG_PANEL_ALL_STOP_OFFSET_IN}i"))
    app._all_stop_slot = ttk.Frame(align, width=all_stop_size, height=all_stop_size)
    app._all_stop_slot.grid(row=1, column=12, rowspan=3, padx=(6, 0), pady=2, sticky="ns")
    app._all_stop_slot.grid_propagate(False)
    app.btn_all_stop = StopSignButton(
        align,
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
    align.bind("<Configure>", app._position_all_stop_offset, add="+")
    app.after(0, app._position_all_stop_offset)
    return z_jog_left_pad


def build_jog_panel(app, parent):
    top = ttk.Frame(parent)
    top.pack(side="top", fill="x")
    align = ttk.Frame(top)
    align.pack(fill="x")
    _configure_jog_grid_columns(align)
    sep_mpos, sep_wpos, sep_jog_line = _build_position_and_action_controls(app, align)

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
        source = getattr(app, "_manual_input_source", None) or "jog"
        app.grbl.jog(dx, dy, dz, feed, app.unit_mode.get(), source=source)

    def jog_cmd(dx, dy, dz):
        feed = _jog_feed_for_move(dx, dy, dz)
        gunit = "G21" if app.unit_mode.get() == "mm" else "G20"
        return f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"

    _build_xy_jog_controls(app, align, j, jog_cmd)
    pad_bg = _apply_separator_styles(app, sep_mpos, sep_wpos, sep_jog_line)
    z_jog_left_pad = _build_z_and_stop_controls(app, align, j, jog_cmd, pad_bg)

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

    xy_steps, z_steps = _build_step_controls(app, align, z_jog_left_pad)
    _bind_jog_separator_position(app, align, sep_jog_line, xy_steps, z_steps)
    _bind_jog_stop_position(app, align, z_steps)

    app._manual_controls.extend([app._xy_step_minus, app._xy_step_plus])
    app._manual_controls.extend([app._z_step_minus, app._z_step_plus])
    app._offline_controls.update([app._xy_step_minus, app._xy_step_plus, app._z_step_minus, app._z_step_plus])
    app._set_step_xy(app.step_xy.get())
    app._set_step_z(app.step_z.get())

    macro_row = ttk.Frame(align)
    macro_row.grid(row=5, column=0, columnspan=13, sticky="ew", pady=(6, 0))
    macro_row.grid_columnconfigure(0, weight=1, uniform="macro_buttons")

    app.macro_panel.attach_frames(macro_row, None)

    app._set_unit_mode(app.unit_mode.get())

