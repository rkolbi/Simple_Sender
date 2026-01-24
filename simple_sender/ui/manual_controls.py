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


def _manual_control_state(app, widget, enabled: bool, connected: bool) -> str:
    if getattr(widget, "_force_disabled", False):
        return "disabled"
    if not connected:
        return "normal" if widget in app._offline_controls else "disabled"
    if not enabled:
        if widget is getattr(app, "btn_all_stop", None):
            return "normal"
        if widget in app._override_controls:
            return "normal"
        return "disabled"
    return "normal"


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
    for w in app._manual_controls:
        try:
            w.config(state=_manual_control_state(app, w, enabled, connected))
        except tk.TclError:
            pass
    if enabled and connected:
        app._set_unit_mode(app.unit_mode.get())
        app._set_step_xy(app.step_xy.get())
        app._set_step_z(app.step_z.get())
