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

import queue


def drain_ui_queue(app):
    for _ in range(100):
        try:
            evt = app.ui_q.get_nowait()
        except queue.Empty:
            break
        try:
            app._handle_evt(evt)
        except Exception as exc:
            app._log_exception("UI event error", exc)
    if app._closing:
        return
    if hasattr(app, "_sync_tool_reference_label"):
        app._sync_tool_reference_label()
    app._maybe_auto_reconnect()
    app.after(50, app._drain_ui_queue)
