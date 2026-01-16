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

from tkinter import ttk

from simple_sender.ui.widgets import set_kb_id


def dro_value_row(app, parent, axis, var):
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk.Label(row, text=f"{axis}:", width=3).grid(row=0, column=0, sticky="w")
    ttk.Label(
        row,
        textvariable=var,
        width=7,
        font=app.dro_value_font,
    ).grid(row=0, column=1, sticky="w")
    # Keep a hidden button area so the MPos rows mirror the WPos layout.
    btn = ttk.Button(
        row,
        text="",
        style=app.HIDDEN_MPOS_BUTTON_STYLE,
        state="disabled",
        width=9,
        takefocus=False,
    )
    btn.grid(row=0, column=2, sticky="w")


def dro_row(app, parent, axis, var, zero_cmd):
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk.Label(row, text=f"{axis}:", width=3).grid(row=0, column=0, sticky="w")
    value_label = ttk.Label(
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
    btn = ttk.Button(row, text=f"Zero {axis}", command=zero_cmd)
    btn.grid(row=0, column=2, sticky="w")
    set_kb_id(btn, f"zero_{axis.lower()}")
    return btn
