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
from tkinter import ttk

from simple_sender.ui.app_settings_sections import (
    build_auto_level_section,
    build_error_dialogs_section,
    build_estimation_section,
    build_gcode_view_section,
    build_interface_section,
    build_jogging_section,
    build_keyboard_shortcuts_section,
    build_macros_section,
    build_power_section,
    build_status_polling_section,
    build_theme_section,
    build_toolpath_settings_section,
    build_zeroing_section,
)


def build_app_settings_tab(app, notebook):
    nb = notebook
    # App Settings tab
    sstab = ttk.Frame(nb, padding=8)
    nb.add(sstab, text="App Settings")
    sstab.grid_columnconfigure(0, weight=1)
    sstab.grid_rowconfigure(0, weight=1)
    app.app_settings_canvas = tk.Canvas(sstab, highlightthickness=0)
    app.app_settings_canvas.grid(row=0, column=0, sticky="nsew")
    app.app_settings_scroll = ttk.Scrollbar(
        sstab,
        orient="vertical",
        command=app.app_settings_canvas.yview,
    )
    app.app_settings_scroll.grid(row=0, column=1, sticky="ns")
    app.app_settings_canvas.configure(yscrollcommand=app.app_settings_scroll.set)
    app._app_settings_inner = ttk.Frame(app.app_settings_canvas)
    app._app_settings_window = app.app_settings_canvas.create_window(
        (0, 0), window=app._app_settings_inner, anchor="nw"
    )
    app._app_settings_inner.bind("<Configure>", lambda event: app._update_app_settings_scrollregion())
    app.app_settings_canvas.bind("<Configure>", lambda event: app.app_settings_canvas.itemconfig(
        app._app_settings_window, width=event.width
    ))
    app._app_settings_inner.bind("<Enter>", lambda event: app._bind_app_settings_mousewheel())
    app._app_settings_inner.bind("<Leave>", lambda event: app._unbind_app_settings_mousewheel())
    app._app_settings_inner.grid_columnconfigure(0, weight=1)

    version_label = ttk.Label(
        app._app_settings_inner,
        textvariable=app.version_var,
        font=("TkDefaultFont", 10, "bold"),
    )
    version_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

    next_row = 1
    next_row = build_theme_section(app, app._app_settings_inner, next_row)
    next_row = build_estimation_section(app, app._app_settings_inner, next_row)
    next_row = build_status_polling_section(app, app._app_settings_inner, next_row)
    next_row = build_error_dialogs_section(app, app._app_settings_inner, next_row)

    next_row = build_macros_section(app, app._app_settings_inner, next_row)
    next_row = build_zeroing_section(app, app._app_settings_inner, next_row)
    next_row = build_jogging_section(app, app._app_settings_inner, next_row)
    next_row = build_keyboard_shortcuts_section(app, app._app_settings_inner, next_row)
    next_row = build_gcode_view_section(app, app._app_settings_inner, next_row)

    next_row += 1
    next_row = build_interface_section(app, app._app_settings_inner, next_row)
    next_row = build_auto_level_section(app, app._app_settings_inner, next_row)
    next_row = build_toolpath_settings_section(app, app._app_settings_inner, next_row)
    build_power_section(app, app._app_settings_inner, next_row)

