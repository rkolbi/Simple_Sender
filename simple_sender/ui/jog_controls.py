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

from simple_sender.utils.constants import (
    SAFE_JOG_FEED_XY,
    SAFE_JOG_FEED_Z,
    SAFE_JOG_STEP_XY,
    SAFE_JOG_STEP_Z,
)

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


def validate_jog_feed_var(app, var: tk.DoubleVar, fallback_default: float):
    try:
        val = float(var.get())
    except Exception:
        val = None
    if val is None or val <= 0:
        try:
            fallback = float(fallback_default)
        except Exception:
            fallback = fallback_default
        var.set(fallback)
        return
    var.set(val)


def on_jog_feed_change_xy(app, _event=None):
    validate_jog_feed_var(app, app.jog_feed_xy, app.settings.get("jog_feed_xy", 4000.0))


def on_jog_feed_change_z(app, _event=None):
    validate_jog_feed_var(app, app.jog_feed_z, app.settings.get("jog_feed_z", 500.0))


def apply_safe_mode_profile(app) -> None:
    unit_mode = "mm"
    try:
        unit_mode = app.unit_mode.get() or "mm"
    except Exception:
        unit_mode = "mm"
    scale = 1.0 if unit_mode == "mm" else 1.0 / 25.4
    feed_xy = SAFE_JOG_FEED_XY * scale
    feed_z = SAFE_JOG_FEED_Z * scale
    step_xy = SAFE_JOG_STEP_XY * scale
    step_z = SAFE_JOG_STEP_Z * scale
    app.jog_feed_xy.set(feed_xy)
    app.jog_feed_z.set(feed_z)
    app.step_xy.set(step_xy)
    app.step_z.set(step_z)
    try:
        app.settings["jog_feed_xy"] = feed_xy
        app.settings["jog_feed_z"] = feed_z
        app.settings["step_xy"] = step_xy
        app.settings["step_z"] = step_z
    except Exception:
        pass
    try:
        app._set_step_xy(step_xy)
        app._set_step_z(step_z)
    except Exception:
        pass
    try:
        app._on_jog_feed_change_xy()
        app._on_jog_feed_change_z()
    except Exception:
        pass
    try:
        app.streaming_controller.handle_log(
            f"[safe mode] Jog feeds set to {feed_xy:g}/{feed_z:g} "
            f"{'mm' if unit_mode == 'mm' else 'in'}/min, steps {step_xy:g}/{step_z:g} "
            f"{'mm' if unit_mode == 'mm' else 'in'}."
        )
    except Exception:
        pass
