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

from .sections import (
    build_auto_level_section,
    build_diagnostics_section,
    build_error_dialogs_section,
    build_estimation_section,
    build_interface_section,
    build_jogging_section,
    build_kasa_plug_section,
    build_keyboard_shortcuts_section,
    build_macros_section,
    build_power_section,
    build_safety_aids_section,
    build_safety_section,
    build_status_polling_section,
    build_theme_section,
    build_viewer_section,
    build_zeroing_section,
)
from simple_sender.ui.widgets_tooltips import set_tab_tooltip


def _build_category_header(
    parent: ttk.Frame,
    row: int,
    title: str,
    description: str,
) -> tuple[int, ttk.Label]:
    title_label = ttk.Label(parent, text=title, font=("TkDefaultFont", 10, "bold"))
    title_label.grid(row=row, column=0, sticky="w", pady=(10, 2))
    desc_label = ttk.Label(parent, text=description, justify="left", wraplength=760)
    desc_label.grid(row=row + 1, column=0, sticky="w", pady=(0, 4))
    ttk.Separator(parent, orient="horizontal").grid(row=row + 2, column=0, sticky="ew", pady=(0, 8))
    return row + 3, title_label


def _update_app_settings_sticky_header(app, *_args) -> None:
    headers = getattr(app, "app_settings_section_headers", None)
    sticky_var = getattr(app, "app_settings_sticky_var", None)
    canvas = getattr(app, "app_settings_canvas", None)
    if not headers or sticky_var is None or canvas is None:
        return

    try:
        visible_top = float(canvas.canvasy(0))
    except Exception:
        return

    positions: list[tuple[str, float]] = []
    for title, header_widget in headers:
        try:
            header_y = float(header_widget.winfo_y())
        except Exception:
            continue
        positions.append((str(title), header_y))

    if not positions:
        active_title = str(headers[0][0])
        try:
            sticky_var.set(active_title)
        except Exception:
            pass
        return

    y_values = [round(y, 3) for _title, y in positions]
    if len(set(y_values)) == 1:
        active_title = str(positions[0][0])
        try:
            sticky_var.set(active_title)
        except Exception:
            pass
        return

    active_title = ""
    for title, header_y in positions:
        if header_y <= visible_top + 1.0:
            active_title = title
        elif not active_title:
            active_title = title
            break
        else:
            break
    if not active_title:
        active_title = str(headers[0][0])
    try:
        sticky_var.set(active_title)
    except Exception:
        pass


def build_app_settings_tab(app, notebook):
    nb = notebook
    # App Settings tab
    sstab = ttk.Frame(nb, padding=8)
    nb.add(sstab, text="App Settings")
    set_tab_tooltip(nb, sstab, "Configure app preferences, UI, and safety settings.")
    sstab.grid_columnconfigure(0, weight=1)
    sstab.grid_rowconfigure(1, weight=1)

    sticky_frame = ttk.Frame(sstab)
    sticky_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    sticky_frame.grid_columnconfigure(0, weight=1)
    app.app_settings_sticky_var = tk.StringVar(value="Interface & Viewer")
    app.app_settings_sticky_label = ttk.Label(
        sticky_frame,
        textvariable=app.app_settings_sticky_var,
        font=("TkDefaultFont", 10, "bold"),
    )
    app.app_settings_sticky_label.grid(row=0, column=0, sticky="w")
    ttk.Separator(sticky_frame, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=(4, 0))

    app.app_settings_canvas = tk.Canvas(sstab, highlightthickness=0)
    app.app_settings_canvas.grid(row=1, column=0, sticky="nsew")
    app.app_settings_scroll = ttk.Scrollbar(
        sstab,
        orient="vertical",
        command=app.app_settings_canvas.yview,
    )
    app.app_settings_scroll.grid(row=1, column=1, sticky="ns")

    def _on_canvas_yview(first: str, last: str) -> None:
        app.app_settings_scroll.set(first, last)
        _update_app_settings_sticky_header(app)

    app.app_settings_canvas.configure(yscrollcommand=_on_canvas_yview)
    app._app_settings_inner = ttk.Frame(app.app_settings_canvas)
    app._app_settings_window = app.app_settings_canvas.create_window(
        (0, 0), window=app._app_settings_inner, anchor="nw"
    )

    def _on_inner_configure(_event=None) -> None:
        app._update_app_settings_scrollregion()
        _update_app_settings_sticky_header(app)

    def _on_canvas_configure(event) -> None:
        app.app_settings_canvas.itemconfig(app._app_settings_window, width=event.width)
        _update_app_settings_sticky_header(app)

    app._app_settings_inner.bind("<Configure>", _on_inner_configure)
    app.app_settings_canvas.bind("<Configure>", _on_canvas_configure)
    app._app_settings_inner.bind("<Enter>", lambda event: app._bind_app_settings_mousewheel())
    app._app_settings_inner.bind("<Leave>", lambda event: app._unbind_app_settings_mousewheel())
    app._app_settings_inner.grid_columnconfigure(0, weight=1)

    version_label = ttk.Label(
        app._app_settings_inner,
        textvariable=app.version_var,
        font=("TkDefaultFont", 10, "bold"),
    )
    version_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

    next_row = 1
    app.app_settings_section_headers = []

    next_row, header = _build_category_header(
        app._app_settings_inner,
        next_row,
        "Interface & Viewer",
        "Startup, UI theme/scale, and viewer behavior.",
    )
    app.app_settings_section_headers.append(("Interface & Viewer", header))
    next_row = build_interface_section(app, app._app_settings_inner, next_row)
    next_row = build_theme_section(app, app._app_settings_inner, next_row)
    next_row = build_viewer_section(app, app._app_settings_inner, next_row)

    next_row, header = _build_category_header(
        app._app_settings_inner,
        next_row,
        "Controls & Inputs",
        "Jogging, zeroing behavior, keyboard/joystick shortcuts, Kasa, and macros.",
    )
    app.app_settings_section_headers.append(("Controls & Inputs", header))
    next_row = build_jogging_section(app, app._app_settings_inner, next_row)
    next_row = build_zeroing_section(app, app._app_settings_inner, next_row)
    next_row = build_keyboard_shortcuts_section(app, app._app_settings_inner, next_row)
    next_row = build_kasa_plug_section(app, app._app_settings_inner, next_row)
    next_row = build_macros_section(app, app._app_settings_inner, next_row)

    next_row, header = _build_category_header(
        app._app_settings_inner,
        next_row,
        "Process & Diagnostics",
        "Time estimation, auto-level presets, and diagnostics tools.",
    )
    app.app_settings_section_headers.append(("Process & Diagnostics", header))
    next_row = build_estimation_section(app, app._app_settings_inner, next_row)
    next_row = build_auto_level_section(app, app._app_settings_inner, next_row)
    next_row = build_diagnostics_section(app, app._app_settings_inner, next_row)

    next_row, header = _build_category_header(
        app._app_settings_inner,
        next_row,
        "Safety & System",
        "Stop behavior, watchdogs, polling, dialogs, and Linux system power actions.",
    )
    app.app_settings_section_headers.append(("Safety & System", header))
    next_row = build_safety_section(app, app._app_settings_inner, next_row)
    next_row = build_safety_aids_section(app, app._app_settings_inner, next_row)
    next_row = build_status_polling_section(app, app._app_settings_inner, next_row)
    next_row = build_error_dialogs_section(app, app._app_settings_inner, next_row)
    build_power_section(app, app._app_settings_inner, next_row)
    after_idle = getattr(app, "after_idle", None)
    if callable(after_idle):
        after_idle(lambda: _update_app_settings_sticky_header(app))
    else:
        _update_app_settings_sticky_header(app)

