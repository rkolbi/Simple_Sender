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

from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.log_viewer import LogViewer


def show_logs_dialog(app) -> None:
    existing = getattr(app, "_logs_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass

    win = tk.Toplevel(app)
    app._logs_window = win
    win.title("Application Logs")
    win.minsize(760, 480)
    win.transient(app)

    def _on_close():
        app._logs_window = None
        win.destroy()

    viewer = LogViewer(win, app, include_close=True, close_callback=_on_close)
    viewer.pack(fill="both", expand=True)

    win.protocol("WM_DELETE_WINDOW", _on_close)
    center_window(win, app)
