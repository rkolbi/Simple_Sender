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
_WHEEL_DELTA_UNIT = 120


def _is_descendant(widget, ancestor) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "master", None)
    return False


def _touch_scroll_allowed(app, widget) -> bool:
    if widget is None:
        return False
    # Restrict touch scrolling to the App Settings content/canvas so controls
    # like the tab's scrollbar do not trigger reverse scan dragging.
    inner = getattr(app, "_app_settings_inner", None)
    canvas = getattr(app, "app_settings_canvas", None)
    if inner is not None and not _is_descendant(widget, inner) and not _is_descendant(widget, canvas):
        return False
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
            tk.Scrollbar,
            ttk.Scrollbar,
        ),
    ):
        return False
    return True


def _wheel_steps(app, event, remainder_attr: str) -> int:
    delta = getattr(event, "delta", 0) or 0
    if delta:
        try:
            carry = float(getattr(app, remainder_attr, 0.0) or 0.0) + float(delta)
        except Exception:
            carry = float(delta)
        steps = int(carry / _WHEEL_DELTA_UNIT)
        try:
            setattr(app, remainder_attr, carry - (steps * _WHEEL_DELTA_UNIT))
        except Exception:
            pass
        return -steps
    num = getattr(event, "num", None)
    if num == 4:
        return -1
    if num == 5:
        return 1
    return 0


def update_app_settings_scrollregion(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.configure(scrollregion=app.app_settings_canvas.bbox("all"))

def on_app_settings_mousewheel(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    if getattr(app, "_app_settings_mousewheel_enabled", True) is False:
        return
    widget = getattr(event, "widget", None)
    inner = getattr(app, "_app_settings_inner", None)
    canvas = getattr(app, "app_settings_canvas", None)
    if widget is not None and inner is not None:
        if not _is_descendant(widget, inner) and not _is_descendant(widget, canvas):
            return
    delta = _wheel_steps(app, event, "_app_settings_mousewheel_remainder")
    if delta:
        app.app_settings_canvas.yview_scroll(delta, "units")
        return "break"

def bind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app._app_settings_mousewheel_enabled = True
    if getattr(app, "_app_settings_mousewheel_bound", False):
        return
    bind_all = getattr(app, "bind_all", None)
    if callable(bind_all):
        bind_all("<MouseWheel>", app._on_app_settings_mousewheel, add="+")
        bind_all("<Button-4>", app._on_app_settings_mousewheel, add="+")
        bind_all("<Button-5>", app._on_app_settings_mousewheel, add="+")
    else:
        app.app_settings_canvas.bind_all("<MouseWheel>", app._on_app_settings_mousewheel, add="+")
        app.app_settings_canvas.bind_all("<Button-4>", app._on_app_settings_mousewheel, add="+")
        app.app_settings_canvas.bind_all("<Button-5>", app._on_app_settings_mousewheel, add="+")
    app._app_settings_mousewheel_bound = True

def unbind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app._app_settings_mousewheel_enabled = False


def on_app_settings_touch_start(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    if getattr(app, "_app_settings_touch_enabled", True) is False:
        return
    if not _touch_scroll_allowed(app, getattr(event, "widget", None)):
        return
    canvas = app.app_settings_canvas
    x = canvas.winfo_pointerx() - canvas.winfo_rootx()
    y = canvas.winfo_pointery() - canvas.winfo_rooty()
    app._app_settings_touch_active = True
    app._app_settings_touch_moved = False
    app._app_settings_touch_start = (x, y)
    canvas.scan_mark(x, y)


def on_app_settings_touch_move(app, event):
    if getattr(app, "_app_settings_touch_enabled", True) is False:
        return
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
    app._app_settings_touch_enabled = True
    if getattr(app, "_app_settings_touch_bound", False):
        return
    bind_all = getattr(app, "bind_all", None)
    if callable(bind_all):
        bind_all("<ButtonPress-1>", app._on_app_settings_touch_start, add="+")
        bind_all("<B1-Motion>", app._on_app_settings_touch_move, add="+")
        bind_all("<ButtonRelease-1>", app._on_app_settings_touch_end, add="+")
    else:
        app.app_settings_canvas.bind_all("<ButtonPress-1>", app._on_app_settings_touch_start, add="+")
        app.app_settings_canvas.bind_all("<B1-Motion>", app._on_app_settings_touch_move, add="+")
        app.app_settings_canvas.bind_all("<ButtonRelease-1>", app._on_app_settings_touch_end, add="+")
    app._app_settings_touch_bound = True


def unbind_app_settings_touch_scroll(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app._app_settings_touch_enabled = False
    app._app_settings_touch_active = False
    app._app_settings_touch_moved = False
