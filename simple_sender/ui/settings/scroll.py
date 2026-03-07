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
_TOUCH_SCROLL_STEP_THRESHOLD = 4
_WHEEL_DELTA_UNIT = 120
_TOUCH_SCROLL_MODE_SWIPE = "thumb_and_swipe"
_TOUCH_SCROLL_MODE_THUMB_ONLY = "thumb_only"


def _is_descendant(widget, ancestor) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "master", None)
    return False


def _is_scrollbar_widget(widget) -> bool:
    if isinstance(widget, (tk.Scrollbar, ttk.Scrollbar)):
        return True
    try:
        klass = str(widget.winfo_class() or "").lower()
    except Exception:
        return False
    return "scrollbar" in klass


def _resolve_touch_scroll_mode(app) -> str:
    raw = None
    var = getattr(app, "touch_scroll_mode", None)
    if var is not None:
        try:
            raw = var.get()
        except Exception:
            raw = None
    if raw is None:
        try:
            raw = getattr(app, "settings", {}).get("touch_scroll_mode", _TOUCH_SCROLL_MODE_SWIPE)
        except Exception:
            raw = _TOUCH_SCROLL_MODE_SWIPE
    normalized = str(raw or "").strip().lower().replace(" ", "_").replace("+", "_and_")
    if normalized in {_TOUCH_SCROLL_MODE_SWIPE, _TOUCH_SCROLL_MODE_THUMB_ONLY}:
        return normalized
    return _TOUCH_SCROLL_MODE_SWIPE


def _touch_swipe_enabled(app) -> bool:
    return _resolve_touch_scroll_mode(app) == _TOUCH_SCROLL_MODE_SWIPE


def _pointer_widget(app):
    canvas = getattr(app, "app_settings_canvas", None)
    if canvas is None:
        return None
    try:
        x_root = int(canvas.winfo_pointerx())
        y_root = int(canvas.winfo_pointery())
    except Exception:
        return None
    try:
        return canvas.winfo_containing(x_root, y_root)
    except Exception:
        return None


def _iter_bind_targets(widget):
    if widget is None:
        return
    yield widget
    try:
        children = tuple(widget.winfo_children())
    except Exception:
        children = ()
    for child in children:
        yield from _iter_bind_targets(child)


def _bind_targets(app):
    canvas = getattr(app, "app_settings_canvas", None)
    inner = getattr(app, "_app_settings_inner", None)
    seen: set[str] = set()
    for root in (canvas, inner):
        for target in _iter_bind_targets(root):
            key = str(target)
            if key in seen:
                continue
            seen.add(key)
            yield key, target


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
        ),
    ):
        return False
    if _is_scrollbar_widget(widget):
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
    note_interaction = getattr(app, "_note_app_settings_interaction", None)
    if callable(note_interaction):
        note_interaction()
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
    bound_ids = getattr(app, "_app_settings_mousewheel_bound_ids", None)
    if not isinstance(bound_ids, set):
        bound_ids = set()
        app._app_settings_mousewheel_bound_ids = bound_ids
    for key, target in _bind_targets(app):
        if key in bound_ids:
            continue
        try:
            target.bind("<MouseWheel>", app._on_app_settings_mousewheel, add="+")
            target.bind("<Button-4>", app._on_app_settings_mousewheel, add="+")
            target.bind("<Button-5>", app._on_app_settings_mousewheel, add="+")
            bound_ids.add(key)
        except Exception:
            continue

def unbind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app._app_settings_mousewheel_enabled = False


def on_app_settings_touch_start(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    if getattr(app, "_app_settings_touch_enabled", True) is False:
        app._app_settings_touch_active = False
        app._app_settings_touch_moved = False
        return
    widget = getattr(event, "widget", None)
    pointer_widget = _pointer_widget(app)
    if _is_scrollbar_widget(pointer_widget):
        app._app_settings_touch_active = False
        app._app_settings_touch_moved = False
        return
    if not _touch_scroll_allowed(app, widget):
        if pointer_widget is not None and _touch_scroll_allowed(app, pointer_widget):
            widget = pointer_widget
        else:
            app._app_settings_touch_active = False
            app._app_settings_touch_moved = False
            return
    if _is_scrollbar_widget(widget):
        app._app_settings_touch_active = False
        app._app_settings_touch_moved = False
        return
    note_interaction = getattr(app, "_note_app_settings_interaction", None)
    if callable(note_interaction):
        note_interaction()
    canvas = app.app_settings_canvas
    x = canvas.winfo_pointerx() - canvas.winfo_rootx()
    y = canvas.winfo_pointery() - canvas.winfo_rooty()
    app._app_settings_touch_active = True
    app._app_settings_touch_moved = False
    app._app_settings_touch_start = (x, y)
    app._app_settings_touch_last = (x, y)
    canvas.scan_mark(x, y)


def on_app_settings_touch_move(app, event):
    if getattr(app, "_app_settings_touch_enabled", True) is False:
        app._app_settings_touch_active = False
        app._app_settings_touch_moved = False
        return
    if not getattr(app, "_app_settings_touch_active", False):
        return
    if _is_scrollbar_widget(getattr(event, "widget", None)):
        app._app_settings_touch_active = False
        app._app_settings_touch_moved = False
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
    last = getattr(app, "_app_settings_touch_last", start)
    if abs(x - last[0]) < _TOUCH_SCROLL_STEP_THRESHOLD and abs(y - last[1]) < _TOUCH_SCROLL_STEP_THRESHOLD:
        return "break"
    canvas.scan_dragto(x, y, gain=1)
    app._app_settings_touch_last = (x, y)
    note_interaction = getattr(app, "_note_app_settings_interaction", None)
    if callable(note_interaction):
        note_interaction()
    return "break"


def on_app_settings_touch_end(app, _event=None):
    app._app_settings_touch_active = False
    app._app_settings_touch_moved = False
    app._app_settings_touch_last = None


def bind_app_settings_touch_scroll(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app._app_settings_touch_enabled = _touch_swipe_enabled(app)
    bound_ids = getattr(app, "_app_settings_touch_bound_ids", None)
    if not isinstance(bound_ids, set):
        bound_ids = set()
        app._app_settings_touch_bound_ids = bound_ids
    for key, target in _bind_targets(app):
        if key in bound_ids:
            continue
        try:
            target.bind("<ButtonPress-1>", app._on_app_settings_touch_start, add="+")
            target.bind("<B1-Motion>", app._on_app_settings_touch_move, add="+")
            target.bind("<ButtonRelease-1>", app._on_app_settings_touch_end, add="+")
            bound_ids.add(key)
        except Exception:
            continue


def unbind_app_settings_touch_scroll(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app._app_settings_touch_enabled = False
    app._app_settings_touch_active = False
    app._app_settings_touch_moved = False
