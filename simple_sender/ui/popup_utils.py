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

from tkinter import messagebox

_DEFAULT_PARENT = None
_PATCHED = False


def set_default_parent(parent) -> None:
    global _DEFAULT_PARENT
    _DEFAULT_PARENT = parent


def _resolve_parent(kwargs: dict) -> dict:
    if kwargs.get("parent") is not None:
        return kwargs
    parent = _DEFAULT_PARENT
    if parent is None:
        return kwargs
    try:
        if not parent.winfo_exists():
            return kwargs
    except Exception:
        return kwargs
    kwargs["parent"] = parent
    return kwargs


def patch_messagebox() -> None:
    global _PATCHED
    if _PATCHED:
        return

    def wrap(func):
        def wrapper(*args, **kwargs):
            return func(*args, **_resolve_parent(kwargs))
        return wrapper

    messagebox.showinfo = wrap(messagebox.showinfo)
    messagebox.showwarning = wrap(messagebox.showwarning)
    messagebox.showerror = wrap(messagebox.showerror)
    messagebox.askyesno = wrap(messagebox.askyesno)
    _PATCHED = True


def center_window(window, parent=None) -> None:
    try:
        window.update_idletasks()
    except Exception:
        return

    w = window.winfo_width() or window.winfo_reqwidth()
    h = window.winfo_height() or window.winfo_reqheight()

    if parent is None:
        parent = _DEFAULT_PARENT

    x = y = 0
    if parent is not None:
        try:
            parent.update_idletasks()
            pw = parent.winfo_width() or parent.winfo_reqwidth()
            ph = parent.winfo_height() or parent.winfo_reqheight()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
        except Exception:
            parent = None

    if parent is None:
        try:
            sw = window.winfo_screenwidth()
            sh = window.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
        except Exception:
            x = y = 0

    window.geometry(f"+{max(0, x)}+{max(0, y)}")
