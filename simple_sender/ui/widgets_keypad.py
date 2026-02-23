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
from typing import Any

from simple_sender.ui.widgets_tooltips import _resolve_owner, _widget_disabled


def attach_numeric_keypad(
    entry,
    *,
    allow_decimal: bool = True,
    allow_negative: bool = False,
    allow_empty: bool = True,
    title: str | None = None,
):
    spec = {
        "allow_decimal": bool(allow_decimal),
        "allow_negative": bool(allow_negative),
        "allow_empty": bool(allow_empty),
        "title": title or "Enter value",
    }
    already_bound = bool(getattr(entry, "_numeric_keypad_bound", False))
    if already_bound and getattr(entry, "_numeric_keypad_spec", None) == spec:
        return entry
    try:
        entry._numeric_keypad_spec = spec
    except (AttributeError, tk.TclError):
        return entry
    if not already_bound:
        entry.bind("<Button-1>", _open_numeric_keypad, add="+")
        entry.bind("<FocusIn>", _open_numeric_keypad_from_focus, add="+")
        try:
            entry._numeric_keypad_bound = True
        except (AttributeError, tk.TclError):
            pass
    return entry


def _open_numeric_keypad(event):
    entry = event.widget
    spec = getattr(entry, "_numeric_keypad_spec", None)
    if not spec:
        return
    try:
        if not entry.winfo_viewable():
            return
    except tk.TclError:
        pass
    owner = _resolve_owner(entry, "numeric_keypad_enabled")
    if owner is not None:
        try:
            if not bool(owner.numeric_keypad_enabled.get()):
                return
        except (AttributeError, TypeError, ValueError, tk.TclError):
            pass
    if _widget_disabled(entry):
        return
    _show_numeric_keypad(entry, spec)
    return "break"


def _open_numeric_keypad_from_focus(event):
    entry = event.widget
    spec = getattr(entry, "_numeric_keypad_spec", None)
    if not spec:
        return
    # Only auto-open on focus if the pointer is over the entry (mouse/touch focus),
    # so keyboard tab navigation does not trigger keypad popups.
    try:
        x_root = entry.winfo_pointerx()
        y_root = entry.winfo_pointery()
        hovered = entry.winfo_containing(x_root, y_root) == entry
    except tk.TclError:
        hovered = False
    if not hovered:
        return
    _open_numeric_keypad(event)


def _center_modal(window, parent):
    try:
        window.update_idletasks()
    except tk.TclError:
        return
    w = window.winfo_width() or window.winfo_reqwidth()
    h = window.winfo_height() or window.winfo_reqheight()
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
        except tk.TclError:
            parent = None
    if parent is None:
        try:
            sw = window.winfo_screenwidth()
            sh = window.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
        except tk.TclError:
            x = y = 0
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


def _show_numeric_keypad(entry, spec: dict[str, Any]):
    dlg = getattr(entry, "_numeric_keypad_dialog", None)
    if dlg is not None:
        try:
            if dlg.winfo_exists():
                dlg.lift()
                return
        except tk.TclError:
            pass
    parent = None
    try:
        parent = entry.winfo_toplevel()
    except tk.TclError:
        parent = entry
    original_value = entry.get()
    current_value = original_value
    value_var = tk.StringVar(value=current_value)
    dlg = tk.Toplevel(parent)
    dlg.title(spec.get("title") or "Enter value")
    dlg.transient(parent)
    try:
        dlg.lift()
    except tk.TclError:
        pass
    dlg.resizable(False, False)
    try:
        entry._numeric_keypad_dialog = dlg
    except AttributeError:
        pass

    frame = ttk.Frame(dlg, padding=12)
    frame.pack(fill="both", expand=True)
    display = ttk.Entry(
        frame,
        textvariable=value_var,
        state="readonly",
        width=16,
        justify="right",
    )
    display.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 10))
    display.configure(takefocus=0)

    def _set_current_value(new_value: str):
        nonlocal current_value
        current_value = new_value
        value_var.set(current_value)

    def _insert_text(text: str):
        nonlocal current_value
        current_value = f"{current_value}{text}"
        value_var.set(current_value)

    def _press_digit(digit: str):
        _insert_text(digit)

    def _press_decimal():
        if not spec.get("allow_decimal", True):
            return
        current = current_value
        if "." in current:
            return
        if current in ("", "-"):
            prefix = "-" if current == "-" else ""
            _set_current_value(f"{prefix}0.")
            return
        _insert_text(".")

    def _toggle_sign():
        if not spec.get("allow_negative", False):
            return
        val = current_value
        if val.startswith("-"):
            _set_current_value(val[1:])
        else:
            _set_current_value(f"-{val}" if val else "-")

    def _backspace():
        val = current_value
        if not val:
            return
        _set_current_value(val[:-1])

    def _clear():
        if not spec.get("allow_empty", True):
            return
        _set_current_value("")

    def _apply_and_close():
        new_value = current_value
        if not spec.get("allow_empty", True) and new_value == "":
            new_value = original_value
        try:
            entry.delete(0, "end")
            if new_value:
                entry.insert(0, new_value)
        except tk.TclError:
            pass
        try:
            entry.event_generate("<Return>")
        except tk.TclError:
            pass
        try:
            entry.event_generate("<FocusOut>")
        except tk.TclError:
            pass
        _close_dialog()

    def _cancel():
        _close_dialog()

    def _close_dialog():
        try:
            dlg.grab_release()
        except tk.TclError:
            pass
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        try:
            entry._numeric_keypad_dialog = None
        except AttributeError:
            pass

    def _make_button(text: str, command, row: int, col: int, *, colspan: int = 1):
        btn = ttk.Button(
            frame,
            text=text,
            command=command,
            width=8,
            padding=(12, 8),
            takefocus=0,
        )
        btn.grid(row=row, column=col, columnspan=colspan, padx=4, pady=4, sticky="nsew")
        return btn

    buttons = [
        ("7", lambda: _press_digit("7")),
        ("8", lambda: _press_digit("8")),
        ("9", lambda: _press_digit("9")),
        ("4", lambda: _press_digit("4")),
        ("5", lambda: _press_digit("5")),
        ("6", lambda: _press_digit("6")),
        ("1", lambda: _press_digit("1")),
        ("2", lambda: _press_digit("2")),
        ("3", lambda: _press_digit("3")),
        ("0", lambda: _press_digit("0")),
    ]
    row = 1
    col = 0
    for label, cmd in buttons:
        _make_button(label, cmd, row, col)
        col += 1
        if col > 2:
            col = 0
            row += 1

    col = 0
    if spec.get("allow_decimal", True):
        _make_button(".", _press_decimal, row, col)
        col += 1
    if spec.get("allow_negative", False):
        _make_button("+/-", _toggle_sign, row, col)
        col += 1
    _make_button("Back", _backspace, row, col)
    row += 1
    _make_button("Clear", _clear, row, 0, colspan=2)
    _make_button("Done", _apply_and_close, row, 2)
    row += 1
    _make_button("Cancel", _cancel, row, 0, colspan=3)

    for i in range(3):
        frame.grid_columnconfigure(i, weight=1)

    try:
        entry.focus_set()
        entry.selection_range(0, "end")
        entry.icursor("end")
    except tk.TclError:
        pass
    dlg.protocol("WM_DELETE_WINDOW", _cancel)
    _center_modal(dlg, parent)
    try:
        dlg.update_idletasks()
        dlg.wait_visibility()
    except tk.TclError:
        pass
    try:
        dlg.grab_set()
    except tk.TclError:
        def _retry_grab():
            try:
                dlg.grab_set()
            except tk.TclError:
                pass
        try:
            dlg.after(0, _retry_grab)
        except tk.TclError:
            pass
