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

from simple_sender.ui.widgets import set_kb_id


def _unit_scale(unit_mode: str) -> float:
    return 25.4 if unit_mode == "inch" else 1.0


def convert_units(value: float, from_units: str, to_units: str) -> float:
    return value * _unit_scale(from_units) / _unit_scale(to_units)


def format_dro_value(value: float, from_units: str, to_units: str) -> str:
    return f"{convert_units(value, from_units, to_units):.3f}"


def refresh_dro_display(app) -> None:
    try:
        unit_mode = app.unit_mode.get()
    except Exception:
        unit_mode = "mm"
    report_units = getattr(app, "_report_units", None) or unit_mode
    mpos = getattr(app, "_mpos_raw", None)
    if mpos and len(mpos) == 3:
        app.mpos_x.set(format_dro_value(mpos[0], report_units, unit_mode))
        app.mpos_y.set(format_dro_value(mpos[1], report_units, unit_mode))
        app.mpos_z.set(format_dro_value(mpos[2], report_units, unit_mode))
    wpos = getattr(app, "_wpos_raw", None)
    if wpos and len(wpos) == 3:
        app.wpos_x.set(format_dro_value(wpos[0], report_units, unit_mode))
        app.wpos_y.set(format_dro_value(wpos[1], report_units, unit_mode))
        app.wpos_z.set(format_dro_value(wpos[2], report_units, unit_mode))


def dro_value_row(app, parent, axis, var, *, ttk_mod=None):
    if ttk_mod is None:
        ttk_mod = ttk
    row = ttk_mod.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk_mod.Label(row, text=f"{axis}:", width=3).grid(row=0, column=0, sticky="w")
    ttk_mod.Label(
        row,
        textvariable=var,
        width=7,
        font=app.dro_value_font,
    ).grid(row=0, column=1, sticky="w")
    # Keep a hidden button area so the MPos rows mirror the WPos layout.
    btn = ttk_mod.Button(
        row,
        text="",
        style=app.HIDDEN_MPOS_BUTTON_STYLE,
        state="disabled",
        width=9,
        takefocus=False,
    )
    btn.grid(row=0, column=2, sticky="w")


def dro_row(app, parent, axis, var, zero_cmd, *, ttk_mod=None, set_kb_id_func=None):
    if ttk_mod is None:
        ttk_mod = ttk
    if set_kb_id_func is None:
        set_kb_id_func = set_kb_id
    row = ttk_mod.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk_mod.Label(row, text=f"{axis}:", width=3).grid(row=0, column=0, sticky="w")
    value_label = ttk_mod.Label(
        row,
        textvariable=var,
        width=8,
        font=app.dro_value_font,
    )
    value_label.grid(row=0, column=1, sticky="w")
    if hasattr(app, "_wpos_value_labels"):
        try:
            app._wpos_value_labels[axis] = value_label
            app._wpos_label_default_fg[axis] = value_label.cget("foreground")
        except Exception:
            pass
    btn = ttk_mod.Button(row, text=f"Zero {axis}", command=zero_cmd)
    btn.grid(row=0, column=2, sticky="w")
    set_kb_id_func(btn, f"zero_{axis.lower()}")
    return btn
