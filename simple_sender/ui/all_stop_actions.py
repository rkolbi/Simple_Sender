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
