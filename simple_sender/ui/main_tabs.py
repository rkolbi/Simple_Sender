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

import logging
import time
from tkinter import ttk

from simple_sender.ui.app_settings import build_app_settings_tab
from simple_sender.ui.checklists_tab import build_checklists_tab
from simple_sender.ui.console_panel import build_console_tab
from simple_sender.ui.gcode_viewer import GcodeViewer
from simple_sender.ui.overdrive_tab import build_overdrive_tab

logger = logging.getLogger(__name__)


def update_tab_visibility(app, nb=None):
    if nb is None:
        nb = getattr(app, "notebook", None)
    if not nb:
        return
    try:
        tab_id = nb.select()
        label = nb.tab(tab_id, "text")
    except Exception as exc:
        logger.exception("Failed to update tab visibility: %s", exc)
        return
    app.toolpath_panel.set_visible(label == "3D View")
    app.toolpath_panel.set_top_view_visible(label == "Top View")


def on_tab_changed(app, event):
    update_tab_visibility(app, event.widget)
    if not bool(app.gui_logging_enabled.get()):
        return
    nb = event.widget
    try:
        tab_id = nb.select()
        label = nb.tab(tab_id, "text")
    except Exception:
        return
    if not label:
        return
    ts = time.strftime("%H:%M:%S")
    app.streaming_controller.log(f"[{ts}] Tab: {label}")


def build_gcode_tab(app, notebook):
    nb = notebook
    # Gcode tab
    gtab = ttk.Frame(nb, padding=6)
    nb.add(gtab, text="G-code")
    app.gcode_tab = gtab
    stats_row = ttk.Frame(gtab)
    stats_row.pack(fill="x", pady=(0, 6))
    app.gcode_stats_label = ttk.Label(stats_row, textvariable=app.gcode_stats_var, anchor="w")
    app.gcode_stats_label.pack(side="left", fill="x", expand=True)
    app.gview = GcodeViewer(gtab)
    app.gview.pack(fill="both", expand=True)


def build_main_tabs(app, parent):
    # Bottom notebook: G-code + Console + Settings + Checklists
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

    # Checklists tab
    build_checklists_tab(app, nb)

    # 3D tab
    app.toolpath_panel.build_tab(nb)
    app._update_tab_visibility(nb)

