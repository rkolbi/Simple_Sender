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

from simple_sender.ui.gcode_viewer import GcodeViewer


def build_gcode_tab(app, notebook):
    nb = notebook
    # Gcode tab
    gtab = ttk.Frame(nb, padding=6)
    nb.add(gtab, text="G-code")
    stats_row = ttk.Frame(gtab)
    stats_row.pack(fill="x", pady=(0, 6))
    app.gcode_stats_label = ttk.Label(stats_row, textvariable=app.gcode_stats_var, anchor="w")
    app.gcode_stats_label.pack(side="left", fill="x", expand=True)
    app.gview = GcodeViewer(gtab)  # Using refactored GcodeViewer
    app.gview.pack(fill="both", expand=True)

