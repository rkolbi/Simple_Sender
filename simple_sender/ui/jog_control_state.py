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

from simple_sender.ui.icons import ICON_UNITS, icon_label


def unit_toggle_label(app, mode: str | None = None) -> str:
    if mode is None:
        mode = app.unit_mode.get() or "mm"
    return icon_label(ICON_UNITS, str(mode))


def update_unit_toggle_display(app):
    reported = getattr(app, "_report_units", None)
    label_units = app.unit_mode.get()
    try:
        if not hasattr(app, "_unit_toggle_default_style"):
            app._unit_toggle_default_style = app.btn_unit_toggle.cget("style") or "TButton"
    except Exception:
        app._unit_toggle_default_style = "TButton"
    try:
        if reported in ("mm", "inch"):
            palette = getattr(app, "theme_palette", None) or {}
            accent = palette.get("accent", "#0e639c")
            style = "SimpleSender.UnitReported.TButton"
            app.style.configure(style, foreground=accent)
            app.btn_unit_toggle.config(style=style)
        else:
            app.btn_unit_toggle.config(style=app._unit_toggle_default_style)
        app.btn_unit_toggle.config(text=unit_toggle_label(app, label_units))
    except Exception:
        pass


def set_unit_mode(app, mode: str):
    old_mode = app.unit_mode.get()
    if mode == old_mode:
        return
    app.unit_mode.set(mode)
    app._modal_units = mode
    with app.macro_executor.macro_vars() as macro_vars:
        macro_vars["units"] = "G21" if mode == "mm" else "G20"
    try:
        update_unit_toggle_display(app)
    except Exception:
        pass
    try:
        app._convert_estimate_rates(old_mode, mode)
        app._update_estimate_rate_units_label()
        if app._last_gcode_lines:
            app._update_gcode_stats(app._last_gcode_lines)
    except Exception:
        pass
    try:
        app._refresh_dro_display()
    except Exception:
        pass
    try:
        app._refresh_gcode_stats_display()
    except Exception:
        pass


def set_step_xy(app, value: float):
    app.step_xy.set(value)
    for v, btn in app._xy_step_buttons:
        btn.config(state="disabled" if v == value else "normal")


def set_step_z(app, value: float):
    app.step_z.set(value)
    for v, btn in app._z_step_buttons:
        btn.config(state="disabled" if v == value else "normal")
