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


def set_manual_controls_enabled(app, enabled: bool):
    if getattr(app, "_alarm_locked", False):
        for w in app._manual_controls:
            try:
                if w is getattr(app, "btn_all_stop", None):
                    continue
                if w is getattr(app, "btn_home_mpos", None):
                    w.config(state="normal")
                    continue
                if w is getattr(app, "btn_unlock_mpos", None):
                    w.config(state="normal")
                    continue
                if w is getattr(app, "btn_unlock_top", None):
                    w.config(state="normal")
                    continue
                w.config(state="disabled")
            except tk.TclError:
                pass
        return
    connected = bool(getattr(app, "connected", False))
    state = "normal" if enabled else "disabled"
    for w in app._manual_controls:
        try:
            if not connected:
                if w in app._offline_controls:
                    w.config(state="normal")
                else:
                    w.config(state="disabled")
                continue
            if not enabled and w is getattr(app, "btn_all_stop", None):
                w.config(state="normal")
                continue
            if not enabled and w in app._override_controls:
                w.config(state="normal")
                continue
            w.config(state=state)
        except tk.TclError:
            pass
    if enabled and connected:
        app._set_unit_mode(app.unit_mode.get())
        app._set_step_xy(app.step_xy.get())
        app._set_step_z(app.step_z.get())
