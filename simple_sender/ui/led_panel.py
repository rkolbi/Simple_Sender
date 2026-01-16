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

import tkinter as tk
from tkinter import ttk

from simple_sender.ui.widgets import _resolve_widget_bg


def _bool_from_var(value, default=True) -> bool:
    if value is None:
        return default
    try:
        return bool(value.get())
    except Exception:
        return bool(value)


def build_led_panel(app, parent):
    frame = ttk.Frame(parent)
    frame.pack(side="right", padx=(8, 0))
    app._led_frame = frame
    app._led_indicators = {}
    app._led_indicator_containers = {}
    app._led_containers = []
    app._led_bg = _resolve_widget_bg(parent)
    labels = [
        ("endstop", "Endstops"),
        ("probe", "Probe"),
        ("hold", "Hold"),
    ]
    for key, text in labels:
        container = tk.Frame(frame, bg=app._led_bg)
        canvas = tk.Canvas(
            container,
            width=18,
            height=18,
            highlightthickness=0,
            bd=0,
            bg=app._led_bg,
        )
        canvas.pack(side="left")
        oval = canvas.create_oval(2, 2, 16, 16, fill="#b0b0b0", outline="#555")
        ttk.Label(container, text=text).pack(side="left", padx=(4, 0))
        app._led_indicators[key] = (canvas, oval)
        app._led_indicator_containers[key] = container
        app._led_containers.append(container)
    app._led_states = {key: False for key in app._led_indicators}
    app._update_led_panel(False, False, False)
    update_led_visibility(app)


def set_led_state(app, key, on):
    entry = app._led_indicators.get(key)
    if not entry:
        return
    canvas, oval = entry
    color = "#00c853" if on else "#b0b0b0"
    canvas.itemconfig(oval, fill=color)
    app._led_states[key] = on


def update_led_panel(app, endstop: bool, probe: bool, hold: bool):
    set_led_state(app, "endstop", endstop)
    set_led_state(app, "probe", probe)
    set_led_state(app, "hold", hold)


def refresh_led_backgrounds(app):
    bg = _resolve_widget_bg(app)
    app._led_bg = bg
    for canvas, _ in getattr(app, "_led_indicators", {}).values():
        try:
            canvas.config(bg=bg)
        except Exception:
            pass
    for container in getattr(app, "_led_indicator_containers", {}).values():
        try:
            container.config(bg=bg)
        except Exception:
            pass


def update_led_visibility(app):
    containers = getattr(app, "_led_indicator_containers", {})
    if not containers:
        return
    visibility = {
        "endstop": _bool_from_var(getattr(app, "show_endstop_indicator", None), True),
        "probe": _bool_from_var(getattr(app, "show_probe_indicator", None), True),
        "hold": _bool_from_var(getattr(app, "show_hold_indicator", None), True),
    }
    order = ("endstop", "probe", "hold")
    for key in order:
        container = containers.get(key)
        if not container:
            continue
        container.pack_forget()
        if visibility.get(key, True):
            container.pack(side="left", padx=(0, 8))


def on_led_visibility_change(app):
    app.settings["show_endstop_indicator"] = bool(app.show_endstop_indicator.get())
    app.settings["show_probe_indicator"] = bool(app.show_probe_indicator.get())
    app.settings["show_hold_indicator"] = bool(app.show_hold_indicator.get())
    update_led_visibility(app)
