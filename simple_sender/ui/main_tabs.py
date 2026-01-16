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

from tkinter import ttk

from simple_sender.ui.app_settings import build_app_settings_tab
from simple_sender.ui.console_panel import build_console_tab
from simple_sender.ui.gcode_tab import build_gcode_tab
from simple_sender.ui.overdrive_tab import build_overdrive_tab


def build_main_tabs(app, parent):
    # Bottom notebook: G-code + Console + Settings
    nb = ttk.Notebook(parent)
    app.notebook = nb
    nb.pack(side="top", fill="both", expand=True, pady=(10, 0))
    nb.bind("<<NotebookTabChanged>>", app._on_tab_changed)

    # Gcode tab
    build_gcode_tab(app, nb)

    # Console tab
    build_console_tab(app, nb)

    otab = ttk.Frame(nb, padding=6)
    nb.add(otab, text="Overdrive")
    build_overdrive_tab(app, otab)
    app.settings_controller.build_tabs(nb)

    # App Settings tab
    build_app_settings_tab(app, nb)

    # 3D tab
    app.toolpath_panel.build_tab(nb)
    app._update_tab_visibility(nb)

