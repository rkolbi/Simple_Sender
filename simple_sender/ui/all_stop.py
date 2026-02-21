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
    JOG_PANEL_ALL_STOP_OFFSET_FALLBACK_PX,
    JOG_PANEL_ALL_STOP_OFFSET_IN,
)

ALL_STOP_POSITION_RETRY_MS = 50


def all_stop_action(app):
    try:
        app._stop_joystick_hold()
    except Exception:
        pass
    if not app._require_grbl_connection():
        return
    mode = app.all_stop_mode.get()
    if mode == "reset":
        app.grbl.reset()
    elif mode == "stop_reset":
        app.grbl.stop_stream()
        app.grbl.reset()
    else:
        app.grbl.stop_stream()


def all_stop_gcode_label(app) -> str:
    mode = app.all_stop_mode.get()
    if mode == "reset":
        return "Ctrl-X"
    return "Stop stream + Ctrl-X"


def position_all_stop_offset(app, event=None):
    slot = getattr(app, "_all_stop_slot", None)
    btn = getattr(app, "btn_all_stop", None)
    if not slot or not btn:
        return
    try:
        if not slot.winfo_exists():
            return
    except tk.TclError:
        return
    if not slot.winfo_ismapped():
        app.after(ALL_STOP_POSITION_RETRY_MS, app._position_all_stop_offset)
        return
    offset = getattr(app, "_all_stop_offset_px", None)
    if offset is None:
        try:
            offset = int(app.winfo_fpixels(f"{JOG_PANEL_ALL_STOP_OFFSET_IN}i"))
        except tk.TclError:
            offset = JOG_PANEL_ALL_STOP_OFFSET_FALLBACK_PX
        app._all_stop_offset_px = offset
    x = slot.winfo_x() - offset
    if x < 0:
        x = 0
    y = slot.winfo_y()
    btn.place(in_=slot.master, x=x, y=y)
    try:
        btn.tk.call("raise", btn._w)
    except tk.TclError:
        pass
