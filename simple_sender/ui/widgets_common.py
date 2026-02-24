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
import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _resolve_widget_bg(widget):
    if widget:
        try:
            bg = widget.cget("background")
        except (AttributeError, tk.TclError):
            bg = ""
        if bg:
            return bg
    style = ttk.Style()
    for target in (
        "TFrame",
        "TLabelframe",
        "TButton",
        "TLabel",
        "Entry",
        "TEntry",
        "TCombobox",
        "TLabelframe.Label",
    ):
        cfg = style.configure(target)
        if isinstance(cfg, dict):
            bg = cfg.get("background") or cfg.get("fieldbackground")
            if bg:
                return bg
        else:
            try:
                lookup = style.lookup(target, "background")
            except tk.TclError:
                lookup = ""
            if lookup:
                return lookup
    if widget:
        try:
            root = widget.winfo_toplevel()
            bg = root.cget("background")
            if bg:
                return bg
        except (AttributeError, tk.TclError) as exc:
            _log_suppressed("Failed reading toplevel background while resolving widget bg", exc)
    return "#f0f0f0"


def attach_log_gcode(widget, gcode_or_func):
    try:
        widget._log_gcode_get = gcode_or_func
    except AttributeError as exc:
        _log_suppressed("Failed attaching log G-code provider to widget", exc)


def set_kb_id(widget, kb_id: str):
    try:
        widget._kb_id = kb_id
    except AttributeError as exc:
        _log_suppressed("Failed attaching keyboard-id metadata to widget", exc)
    return widget
