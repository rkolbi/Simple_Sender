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

from simple_sender.utils.constants import ALL_STOP_CHOICES, CURRENT_LINE_CHOICES


def sync_all_stop_mode_combo(app):
    mode = app.all_stop_mode.get()
    label = None
    for lbl, code in ALL_STOP_CHOICES:
        if code == mode:
            label = lbl
            break
    if label is None and ALL_STOP_CHOICES:
        label = ALL_STOP_CHOICES[0][0]
        app.all_stop_mode.set(ALL_STOP_CHOICES[0][1])
    if hasattr(app, "all_stop_combo"):
        app.all_stop_combo.set(label if label else "")


def on_all_stop_mode_change(app, _event=None):
    label = ""
    if hasattr(app, "all_stop_combo"):
        label = app.all_stop_combo.get()
    mode = next((code for lbl, code in ALL_STOP_CHOICES if lbl == label), "stop_reset")
    app.all_stop_mode.set(mode)
    app.status.config(text=f"All Stop mode: {label}")


def sync_current_line_mode_combo(app):
    mode = app.current_line_mode.get()
    label = None
    for lbl, code in CURRENT_LINE_CHOICES:
        if code == mode:
            label = lbl
            break
    if label is None and CURRENT_LINE_CHOICES:
        label = CURRENT_LINE_CHOICES[0][0]
        app.current_line_mode.set(CURRENT_LINE_CHOICES[0][1])
    if hasattr(app, "current_line_combo"):
        app.current_line_combo.set(label if label else "")


def on_current_line_mode_change(app, _event=None):
    label = ""
    if hasattr(app, "current_line_combo"):
        label = app.current_line_combo.get()
    mode = next((code for lbl, code in CURRENT_LINE_CHOICES if lbl == label), "acked")
    app.current_line_mode.set(mode)
    app._update_current_highlight()


def update_current_highlight(app):
    if not hasattr(app, "gview") or app.gview is None or app.gview.lines_count <= 0:
        return
    max_idx = app.gview.lines_count - 1
    mode = app.current_line_mode.get()
    target_idx = None
    if mode == "acked":
        desired = app._last_acked_index + 1
        if desired < 0:
            desired = 0
        target_idx = min(desired, max_idx)
    else:
        if app._last_sent_index >= 0:
            target_idx = min(app._last_sent_index, max_idx)
        elif app._last_acked_index >= 0:
            candidate = app._last_acked_index + 1
            target_idx = min(candidate, max_idx)
    if target_idx is not None:
        app.gview.highlight_current(target_idx)
