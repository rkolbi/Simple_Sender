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

import logging
import time

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
