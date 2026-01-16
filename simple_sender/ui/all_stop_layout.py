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

import tkinter as tk


def position_all_stop_offset(app, event=None):
    slot = getattr(app, "_all_stop_slot", None)
    btn = getattr(app, "btn_all_stop", None)
    if not slot or not btn:
        return
    if not slot.winfo_ismapped():
        app.after(50, app._position_all_stop_offset)
        return
    offset = getattr(app, "_all_stop_offset_px", None)
    if offset is None:
        try:
            offset = int(app.winfo_fpixels("0.7i"))
        except tk.TclError:
            offset = 96
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
