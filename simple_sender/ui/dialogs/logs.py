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

from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.log_viewer import LogViewer

logger = logging.getLogger(__name__)


def _resolve_logs_parent(app):
    popup_windows = getattr(app, "_lower_popup_windows", None)
    if isinstance(popup_windows, dict):
        popup = popup_windows.get("app_settings")
        if popup is not None:
            try:
                if bool(popup.winfo_exists()) and bool(popup.winfo_viewable()):
                    return popup
            except Exception as exc:
                logger.debug("Failed checking App Settings popup ownership for logs dialog: %s", exc, exc_info=exc)
    return app


def show_logs_dialog(app) -> None:
    parent = _resolve_logs_parent(app)
    existing = getattr(app, "_logs_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                try:
                    existing.transient(parent)
                except Exception as exc:
                    logger.debug("Failed updating logs window transient parent: %s", exc, exc_info=exc)
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            logger.debug("Failed reusing existing logs window: %s", exc, exc_info=exc)
            app._logs_window = None

    win = tk.Toplevel(app)
    app._logs_window = win
    win.title("Application Logs")
    win.minsize(760, 480)
    win.transient(parent)

    def _on_close():
        app._logs_window = None
        win.destroy()

    viewer = LogViewer(win, app, include_close=True, close_callback=_on_close)
    viewer.pack(fill="both", expand=True)

    win.protocol("WM_DELETE_WINDOW", _on_close)
    center_window(win, parent)
    try:
        win.lift()
        win.focus_force()
    except Exception as exc:
        logger.debug("Failed focusing logs window after creation: %s", exc, exc_info=exc)
