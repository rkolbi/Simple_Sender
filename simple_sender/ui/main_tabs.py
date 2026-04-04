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

import logging
import time
from tkinter import ttk

from simple_sender.ui.settings import build_app_settings_tab
from simple_sender.ui.checklists_tab import build_checklists_tab
from simple_sender.ui.console import build_console_tab
from simple_sender.ui.log_viewer import LogViewer
from simple_sender.ui.viewer.gcode_viewer import GcodeViewer
from simple_sender.ui.overdrive_tab import build_overdrive_tab
from simple_sender.ui.file_info_tab import build_file_info_tab
from simple_sender.ui.theme_helpers import bind_scrollbar_theme, notebook_page_style_name, register_theme_refresh
from simple_sender.ui.widgets_tooltips import set_tab_tooltip

logger = logging.getLogger(__name__)


def _var_bool(value, default: bool) -> bool:
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return default
    if value is None:
        return default
    return bool(value)


def _managed_notebook_tabs(nb) -> tuple[str, ...]:
    try:
        return tuple(str(tab_id) for tab_id in nb.tabs())
    except Exception:
        return ()


def _notebook_has_tab(nb, tab) -> bool:
    try:
        return str(tab) in _managed_notebook_tabs(nb)
    except Exception:
        return False


def _notebook_tab_state(nb, tab) -> str:
    if tab is None or not _notebook_has_tab(nb, tab):
        return "missing"
    try:
        return str(nb.tab(tab, "state") or "normal").strip().lower() or "normal"
    except Exception:
        return "normal"


def _tab_should_be_visible(app, variable_name: str, default: bool) -> bool:
    return _var_bool(getattr(app, variable_name, None), default)


def _register_optional_tabs(app) -> None:
    raw_tab = getattr(getattr(app, "settings_controller", None), "settings_raw_tab", None)
    app._optional_notebook_tabs = {
        "logs": {
            "tab": getattr(app, "logs_tab", None),
            "label": "Logs",
            "variable_name": "show_logs_tab",
            "default": False,
        },
        "raw_grbl": {
            "tab": raw_tab,
            "label": "Raw $$",
            "variable_name": "show_raw_grbl_tab",
            "default": False,
        },
        "checklists": {
            "tab": getattr(app, "checklists_tab", None),
            "label": "Checklists",
            "variable_name": "show_checklists_tab",
            "default": True,
        },
    }


def sync_optional_tab_visibility(app, nb=None):
    if nb is None:
        nb = getattr(app, "notebook", None)
    if not nb:
        return
    registry = getattr(app, "_optional_notebook_tabs", None)
    if not isinstance(registry, dict) or not registry:
        return
    gcode_tab = getattr(app, "gcode_tab", None)
    selected_tab = None
    try:
        selected_tab = nb.select()
    except Exception:
        selected_tab = None
    for entry in registry.values():
        tab = entry.get("tab")
        if tab is None or not _notebook_has_tab(nb, tab):
            continue
        should_show = _tab_should_be_visible(
            app,
            str(entry.get("variable_name") or ""),
            bool(entry.get("default", False)),
        )
        tab_state = _notebook_tab_state(nb, tab)
        if should_show:
            if tab_state == "hidden":
                try:
                    nb.add(tab)
                except Exception:
                    logger.exception("Failed restoring notebook tab %r", entry.get("label"))
            continue
        if str(selected_tab) == str(tab):
            fallback = None
            if gcode_tab is not None and _notebook_has_tab(nb, gcode_tab):
                gcode_state = _notebook_tab_state(nb, gcode_tab)
                if gcode_state != "hidden" and str(gcode_tab) != str(tab):
                    fallback = gcode_tab
            if fallback is None:
                for candidate in _managed_notebook_tabs(nb):
                    if candidate == str(tab):
                        continue
                    try:
                        if str(nb.tab(candidate, "state") or "normal").strip().lower() == "hidden":
                            continue
                    except Exception:
                        pass
                    fallback = candidate
                    break
            if fallback is not None:
                try:
                    nb.select(fallback)
                    selected_tab = fallback
                except Exception:
                    logger.exception("Failed selecting fallback notebook tab while hiding %r", entry.get("label"))
        if tab_state != "hidden":
            try:
                nb.hide(tab)
            except Exception:
                logger.exception("Failed hiding notebook tab %r", entry.get("label"))
    update_tab_visibility(app, nb)


def update_tab_visibility(app, nb=None):
    """Sync app-level tab state and tab-specific bindings from the active notebook tab."""

    if nb is None:
        nb = getattr(app, "notebook", None)
    if not nb:
        return
    try:
        tab_id = nb.select()
        label = nb.tab(tab_id, "text")
    except Exception as exc:
        logger.exception("Failed resolving active notebook tab while updating tab visibility")
        return
    try:
        app._active_tab_label = str(label)
        app._app_settings_tab_active = (label == "App Settings")
        if app._app_settings_tab_active:
            note_interaction = getattr(app, "_note_app_settings_interaction", None)
            if callable(note_interaction):
                note_interaction()
    except Exception:
        pass
    try:
        app._update_quick_button_visibility()
    except Exception as exc:
        logger.debug(
            "Failed updating quick-button visibility for active tab %r",
            label,
            exc_info=exc,
        )
    streaming_controller = getattr(app, "streaming_controller", None)
    handle_active_tab_changed = getattr(streaming_controller, "handle_active_tab_changed", None)
    if callable(handle_active_tab_changed):
        try:
            handle_active_tab_changed()
        except Exception as exc:
            logger.debug(
                "Failed syncing streaming controller for active tab %r",
                label,
                exc_info=exc,
            )
    try:
        if label == "App Settings":
            app._bind_app_settings_mousewheel()
            app._bind_app_settings_touch_scroll()
            refresh_sticky = getattr(app, "_refresh_app_settings_sticky_header", None)
            if callable(refresh_sticky):
                refresh_sticky(force=True)
            resume_lazy_build = getattr(app, "_resume_app_settings_lazy_build", None)
            if callable(resume_lazy_build):
                resume_lazy_build()
        else:
            suspend_background = getattr(app, "_suspend_app_settings_background_work", None)
            if callable(suspend_background):
                suspend_background()
            app._unbind_app_settings_mousewheel()
            app._unbind_app_settings_touch_scroll()
    except Exception as exc:
        logger.debug(
            "Failed updating App Settings input bindings for active tab %r",
            label,
            exc_info=exc,
        )


def on_tab_changed(app, event):
    update_tab_visibility(app, event.widget)
    if not bool(app.gui_logging_enabled.get()):
        return
    nb = event.widget
    try:
        tab_id = nb.select()
        label = nb.tab(tab_id, "text")
    except Exception:
        return
    if not label:
        return
    ts = time.strftime("%H:%M:%S")
    app.streaming_controller.log(f"[{ts}] Tab: {label}")


def build_gcode_tab(app, notebook):
    nb = notebook
    # Gcode tab
    gtab = ttk.Frame(nb, padding=6, style=notebook_page_style_name())
    nb.add(gtab, text="G-code")
    set_tab_tooltip(nb, gtab, "Live Past/Current/Next G-code window and job stats.")
    app.gcode_tab = gtab
    live_row = ttk.Frame(gtab)
    live_row.pack(fill="x", pady=(0, 4))
    app.gcode_live_header_label = ttk.Label(
        live_row,
        textvariable=app.gcode_live_header_var,
        anchor="w",
        justify="left",
    )
    app.gcode_live_header_label.pack(side="left", fill="x", expand=True)
    stats_row = ttk.Frame(gtab)
    stats_row.pack(fill="x", pady=(0, 6))
    app.gcode_stats_label = ttk.Label(
        stats_row,
        textvariable=app.gcode_stats_var,
        anchor="w",
        justify="left",
    )
    app.gcode_stats_label.pack(side="left", fill="x", expand=True)
    app.gview = GcodeViewer(gtab)
    app.gview.apply_theme_palette(getattr(app, "theme_palette", None))
    bind_scrollbar_theme(app, app.gview.vsb)
    bind_scrollbar_theme(app, app.gview.hsb)
    register_theme_refresh(
        app,
        lambda: getattr(app, "gview", None) and app.gview.apply_theme_palette(getattr(app, "theme_palette", None)),
    )
    app.gview.pack(fill="both", expand=True)


def build_main_tabs(app, parent):
    # Bottom notebook: G-code + Console + Settings + Checklists
    nb = ttk.Notebook(parent)
    app.notebook = nb
    nb.pack(side="top", fill="both", expand=True, pady=(10, 0))
    nb.bind("<<NotebookTabChanged>>", app._on_tab_changed)

    # Gcode tab
    build_gcode_tab(app, nb)

    # File Info tab
    build_file_info_tab(app, nb)

    # Console tab
    build_console_tab(app, nb)

    # Logs tab
    ltab = ttk.Frame(nb, padding=6, style=notebook_page_style_name())
    nb.add(ltab, text="Logs")
    set_tab_tooltip(nb, ltab, "Review streaming and UI log output.")
    app.logs_tab = ltab
    app.logs_viewer = LogViewer(ltab, app)
    app.logs_viewer.pack(fill="both", expand=True)

    otab = ttk.Frame(nb, padding=6, style=notebook_page_style_name())
    nb.add(otab, text="Overdrive")
    set_tab_tooltip(nb, otab, "Adjust feed/spindle overrides and quick controls.")
    build_overdrive_tab(app, otab)
    app.settings_controller.build_tabs(nb)

    # App Settings tab
    build_app_settings_tab(app, nb)

    # Checklists tab
    app.checklists_tab = build_checklists_tab(app, nb)

    _register_optional_tabs(app)
    sync_optional_tab_visibility(app, nb)
