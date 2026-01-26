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

_TOUCH_SCROLL_THRESHOLD = 6


def _touch_scroll_allowed(widget) -> bool:
    if isinstance(
        widget,
        (
            tk.Entry,
            tk.Text,
            tk.Listbox,
            tk.Spinbox,
            ttk.Entry,
            ttk.Combobox,
            ttk.Scale,
            ttk.Spinbox,
        ),
    ):
        return False
    return True

def update_app_settings_scrollregion(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.configure(scrollregion=app.app_settings_canvas.bbox("all"))

def on_app_settings_mousewheel(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    delta = 0
    if event.delta:
        delta = -int(event.delta / 120)
    elif getattr(event, "num", None) == 4:
        delta = -1
    elif getattr(event, "num", None) == 5:
        delta = 1
    if delta:
        app.app_settings_canvas.yview_scroll(delta, "units")

def bind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.bind_all("<MouseWheel>", app._on_app_settings_mousewheel)
    app.app_settings_canvas.bind_all("<Button-4>", app._on_app_settings_mousewheel)
    app.app_settings_canvas.bind_all("<Button-5>", app._on_app_settings_mousewheel)

def unbind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.unbind_all("<MouseWheel>")
    app.app_settings_canvas.unbind_all("<Button-4>")
    app.app_settings_canvas.unbind_all("<Button-5>")


def on_app_settings_touch_start(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    if not _touch_scroll_allowed(getattr(event, "widget", None)):
        return
    canvas = app.app_settings_canvas
    x = canvas.winfo_pointerx() - canvas.winfo_rootx()
    y = canvas.winfo_pointery() - canvas.winfo_rooty()
    app._app_settings_touch_active = True
    app._app_settings_touch_moved = False
    app._app_settings_touch_start = (x, y)
    canvas.scan_mark(x, y)


def on_app_settings_touch_move(app, event):
    if not getattr(app, "_app_settings_touch_active", False):
        return
    canvas = app.app_settings_canvas
    x = canvas.winfo_pointerx() - canvas.winfo_rootx()
    y = canvas.winfo_pointery() - canvas.winfo_rooty()
    start = getattr(app, "_app_settings_touch_start", (x, y))
    dx = x - start[0]
    dy = y - start[1]
    if not getattr(app, "_app_settings_touch_moved", False):
        if abs(dx) < _TOUCH_SCROLL_THRESHOLD and abs(dy) < _TOUCH_SCROLL_THRESHOLD:
            return
        app._app_settings_touch_moved = True
    canvas.scan_dragto(x, y, gain=1)
    return "break"


def on_app_settings_touch_end(app, _event=None):
    app._app_settings_touch_active = False
    app._app_settings_touch_moved = False


def bind_app_settings_touch_scroll(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.bind_all("<ButtonPress-1>", app._on_app_settings_touch_start, add="+")
    app.app_settings_canvas.bind_all("<B1-Motion>", app._on_app_settings_touch_move, add="+")
    app.app_settings_canvas.bind_all("<ButtonRelease-1>", app._on_app_settings_touch_end, add="+")


def unbind_app_settings_touch_scroll(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.unbind_all("<ButtonPress-1>")
    app.app_settings_canvas.unbind_all("<B1-Motion>")
    app.app_settings_canvas.unbind_all("<ButtonRelease-1>")
