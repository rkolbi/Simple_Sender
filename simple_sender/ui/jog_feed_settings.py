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
