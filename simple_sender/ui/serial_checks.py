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

from tkinter import messagebox


def ensure_serial_available(app, serial_available: bool, serial_error: str | None = None) -> bool:
    if serial_available:
        return True
    msg = (
        "pyserial is required to communicate with GRBL. Install pyserial (pip install pyserial) "
        "and restart the application."
    )
    if serial_error:
        msg += f"\n{serial_error}"
    messagebox.showerror("Missing dependency", msg)
    return False
