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
from typing import Any, Callable

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

_APP_SETTINGS_VIEW_BASIC = "Basic"
_APP_SETTINGS_VIEW_ADVANCED = "Advanced"
_NO_MATCHING_SETTINGS_TEXT = "No matching settings"


def _build_category_header(
    parent: ttk.Frame,
    row: int,
    title: str,
    description: str,
) -> tuple[int, dict[str, Any]]:
    title_label = ttk.Label(parent, text=title, font=("TkDefaultFont", 10, "bold"))
    title_label.grid(row=row, column=0, sticky="w", pady=(10, 2))
    desc_label = ttk.Label(parent, text=description, justify="left", wraplength=760)
    desc_label.grid(row=row + 1, column=0, sticky="w", pady=(0, 4))
    separator = ttk.Separator(parent, orient="horizontal")
    separator.grid(row=row + 2, column=0, sticky="ew", pady=(0, 8))
    return row + 3, {
        "title": title,
        "title_widget": title_label,
        "widgets": (title_label, desc_label, separator),
    }


def _normalize_filter_tokens(value: str) -> list[str]:
    return [token for token in str(value or "").strip().lower().split() if token]


def _build_section_search_text(
    category_title: str,
    section_title: str,
    description: str,
    keywords: tuple[str, ...],
) -> str:
    parts = [category_title, section_title, description]
    parts.extend(keywords)
    return " ".join(part.strip().lower() for part in parts if part and str(part).strip())


def _build_app_settings_section_entry(
    app,
    parent: ttk.Frame,
    row: int,
    category_title: str,
    section_title: str,
    description: str,
    mode: str,
    keywords: tuple[str, ...],
    visibility_predicate: Callable[[Any], bool] | None,
    builder: Callable[[Any, ttk.Frame, int], int],
) -> dict[str, Any]:
    before = tuple(parent.winfo_children())
    next_row = int(builder(app, parent, row))
    after = tuple(parent.winfo_children())
    added_widgets = [widget for widget in after if widget not in before]
    section_widget = None
    for widget in added_widgets:
        grid_info = getattr(widget, "grid_info", None)
        if not callable(grid_info):
            continue
        try:
            grid_row = int(grid_info().get("row", -1))
        except Exception:
            continue
        if grid_row == row:
            section_widget = widget
            break
    available = section_widget is not None and next_row > row
    return {
        "category_title": category_title,
        "title": section_title,
        "description": description,
        "mode": str(mode).strip().lower(),
        "keywords": keywords,
        "search_text": _build_section_search_text(category_title, section_title, description, keywords),
        "widget": section_widget,
        "available": available,
        "visibility_predicate": visibility_predicate,
        "next_row": next_row,
        "visible": False,
    }


def _should_show_section(app, entry: dict[str, Any], mode: str, tokens: list[str]) -> bool:
    if not bool(entry.get("available", False)):
        return False
    predicate = entry.get("visibility_predicate")
    if callable(predicate):
        try:
            if not bool(predicate(app)):
                return False
        except Exception:
            pass
    normalized_mode = str(mode or "").strip().lower()
    section_mode = str(entry.get("mode", "basic")).strip().lower()
    if normalized_mode != _APP_SETTINGS_VIEW_ADVANCED.lower() and section_mode != "basic":
        return False
    if not tokens:
        return True
    haystack = str(entry.get("search_text", ""))
    return all(token in haystack for token in tokens)


def _apply_app_settings_filters(app, *, reset_scroll: bool = False) -> None:
    search_var = getattr(app, "app_settings_search_var", None)
    mode_var = getattr(app, "app_settings_view_mode_var", None)
    sections = getattr(app, "app_settings_section_entries", [])
    categories = getattr(app, "app_settings_category_entries", [])
    empty_label = getattr(app, "app_settings_empty_label", None)
    sticky_var = getattr(app, "app_settings_sticky_var", None)

    search_text = ""
    if search_var is not None:
        try:
            search_text = str(search_var.get() or "")
        except Exception:
            search_text = ""
    tokens = _normalize_filter_tokens(search_text)

    mode = _APP_SETTINGS_VIEW_BASIC
    if mode_var is not None:
        try:
            mode = str(mode_var.get() or _APP_SETTINGS_VIEW_BASIC)
        except Exception:
            mode = _APP_SETTINGS_VIEW_BASIC

    try:
        app.settings["app_settings_view_mode"] = (
            "advanced" if str(mode).strip().lower() == _APP_SETTINGS_VIEW_ADVANCED.lower() else "basic"
        )
    except Exception:
        pass

    visible_section_count = 0
    for entry in sections:
        widget = entry.get("widget")
        visible = _should_show_section(app, entry, mode, tokens)
        entry["visible"] = visible
        if widget is not None:
            try:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
            except Exception:
                pass
        if visible:
            visible_section_count += 1

    visible_headers: list[tuple[str, Any]] = []
    for category in categories:
        category_visible = any(bool(section.get("visible", False)) for section in category.get("sections", ()))
        for widget in category.get("widgets", ()):
            try:
                if category_visible:
                    widget.grid()
                else:
                    widget.grid_remove()
            except Exception:
                continue
        if category_visible:
            visible_headers.append((str(category.get("title", "")), category.get("title_widget")))

    app.app_settings_section_headers = visible_headers

    if empty_label is not None:
        try:
            if visible_section_count == 0:
                empty_label.grid()
            else:
                empty_label.grid_remove()
        except Exception:
            pass

    if sticky_var is not None:
        try:
            if visible_headers:
                sticky_var.set(str(visible_headers[0][0]))
            else:
                sticky_var.set(_NO_MATCHING_SETTINGS_TEXT)
        except Exception:
            pass

    updater = getattr(app, "_update_app_settings_scrollregion", None)
    if callable(updater):
        updater()
    if reset_scroll:
        canvas = getattr(app, "app_settings_canvas", None)
        if canvas is not None:
            try:
                canvas.yview_moveto(0.0)
            except Exception:
                pass
    _update_app_settings_sticky_header(app)


def _on_app_settings_filter_change(app, *_args) -> None:
    _apply_app_settings_filters(app, reset_scroll=True)


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
            if not bool(header_widget.winfo_ismapped()):
                continue
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
    sticky_frame.grid_columnconfigure(1, weight=0)
    app.app_settings_search_var = tk.StringVar(master=sticky_frame, value="")
    initial_mode = str(getattr(app, "settings", {}).get("app_settings_view_mode", "basic")).strip().lower()
    default_mode = (
        _APP_SETTINGS_VIEW_ADVANCED
        if initial_mode == "advanced"
        else _APP_SETTINGS_VIEW_BASIC
    )
    app.app_settings_view_mode_var = tk.StringVar(master=sticky_frame, value=default_mode)
    filter_row = ttk.Frame(sticky_frame)
    filter_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    filter_row.grid_columnconfigure(1, weight=1)
    ttk.Label(filter_row, text="Search").grid(row=0, column=0, sticky="w", padx=(0, 6))
    app.app_settings_search_entry = ttk.Entry(
        filter_row,
        textvariable=app.app_settings_search_var,
    )
    app.app_settings_search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
    ttk.Label(filter_row, text="View").grid(row=0, column=2, sticky="e", padx=(0, 6))
    app.app_settings_view_mode_combo = ttk.Combobox(
        filter_row,
        state="readonly",
        width=12,
        values=(_APP_SETTINGS_VIEW_BASIC, _APP_SETTINGS_VIEW_ADVANCED),
        textvariable=app.app_settings_view_mode_var,
    )
    app.app_settings_view_mode_combo.grid(row=0, column=3, sticky="e")
    app.app_settings_view_mode_combo.bind(
        "<<ComboboxSelected>>",
        lambda _event: _on_app_settings_filter_change(app),
    )
    app.app_settings_search_var.trace_add(
        "write",
        lambda *_args: _on_app_settings_filter_change(app),
    )

    app.app_settings_sticky_var = tk.StringVar(value="Interface & Viewer")
    app.app_settings_sticky_label = ttk.Label(
        sticky_frame,
        textvariable=app.app_settings_sticky_var,
        font=("TkDefaultFont", 10, "bold"),
    )
    app.app_settings_sticky_label.grid(row=1, column=0, sticky="w")
    ttk.Separator(sticky_frame, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=(4, 0))

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
    app.app_settings_empty_label = ttk.Label(
        app._app_settings_inner,
        text=_NO_MATCHING_SETTINGS_TEXT,
        justify="left",
    )
    app.app_settings_empty_label.grid(row=1, column=0, sticky="w", pady=(0, 8))
    app.app_settings_empty_label.grid_remove()

    next_row = 2
    app.app_settings_section_headers = []
    app.app_settings_section_entries = []
    app.app_settings_category_entries = []

    active_category: dict[str, Any] | None = None

    def _start_category(title: str, description: str) -> None:
        nonlocal next_row, active_category
        next_row, category_entry = _build_category_header(
            app._app_settings_inner,
            next_row,
            title,
            description,
        )
        category_entry["sections"] = []
        app.app_settings_category_entries.append(category_entry)
        active_category = category_entry

    def _add_section(
        section_title: str,
        builder: Callable[[Any, ttk.Frame, int], int],
        *,
        mode: str,
        description: str,
        keywords: tuple[str, ...] = (),
        visibility_predicate: Callable[[Any], bool] | None = None,
    ) -> None:
        nonlocal next_row
        if active_category is None:
            return
        entry = _build_app_settings_section_entry(
            app,
            app._app_settings_inner,
            next_row,
            str(active_category.get("title", "")),
            section_title,
            description,
            mode,
            keywords,
            visibility_predicate,
            builder,
        )
        app.app_settings_section_entries.append(entry)
        active_category["sections"].append(entry)
        next_row = int(entry["next_row"])

    _start_category(
        "Interface & Viewer",
        "Startup, UI theme/scale, and viewer behavior.",
    )
    _add_section(
        "Interface",
        build_interface_section,
        mode="basic",
        description="Startup behavior, toolbar visibility, status indicators, and quick button toggles.",
        keywords=("startup", "fullscreen", "toolbar", "quick buttons"),
    )
    _add_section(
        "Theme",
        build_theme_section,
        mode="basic",
        description="Theme, UI scale, scrollbar width, tooltip behavior, and touch keypad preferences.",
        keywords=("appearance", "touch", "ui scale", "tooltips"),
    )
    _add_section(
        "Viewer",
        build_viewer_section,
        mode="basic",
        description="Current-line highlight behavior and 3D streaming refresh.",
        keywords=("gcode viewer", "line highlight", "3d"),
    )

    _start_category(
        "Controls & Inputs",
        "Jogging, zeroing behavior, keyboard/joystick shortcuts, Kasa, and macros.",
    )
    _add_section(
        "Jogging",
        build_jogging_section,
        mode="basic",
        description="Default jog feed rates and quick safe-mode profile.",
        keywords=("feed", "jog", "safe mode"),
    )
    _add_section(
        "Zeroing",
        build_zeroing_section,
        mode="basic",
        description="Choose persistent WCS zeroing versus G92 temporary zeroing.",
        keywords=("zero", "wcs", "g10", "g92"),
    )
    _add_section(
        "Keyboard shortcuts",
        build_keyboard_shortcuts_section,
        mode="advanced",
        description="Keyboard and joystick shortcut bindings, safety-hold behavior, and live input status.",
        keywords=("keyboard", "joystick", "bindings"),
    )
    _add_section(
        "Kasa Plug",
        build_kasa_plug_section,
        mode="advanced",
        description="Kasa accessory discovery, outlet mapping, and manual outlet tests.",
        keywords=("kasa", "accessory", "vacuum", "light"),
    )
    _add_section(
        "Macros",
        build_macros_section,
        mode="advanced",
        description="Macro manager, probe defaults, and macro script timeout/security settings.",
        keywords=("macro", "scripting", "probe"),
    )

    _start_category(
        "Process & Diagnostics",
        "Time estimation, auto-level presets, and diagnostics tools.",
    )
    _add_section(
        "Estimation",
        build_estimation_section,
        mode="advanced",
        description="Fallback rapid rates, estimator adjustment factor, and axis max-rate overrides.",
        keywords=("time estimate", "rapid rate", "max rates"),
    )
    _add_section(
        "Auto-Level",
        build_auto_level_section,
        mode="advanced",
        description="Configure area thresholds and interpolation presets for auto-level probing.",
        keywords=("probe", "interpolation", "spacing"),
        visibility_predicate=lambda app_ref: bool(app_ref.auto_level_enabled.get()),
    )
    _add_section(
        "Diagnostics",
        build_diagnostics_section,
        mode="advanced",
        description="Preflight checks, diagnostic export, backup bundles, and large-file validation.",
        keywords=("preflight", "report", "backup", "validation"),
    )

    _start_category(
        "Safety & System",
        "Stop behavior, watchdogs, polling, dialogs, and Linux system power actions.",
    )
    _add_section(
        "Safety",
        build_safety_section,
        mode="basic",
        description="All Stop behavior and homing watchdog safety controls.",
        keywords=("all stop", "watchdog", "dry run"),
    )
    _add_section(
        "Safety Aids",
        build_safety_aids_section,
        mode="basic",
        description="Training wheels confirmations and reconnect-on-open behavior.",
        keywords=("training wheels", "reconnect"),
    )
    _add_section(
        "Status polling",
        build_status_polling_section,
        mode="advanced",
        description="Adjust status-report interval and disconnect failure thresholds.",
        keywords=("status", "polling", "disconnect"),
    )
    _add_section(
        "Error dialogs",
        build_error_dialogs_section,
        mode="advanced",
        description="Control dialog cadence, burst suppression, and GRBL popup behavior.",
        keywords=("dialogs", "popup", "error"),
    )
    _add_section(
        "System",
        build_power_section,
        mode="advanced",
        description="Linux-only shutdown and reboot actions.",
        keywords=("shutdown", "reboot", "linux"),
    )
    _apply_app_settings_filters(app, reset_scroll=False)
    after_idle = getattr(app, "after_idle", None)
    if callable(after_idle):
        after_idle(lambda: _update_app_settings_sticky_header(app))
    else:
        _update_app_settings_sticky_header(app)

