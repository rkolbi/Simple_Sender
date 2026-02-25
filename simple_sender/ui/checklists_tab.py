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

import os
import tkinter as tk
from tkinter import ttk

from simple_sender.ui.settings.sections_advanced import build_safety_aids_section
from simple_sender.ui.settings.sections_general import (
    build_diagnostics_section,
    build_safety_section,
)
from simple_sender.ui.checklist_files import (
    discover_checklist_files,
    format_checklist_title,
    load_checklist_items,
)
from simple_sender.ui.widgets_tooltips import set_tab_tooltip

_WHEEL_DELTA_UNIT = 120


def _is_descendant(widget, ancestor) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "master", None)
    return False


def _build_checklist_section(app, parent: ttk.Frame, row: int) -> int:
    frame = ttk.LabelFrame(parent, text="Checklists", padding=8)
    frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    frame.grid_columnconfigure(0, weight=1)

    app._checklist_vars = {}
    paths = discover_checklist_files(app)
    if not paths:
        ttk.Label(
            frame,
            text="No checklist files found. Add checklist-*.chk to the macros folder.",
            wraplength=640,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=2)
        return row + 1

    for idx, path in enumerate(paths):
        title = format_checklist_title(path)
        block = ttk.LabelFrame(frame, text=title, padding=6)
        block.grid(row=idx, column=0, sticky="ew", pady=(0, 8))
        block.grid_columnconfigure(0, weight=1)
        items = load_checklist_items(path)
        if items is None:
            ttk.Label(
                block,
                text=f"Unable to read {os.path.basename(path)}.",
                wraplength=620,
                justify="left",
            ).grid(row=0, column=0, sticky="w", pady=2)
            continue
        if not items:
            ttk.Label(
                block,
                text="Checklist file is empty.",
                wraplength=620,
                justify="left",
            ).grid(row=0, column=0, sticky="w", pady=2)
            continue
        vars_for_file = []
        for item_idx, item in enumerate(items):
            var = tk.BooleanVar(value=False)
            check = ttk.Checkbutton(block, text=item, variable=var)
            check.grid(row=item_idx, column=0, sticky="w", pady=2)
            vars_for_file.append(var)
        app._checklist_vars[path] = vars_for_file
    return row + 1


def build_checklists_tab(app, notebook: ttk.Notebook) -> ttk.Frame:
    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Checklists")
    set_tab_tooltip(notebook, tab, "Run setup and safety checklists.")
    tab.grid_columnconfigure(0, weight=1)
    tab.grid_rowconfigure(0, weight=1)
    canvas = tk.Canvas(tab, highlightthickness=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    canvas.configure(yscrollcommand=scroll.set)
    inner = ttk.Frame(canvas)
    inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")
    wheel_remainder = 0.0
    scroll_bound = False

    def _update_scrollregion(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _resize_width(event):
        canvas.itemconfig(inner_window, width=event.width)

    def _on_mousewheel(event):
        nonlocal wheel_remainder
        widget = getattr(event, "widget", None)
        if widget is not None and not _is_descendant(widget, inner) and not _is_descendant(widget, canvas):
            return
        delta = 0
        wheel_delta = getattr(event, "delta", 0) or 0
        if wheel_delta:
            wheel_remainder += float(wheel_delta)
            steps = int(wheel_remainder / _WHEEL_DELTA_UNIT)
            wheel_remainder -= float(steps * _WHEEL_DELTA_UNIT)
            delta = -steps
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        if delta:
            canvas.yview_scroll(delta, "units")
            return "break"

    def _bind_scroll():
        nonlocal scroll_bound
        if scroll_bound:
            return
        root = canvas.winfo_toplevel()
        root.bind_all("<MouseWheel>", _on_mousewheel, add="+")
        root.bind_all("<Button-4>", _on_mousewheel, add="+")
        root.bind_all("<Button-5>", _on_mousewheel, add="+")
        scroll_bound = True

    inner.bind("<Configure>", _update_scrollregion)
    canvas.bind("<Configure>", _resize_width)
    _bind_scroll()

    inner.grid_columnconfigure(0, weight=1)
    row = 0
    row = _build_checklist_section(app, inner, row)
    row = build_diagnostics_section(app, inner, row)
    row = build_safety_section(app, inner, row)
    build_safety_aids_section(app, inner, row)
    return tab
