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

def set_streaming_lock(app, locked: bool):
    state = "disabled" if locked else "normal"
    try:
        app.btn_conn.config(state=state)
    except Exception:
        pass
    try:
        app.btn_refresh.config(state=state)
    except Exception:
        pass
    try:
        app.port_combo.config(state="disabled" if locked else "readonly")
    except Exception:
        pass
    try:
        app.btn_unit_toggle.config(state=state)
    except Exception:
        pass
