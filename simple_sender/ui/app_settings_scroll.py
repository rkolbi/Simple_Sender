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

def update_app_settings_scrollregion(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.configure(scrollregion=app.app_settings_canvas.bbox("all"))

def on_app_settings_mousewheel(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    delta = 0
    if event.delta:
        delta = -int(event.delta / 120)
    elif getattr(event, "num", None) == 4:
        delta = -1
    elif getattr(event, "num", None) == 5:
        delta = 1
    if delta:
        app.app_settings_canvas.yview_scroll(delta, "units")

def bind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.bind_all("<MouseWheel>", app._on_app_settings_mousewheel)
    app.app_settings_canvas.bind_all("<Button-4>", app._on_app_settings_mousewheel)
    app.app_settings_canvas.bind_all("<Button-5>", app._on_app_settings_mousewheel)

def unbind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.unbind_all("<MouseWheel>")
    app.app_settings_canvas.unbind_all("<Button-4>")
    app.app_settings_canvas.unbind_all("<Button-5>")
