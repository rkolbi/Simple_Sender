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

def on_resume_button_visibility_change(app):
    app.settings["show_resume_from_button"] = bool(app.show_resume_from_button.get())
    update_resume_button_visibility(app)


def on_recover_button_visibility_change(app):
    app.settings["show_recover_button"] = bool(app.show_recover_button.get())
    update_recover_button_visibility(app)


def update_resume_button_visibility(app):
    if not hasattr(app, "btn_resume_from"):
        return
    visible = bool(app.show_resume_from_button.get())
    if visible:
        if not app.btn_resume_from.winfo_ismapped():
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            before_widget = getattr(app, "btn_unlock_top", None)
            if before_widget and before_widget.winfo_exists():
                pack_kwargs["before"] = before_widget
            app.btn_resume_from.pack(**pack_kwargs)
    else:
        app.btn_resume_from.pack_forget()


def update_recover_button_visibility(app):
    if not hasattr(app, "btn_alarm_recover"):
        return
    visible = bool(app.show_recover_button.get())
    if visible:
        if not app.btn_alarm_recover.winfo_ismapped():
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            separator = getattr(app, "_recover_separator", None)
            if separator and separator.winfo_exists():
                pack_kwargs["before"] = separator
            app.btn_alarm_recover.pack(**pack_kwargs)
    else:
        app.btn_alarm_recover.pack_forget()
