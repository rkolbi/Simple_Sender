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
from typing import Callable

from simple_sender.ui.checklist_files import (
    discover_checklist_files,
    format_checklist_title,
    load_checklist_items,
)
from simple_sender.ui.scrollable_container import build_scrollable_container
from simple_sender.ui.theme_helpers import notebook_page_style_name
from simple_sender.ui.widgets_tooltips import set_tab_tooltip


def _build_checklist_section(
    app,
    parent: ttk.Frame,
    row: int,
    on_layout_change: Callable[[], None] | None = None,
) -> int:
    frame = ttk.LabelFrame(parent, text="Checklists", padding=8)
    frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    frame.grid_columnconfigure(0, weight=1)

    app._checklist_vars = {}
    app._checklist_collapsed = {}
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
        block = ttk.LabelFrame(frame, padding=6)
        block.grid(row=idx, column=0, sticky="ew", pady=(0, 8))
        block.grid_columnconfigure(0, weight=1)
        content = ttk.Frame(block)
        content.grid(row=1, column=0, sticky="ew")
        content.grid_columnconfigure(0, weight=1)
        is_expanded = tk.BooleanVar(master=block, value=True)
        title_btn = ttk.Button(block)
        title_btn.grid(row=0, column=0, sticky="w", pady=(0, 4))
        app._checklist_collapsed[path] = False

        def _update_title(
            _button=title_btn,
            _title=title,
            _expanded_var=is_expanded,
        ) -> None:
            marker = "[-]" if bool(_expanded_var.get()) else "[+]"
            _button.config(text=f"{marker} {_title}")

        def _toggle(
            _content=content,
            _expanded_var=is_expanded,
            _path=path,
            _refresh_title=_update_title,
        ) -> None:
            expanded = bool(_expanded_var.get())
            if expanded:
                _content.grid_remove()
            else:
                _content.grid()
            _expanded_var.set(not expanded)
            app._checklist_collapsed[_path] = expanded
            _refresh_title()
            if callable(on_layout_change):
                on_layout_change()

        title_btn.configure(command=_toggle)
        _update_title()
        items = load_checklist_items(path)
        if items is None:
            ttk.Label(
                content,
                text=f"Unable to read {os.path.basename(path)}.",
                wraplength=620,
                justify="left",
            ).grid(row=0, column=0, sticky="w", pady=2)
            continue
        if not items:
            ttk.Label(
                content,
                text="Checklist file is empty.",
                wraplength=620,
                justify="left",
            ).grid(row=0, column=0, sticky="w", pady=2)
            continue
        vars_for_file = []
        for item_idx, item in enumerate(items):
            var = tk.BooleanVar(value=False)
            check = ttk.Checkbutton(content, text=item, variable=var)
            check.grid(row=item_idx, column=0, sticky="w", pady=2)
            vars_for_file.append(var)
        app._checklist_vars[path] = vars_for_file
    return row + 1


def build_checklists_tab(app, notebook: ttk.Notebook) -> ttk.Frame:
    tab = ttk.Frame(notebook, padding=8, style=notebook_page_style_name())
    notebook.add(tab, text="Checklists")
    set_tab_tooltip(notebook, tab, "Run setup and safety checklists.")
    tab.grid_columnconfigure(0, weight=1)
    tab.grid_rowconfigure(0, weight=1)
    scroll_container = build_scrollable_container(
        tab,
        app=app,
        tk_module=tk,
        ttk_module=ttk,
        bind_mousewheel_support=True,
    )
    inner = scroll_container.content

    inner.grid_columnconfigure(0, weight=1)
    row = 0
    _build_checklist_section(app, inner, row, on_layout_change=scroll_container.update_scrollregion)
    return tab
